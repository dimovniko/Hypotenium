"""Клиент к API моделей.

Поддерживаются два провайдера (поле `provider` в слоте config/models.yaml):
- `openai` (по умолчанию) — любой OpenAI-совместимый API с ключом (polza.ai и др.);
- `gigachat` — прямое подключение к GigaChat API Сбера: своя OAuth-авторизация
  (Basic-ключ → access_token на 30 минут, обновляется автоматически) и опция
  `verify_ssl: false` для их сертификатов НУЦ Минцифры.

Конфигурация читается из config/models.yaml на каждый вызов — модели
переключаются на лету без перезапуска сервиса.

Все вызовы устойчивы к rate-limit (429) и временным сбоям (5xx):
повторяются с экспоненциальной задержкой, уважая заголовок Retry-After.
"""

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import AsyncIterator

import httpx

from .config import models_config


class LLMError(Exception):
    pass


RETRYABLE_STATUSES = {429, 500, 502, 503, 529}
MAX_ATTEMPTS = 6
BASE_DELAY = 3.0   # секунды; растет: 3 → 6 → 12 → 24 → 48
MAX_DELAY = 60.0
EMBED_BATCH_SIZE = 32
EMBED_BATCH_PAUSE = 0.3  # пауза между пачками, чтобы не упираться в лимит запросов

GIGACHAT_OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"

# Кэш access_token'ов GigaChat: {auth_key+scope: (token, expires_at_unix)}
_gigachat_tokens: dict[str, tuple[str, float]] = {}


def _provider(slot: dict) -> str:
    return (slot.get("provider") or "openai").lower()


def _verify(slot: dict) -> bool:
    return bool(slot.get("verify_ssl", True))


async def _gigachat_token(slot: dict, force: bool = False) -> str:
    """Получает (и кэширует) access_token GigaChat. Токен живет 30 минут."""
    auth_key = slot.get("auth_key") or ""
    if not auth_key:
        raise LLMError("GigaChat: не задан auth_key в config/models.yaml")
    scope = slot.get("scope") or "GIGACHAT_API_PERS"
    cache_key = f"{auth_key[:16]}:{scope}"

    if not force:
        cached = _gigachat_tokens.get(cache_key)
        if cached and cached[1] - time.time() > 60:
            return cached[0]

    try:
        async with httpx.AsyncClient(timeout=30, verify=_verify(slot)) as client:
            resp = await client.post(
                slot.get("oauth_url") or GIGACHAT_OAUTH_URL,
                headers={
                    "Authorization": f"Basic {auth_key}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    "RqUID": str(uuid.uuid4()),
                },
                data={"scope": scope},
            )
    except httpx.HTTPError as e:
        raise LLMError(f"GigaChat OAuth: сетевая ошибка: {e}")
    if resp.status_code != 200:
        raise LLMError(f"GigaChat OAuth {resp.status_code}: {resp.text[:200]}")

    payload = resp.json()
    token = payload.get("access_token") or ""
    if not token:
        raise LLMError("GigaChat OAuth: в ответе нет access_token")
    expires_at = float(payload.get("expires_at") or 0) / 1000.0  # приходит в миллисекундах
    if expires_at <= time.time():
        expires_at = time.time() + 25 * 60
    _gigachat_tokens[cache_key] = (token, expires_at)
    return token


async def _slot_headers(slot: dict, force_auth_refresh: bool = False) -> dict:
    if _provider(slot) == "gigachat":
        return {"Authorization": f"Bearer {await _gigachat_token(slot, force=force_auth_refresh)}"}
    headers = {}
    if slot.get("api_key"):
        headers["Authorization"] = f"Bearer {slot['api_key']}"
    return headers


def _retry_wait(resp: httpx.Response | None, delay: float) -> float:
    wait = delay
    if resp is not None:
        retry_after = resp.headers.get("retry-after")
        if retry_after:
            try:
                wait = max(wait, float(retry_after))
            except ValueError:
                pass
    return min(wait, MAX_DELAY)


async def _post_with_retry(
    url: str,
    slot: dict,
    *,
    json_body: dict | None = None,
    data: dict | None = None,
    files: dict | None = None,
    timeout: float = 180,
) -> httpx.Response:
    delay = BASE_DELAY
    last_error = ""
    force_refresh = False
    for attempt in range(MAX_ATTEMPTS):
        resp: httpx.Response | None = None
        headers = await _slot_headers(slot, force_auth_refresh=force_refresh)
        force_refresh = False
        try:
            async with httpx.AsyncClient(timeout=timeout, verify=_verify(slot)) as client:
                resp = await client.post(url, headers=headers, json=json_body, data=data, files=files)
        except httpx.HTTPError as e:
            last_error = f"сетевая ошибка: {e}"
        if resp is not None:
            if resp.status_code == 200:
                return resp
            # У GigaChat токен живет 30 минут — на 401 обновляем и повторяем
            if resp.status_code == 401 and _provider(slot) == "gigachat" and attempt < MAX_ATTEMPTS - 1:
                force_refresh = True
                last_error = "API 401: токен истек, обновляю"
                continue
            if resp.status_code not in RETRYABLE_STATUSES:
                raise LLMError(f"API {resp.status_code}: {resp.text[:300]}")
            last_error = f"API {resp.status_code}: {resp.text[:200]}"
        if attempt < MAX_ATTEMPTS - 1:
            await asyncio.sleep(_retry_wait(resp, delay))
            delay = min(delay * 2, MAX_DELAY)
    raise LLMError(f"Не удалось получить ответ после {MAX_ATTEMPTS} попыток. {last_error}")


