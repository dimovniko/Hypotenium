"""Локальный сервис транскрибации на GigaAM v3 (Сбер).

Мимикрирует под OpenAI API (POST /v1/audio/transcriptions, verbose_json),
чтобы подключаться к основному сервису простой сменой base_url в models.yaml.

Пайплайн: ffmpeg → 16kHz mono wav → Silero VAD (границы речи) →
окна ≤ 22 c (лимит GigaAM ~25 c) → GigaAM v3 e2e (пунктуация + нормализация
чисел) → сегменты с таймкодами по границам VAD-окон.
"""

import os
import subprocess
import tempfile
import threading

from fastapi import FastAPI, File, Form, UploadFile

MODEL_NAME = os.environ.get("GIGAAM_MODEL", "v3_e2e_rnnt")
MAX_WINDOW_SEC = 22.0   # лимит .transcribe у GigaAM — ~25 секунд
JOIN_GAP_SEC = 1.0      # VAD-регионы ближе этого сливаются в одно окно

app = FastAPI(title="GigaAM transcriber")

_asr = None
_vad = None
_get_speech_timestamps = None
_read_audio = None
# Одна транскрибация за раз: CPU-bound, параллельные запросы деградируют все сразу
_lock = threading.Lock()


@app.on_event("startup")
def _load_models() -> None:
    global _asr, _vad, _get_speech_timestamps, _read_audio
    import gigaam
    from silero_vad import get_speech_timestamps, load_silero_vad, read_audio

    _asr = gigaam.load_model(MODEL_NAME)
    _vad = load_silero_vad()
    _get_speech_timestamps = get_speech_timestamps
    _read_audio = read_audio


@app.get("/health")
def health() -> dict:
    return {"ok": _asr is not None, "model": MODEL_NAME}


def _to_wav16k(src: str, dst: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-ac", "1", "-ar", "16000", dst],
        check=True,
    )


def _build_windows(stamps: list[dict]) -> list[tuple[float, float]]:
    """Склеивает VAD-регионы речи в окна ≤ MAX_WINDOW_SEC."""
    windows: list[tuple[float, float]] = []
    cur_start: float | None = None
    cur_end = 0.0
    for region in stamps:
        r_start, r_end = float(region["start"]), float(region["end"])
        # Слишком длинный одиночный регион — жесткая нарезка
        while r_end - r_start > MAX_WINDOW_SEC:
            if cur_start is not None:
                windows.append((cur_start, cur_end))
                cur_start = None
            windows.append((r_start, r_start + MAX_WINDOW_SEC))
            r_start += MAX_WINDOW_SEC
        if cur_start is None:
            cur_start, cur_end = r_start, r_end
        elif r_end - cur_start <= MAX_WINDOW_SEC and r_start - cur_end <= JOIN_GAP_SEC:
            cur_end = r_end
        else:
            windows.append((cur_start, cur_end))
            cur_start, cur_end = r_start, r_end
    if cur_start is not None:
        windows.append((cur_start, cur_end))
    return windows


def _transcribe_piece(path: str) -> str:
    result = _asr.transcribe(path)
    if isinstance(result, dict):
        result = result.get("transcription") or result.get("text") or ""
    return str(result or "").strip()


@app.post("/v1/audio/transcriptions")
def transcribe(
    file: UploadFile = File(...),
    model: str = Form(""),
    response_format: str = Form("verbose_json"),
) -> dict:
    with _lock, tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, os.path.basename(file.filename or "audio"))
        with open(src, "wb") as f:
            f.write(file.file.read())
        wav = os.path.join(td, "audio16k.wav")
        _to_wav16k(src, wav)

        audio = _read_audio(wav)
        duration = round(len(audio) / 16000.0, 2)
        stamps = _get_speech_timestamps(audio, _vad, return_seconds=True)
        windows = _build_windows(stamps)

        segments = []
        piece = os.path.join(td, "piece.wav")
        for w_start, w_end in windows:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(w_start), "-to", str(w_end), "-i", wav, piece],
                check=True,
            )
            try:
                text = _transcribe_piece(piece)
            except Exception:
                continue
            if text:
                segments.append({"start": round(w_start, 2), "end": round(w_end, 2), "text": text})

        return {
            "language": "ru",
            "duration": duration,
            "text": " ".join(s["text"] for s in segments),
            "segments": segments,
        }
