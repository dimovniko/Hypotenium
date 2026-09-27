"""RAG-пайплайн: переформулировка запроса, векторный поиск, сборка промпта, цитаты."""

import json
import re
from datetime import date

from sqlalchemy import case, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import llm
from .config import models_config
from .models import Chunk, File, Message, Study

SYSTEM_PROMPT = """Ты — ассистент-ресерчер. Ты отвечаешь на вопросы СТРОГО на основе \
приложенных источников из базы исследований компании.

Жесткие правила:
1. Каждый содержательный тезис ответа обязан заканчиваться ссылкой на источник в виде [N], \
где N — номер источника из списка. Можно ссылаться на несколько: [1][3].
2. Используй ТОЛЬКО номера из списка источников. Не выдумывай номера.
3. Если источники не содержат ответа на вопрос — прямо напиши, что в базе исследований \
по этому вопросу ничего не найдено, и НЕ ставь ссылки [N] вовсе. \
НИКОГДА не отвечай из общих знаний и ничего не выдумывай.
4. Если один и тот же вывод подтверждается и отчетом, и сырыми данными одного исследования — \
сформулируй его ОДНИМ тезисом с несколькими ссылками [N][M], а не двумя абзацами про одно и то же.
5. Отвечай на языке вопроса пользователя (обычно русский). Пиши понятно и по делу.
6. Форматирование: короткие абзацы, списки через дефис или нумерацией. \
Можно выделять ключевое **жирным** и использовать короткие подзаголовки. \
Таблицы и другую сложную разметку не используй.
7. Все числа, проценты, суммы и даты переноси ДОСЛОВНО так, как они записаны в источнике: \
не округляй, не пересчитывай и не восстанавливай по памяти. Учитывай, что пробел внутри числа — \
разделитель разрядов («1 220» — это 1220). Если нужного числа в источниках нет — прямо скажи, \
что его нет, вместо того чтобы предполагать.
8. Кавычки используй ТОЛЬКО для дословных фраз, которые буква в букву есть в источнике. \
Пересказ и обобщение — всегда без кавычек. Никогда не выдавай пересказ за цитату.
9. Ставь номер именно того источника, из которого взят конкретный факт или фраза. \
Не переноси все факты на один «главный» источник: каждый тезис — со своим номером.

Сегодняшняя дата: {today}."""

REWRITE_PROMPT = """Ты помогаешь поисковой системе по базе исследований. \
По истории диалога и последнему вопросу пользователя сформулируй самостоятельный поисковый запрос \
(на языке вопроса), а также извлеки период дат, если пользователь его явно упомянул.

Ответь ТОЛЬКО валидным JSON без пояснений:
{"query": "<поисковый запрос>", "date_from": "YYYY-MM-DD" | null, "date_to": "YYYY-MM-DD" | null}

Сегодняшняя дата: %s."""

NO_RESULTS_ANSWER = (
    "В базе исследований я не нашел ничего релевантного по этому вопросу. "
    "Попробуйте переформулировать вопрос или уточнить, о каком продукте/периоде идет речь."
)

# Модель иногда пишет «нет информации», но все равно ставит ссылки — определяем
# «пустой» ответ по тексту, а не только по наличию маркеров цитат
_NO_INFO_RE = re.compile(
    r"(?:нет|не\s+содерж\w+|отсутств\w+)\s+(?:[\wё-]+[\s,]+){0,4}(?:информаци|данн|сведени|ответ)"
    r"|ничего не найдено|не нашел|не нашлось|невозможно ответить|не удалось найти",
    re.IGNORECASE,
)


def looks_like_no_info(answer: str) -> bool:
    """Похож ли ответ на «в базе ничего нет» (по первым фразам)."""
    return bool(_NO_INFO_RE.search(answer[:400]))

KIND_LABELS = {
    "report_pdf": "отчет",
    "raw_csv": "сырые данные",
    "audio": "аудиозапись интервью",
    "video": "видеозапись интервью",
}