async def chat_completion(messages: list[dict], temperature: float = 0.2) -> str:
    """Одиночный (нестриминговый) вызов chat-модели."""
    slot = models_config.slot("chat")
    resp = await _post_with_retry(
        f"{slot['base_url'].rstrip('/')}/chat/completions",
        slot,
        json_body={"model": slot["model"], "messages": messages, "temperature": temperature},
    )
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError):
        raise LLMError(f"Неожиданный ответ chat API: {json.dumps(data)[:300]}")


async def chat_completion_stream(
    messages: list[dict], temperature: float = 0.2
) -> AsyncIterator[str]:
    """Стриминговый вызов chat-модели: отдает дельты текста.

    Ретраи выполняются только до первого полученного токена — начатый ответ не перезапускаем.
    """
    slot = models_config.slot("chat")
    url = f"{slot['base_url'].rstrip('/')}/chat/completions"
    body = {"model": slot["model"], "messages": messages, "temperature": temperature, "stream": True}
    delay = BASE_DELAY
    force_refresh = False
    for attempt in range(MAX_ATTEMPTS):
        started = False
        retry_wait = delay
        headers = await _slot_headers(slot, force_auth_refresh=force_refresh)
        force_refresh = False
        try:
            async with httpx.AsyncClient(timeout=300, verify=_verify(slot)) as client:
                async with client.stream("POST", url, headers=headers, json=body) as resp:
                    if resp.status_code != 200:
                        raw = await resp.aread()
                        if resp.status_code == 401 and _provider(slot) == "gigachat" and attempt < MAX_ATTEMPTS - 1:
                            force_refresh = True
                            retry_wait = 0.5
                        elif resp.status_code in RETRYABLE_STATUSES and attempt < MAX_ATTEMPTS - 1:
                            retry_wait = _retry_wait(resp, delay)
                        else:
                            raise LLMError(
                                f"chat API {resp.status_code}: {raw.decode(errors='replace')[:300]}"
                            )
                    else:
                        async for line in resp.aiter_lines():
                            line = line.strip()
                            if not line.startswith("data:"):
                                continue
                            payload = line[len("data:"):].strip()
                            if payload == "[DONE]":
                                return
                            try:
                                delta = json.loads(payload)["choices"][0]["delta"].get("content")
                            except (json.JSONDecodeError, KeyError, IndexError):
                                continue
                            if delta:
                                started = True
                                yield delta
                        return
        except httpx.HTTPError as e:
            if started or attempt >= MAX_ATTEMPTS - 1:
                raise LLMError(f"chat API: сетевая ошибка: {e}")
        await asyncio.sleep(retry_wait)
        delay = min(delay * 2, MAX_DELAY)
    raise LLMError("chat API: не удалось начать стриминг")


async def embed_texts(texts: list[str]) -> tuple[list[list[float]], str]:
    """Эмбеддинги пачки текстов. Возвращает (векторы, имя модели)."""
    slot = models_config.slot("embedding")
    url = f"{slot['base_url'].rstrip('/')}/embeddings"
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        if i > 0:
            await asyncio.sleep(EMBED_BATCH_PAUSE)
        batch = texts[i : i + EMBED_BATCH_SIZE]
        resp = await _post_with_retry(
            url, slot, json_body={"model": slot["model"], "input": batch}
        )
        data = resp.json()["data"]
        data.sort(key=lambda d: d.get("index", 0))
        vectors.extend(d["embedding"] for d in data)
    return vectors, slot["model"]


async def transcribe_file(path: str | Path) -> dict:
    """Транскрибация аудиофайла.

    Возвращает {"language": str, "segments": [{"text", "t_start", "t_end"}], "text": str}.
    Просит verbose_json ради таймкодов; если провайдер вернул только текст —
    таймкоды сегментов остаются пустыми (обрабатывается вызывающей стороной).
    """
    slot = models_config.slot("transcription")
    path = Path(path)
    audio_bytes = path.read_bytes()  # в память, чтобы повторы не перечитывали файл
    resp = await _post_with_retry(
        f"{slot['base_url'].rstrip('/')}/audio/transcriptions",
        slot,
        data={"model": slot["model"], "response_format": "verbose_json"},
        files={"file": (path.name, audio_bytes, "audio/mpeg")},
        timeout=1800,
    )
    try:
        payload = resp.json()
    except json.JSONDecodeError:
        # Провайдер вернул plain text
        return {"language": "", "segments": [], "text": resp.text}

    segments = [
        {
            "text": (s.get("text") or "").strip(),
            "t_start": float(s.get("start", 0.0)),
            "t_end": float(s.get("end", 0.0)),
        }
        for s in payload.get("segments") or []
        if (s.get("text") or "").strip()
    ]
    return {
        "language": payload.get("language") or "",
        "segments": segments,
        "text": payload.get("text") or "",
    }