_MARKER_STRIP_RE = re.compile(r"\s*\[\d{1,3}\]")


def _history_to_messages(history: list[Message], limit: int) -> list[dict]:
    msgs = []
    for m in history[-limit:]:
        content = m.content
        if m.role == "assistant":
            # Убираем маркеры цитат из истории: иначе модель подражает старым
            # номерам чата вместо номеров текущего списка источников
            content = _MARKER_STRIP_RE.sub("", content)
        msgs.append({"role": m.role, "content": content[:2000]})
    return msgs


async def rewrite_query(history: list[Message], question: str) -> dict:
    """Переформулирует вопрос в самостоятельный поисковый запрос + мягкий фильтр дат."""
    limit = models_config.rag()["history_messages"]
    messages = [
        {"role": "system", "content": REWRITE_PROMPT % date.today().isoformat()},
        *_history_to_messages(history, limit),
        {"role": "user", "content": question},
    ]
    fallback = {"query": question, "date_from": None, "date_to": None}
    try:
        raw = await llm.chat_completion(messages, temperature=0.0)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return fallback
        parsed = json.loads(match.group(0))
        query = (parsed.get("query") or "").strip() or question
        result = {"query": query, "date_from": None, "date_to": None}
        for key in ("date_from", "date_to"):
            value = parsed.get(key)
            if value:
                try:
                    result[key] = date.fromisoformat(str(value)[:10])
                except ValueError:
                    pass
        return result
    except Exception:
        return fallback


async def search_chunks(
    session: AsyncSession,
    query_vector: list[float],
    embedding_model: str,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict]:
    """Векторный поиск. Даты — мягкий фильтр: штраф к дистанции, не отсечение."""
    rag = models_config.rag()
    distance = Chunk.embedding.cosine_distance(query_vector).label("distance")

    if date_from or date_to:
        in_range = Study.conducted_at.between(
            date_from or date(1970, 1, 1), date_to or date(2100, 1, 1)
        )
        penalty = case((in_range, 0.0), else_=0.15)
    else:
        penalty = literal(0.0)

    stmt = (
        select(Chunk, Study, File, distance)
        .join(Study, Chunk.study_id == Study.id)
        .join(File, Chunk.file_id == File.id)
        .where(Chunk.embedding_model == embedding_model, Chunk.embedding.is_not(None))
        .order_by(distance + penalty)
        .limit(rag["top_k"])
    )
    rows = (await session.execute(stmt)).all()

    min_similarity = rag["min_similarity"]
    sources = []
    for chunk, study, file, dist in rows:
        similarity = 1.0 - float(dist)
        if similarity < min_similarity:
            continue
        sources.append(
            {
                "chunk_id": chunk.id,
                "content": chunk.content,
                "locator": chunk.locator or {},
                "study_id": study.id,
                "study_title": study.title,
                "conducted_at": study.conducted_at,
                "file_id": file.id,
                "file_name": file.original_name,
                "file_kind": file.kind,
                "file_comment": file.comment,
                "similarity": similarity,
            }
        )
    # Группируем по исследованию, чтобы модели было проще склеивать дубли (правило 4)
    sources.sort(key=lambda s: (str(s["study_id"]), str(s["file_id"]), -s["similarity"]))
    return sources


def _locator_label(kind: str, locator: dict) -> str:
    if kind == "report_pdf" and "page" in locator:
        return f"страница {locator['page']}"
    if kind == "raw_csv" and "row_from" in locator:
        return f"строки {locator['row_from']}–{locator['row_to']}"
    if "t_start" in locator:
        def fmt(t):
            t = int(t)
            return f"{t // 60:02d}:{t % 60:02d}"
        return f"таймкод {fmt(locator['t_start'])}–{fmt(locator.get('t_end', locator['t_start']))}"
    return ""


def build_sources_block(sources: list[dict]) -> str:
    lines = []
    for i, s in enumerate(sources, start=1):
        kind = KIND_LABELS.get(s["file_kind"], s["file_kind"])
        loc = _locator_label(s["file_kind"], s["locator"])
        loc_part = f", {loc}" if loc else ""
        comment_part = f" (пометка к файлу: {s['file_comment']})" if s.get("file_comment") else ""
        lines.append(
            f"[{i}] Исследование «{s['study_title']}» "
            f"(дата проведения: {s['conducted_at'].isoformat()}) — "
            f"{kind}, файл {s['file_name']}{comment_part}{loc_part}:\n{s['content']}"
        )
    return "\n\n".join(lines)


def build_answer_messages(
    history: list[Message], question: str, sources: list[dict]
) -> list[dict]:
    limit = models_config.rag()["history_messages"]
    user_content = (
        f"ИСТОЧНИКИ:\n\n{build_sources_block(sources)}\n\n---\n\nВОПРОС: {question}"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(today=date.today().isoformat())},
        *_history_to_messages(history, limit),
        {"role": "user", "content": user_content},
    ]


class CitationRenumberer:
    """Переводит номера источников в сквозную нумерацию чата — на лету, по мере стриминга.

    Модель ссылается на позицию в переданном списке источников (может быть [14] из 16),
    а пользователь должен видеть подряд идущие номера: первый ответ — [1], [2],
    следующий продолжает с [3]. Маркер, разорванный между дельтами стрима, придерживается
    в буфере до получения закрывающей скобки.
    """

    MARKER_RE = re.compile(r"\[(\d{1,3})\]")
    # Модель иногда эхом выдает шаблон из промпта — литеральный «[N]» вместо номера
    PLACEHOLDER_RE = re.compile(r"\s?\[[Nn]{1,3}\]")

    def __init__(self, sources_count: int, start_from: int = 1):
        self.sources_count = sources_count
        self.next_number = start_from
        self.mapping: dict[int, int] = {}  # номер источника → номер в чате
        self._buffer = ""

    def feed(self, chunk: str) -> str:
        self._buffer += chunk
        out: list[str] = []
        while True:
            m = self.MARKER_RE.search(self._buffer)
            if not m:
                break
            out.append(self._buffer[: m.start()])
            out.append(self._renumber(int(m.group(1))))
            self._buffer = self._buffer[m.end() :]
        hold = self._hold_from()
        out.append(self._buffer[:hold])
        self._buffer = self._buffer[hold:]
        return self.PLACEHOLDER_RE.sub("", "".join(out))

    def flush(self) -> str:
        rest, self._buffer = self._buffer, ""
        return self.PLACEHOLDER_RE.sub("", rest)

    def _hold_from(self) -> int:
        """Индекс, с которого хвост похож на незакрытый маркер («…текст [1» или «…[N»)."""
        i = self._buffer.rfind("[")
        if i >= 0 and len(self._buffer) - i <= 4:
            tail = self._buffer[i + 1 :]
            if tail == "" or tail.isdigit() or all(c in "Nn" for c in tail):
                return i
        return len(self._buffer)

    def _renumber(self, n: int) -> str:
        if not 1 <= n <= self.sources_count:
            return f"[{n}]"  # модель выдумала номер — оставляем как есть, цитаты не будет
        if n not in self.mapping:
            self.mapping[n] = self.next_number
            self.next_number += 1
        return f"[{self.mapping[n]}]"


async def generate_chat_title(question: str) -> str:
    try:
        title = await llm.chat_completion(
            [
                {
                    "role": "system",
                    "content": "Придумай короткое название чата (3–6 слов) по вопросу пользователя. "
                    "Ответь только названием, без кавычек и точки в конце.",
                },
                {"role": "user", "content": question[:1000]},
            ],
            temperature=0.3,
        )
        title = title.strip().strip('"«»').strip()
        return title[:120] or "Новый чат"
    except Exception:
        return "Новый чат"
