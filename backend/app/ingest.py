"""Обработка файлов исследования: извлечение текста, чанкинг, эмбеддинги."""

import asyncio
import csv
import io
import re
import tempfile
from pathlib import Path

from pypdf import PdfReader

from . import llm

CHUNK_MAX_CHARS = 1200
CHUNK_OVERLAP_CHARS = 150
CSV_ROWS_PER_CHUNK = 25
CSV_CHUNK_MAX_CHARS = 1800
MEDIA_SEGMENT_SECONDS = 600  # нарезка длинных записей для API транскрибации
TRANSCRIPT_CHUNK_MAX_CHARS = 900


class IngestError(Exception):
    """Понятная пользователю ошибка обработки."""


def split_text(text: str, max_chars: int = CHUNK_MAX_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    """Режет текст на чанки по границам абзацев/предложений с небольшим перекрытием."""
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r"(?<=[.!?…])\s+|\n{2,}", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(current) + len(sentence) + 1 > max_chars and current:
            chunks.append(current)
            current = current[-overlap:] if overlap else ""
        current = (current + " " + sentence).strip()
        # Очень длинное «предложение» — режем жестко
        while len(current) > max_chars:
            chunks.append(current[:max_chars])
            current = current[max_chars - overlap :]
    if current.strip():
        chunks.append(current.strip())
    return chunks


# ---------- Фильтрация мусора ----------

def is_garbage_chunk(text: str) -> bool:
    """Отсекает бессодержательные чанки.

    Ловит два случая: (1) низкоэнтропийные повторы — например, извлеченная из PDF
    сетка гиперссылок «li n k li n k …»; (2) чанки почти без букв/цифр.
    """
    words = text.split()
    if len(words) >= 25 and len(set(words)) / len(words) < 0.15:
        return True
    alnum = sum(ch.isalnum() for ch in text)
    return alnum < 30


def _norm_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip().lower()


def strip_boilerplate_lines(page_texts: list[str]) -> list[str]:
    """Убирает колонтитулы — короткие строки, повторяющиеся на большинстве страниц."""
    n_pages = len(page_texts)
    if n_pages < 5:
        return page_texts
    counts: dict[str, int] = {}
    for text in page_texts:
        for line in {_norm_line(l) for l in text.split("\n") if _norm_line(l)}:
            counts[line] = counts.get(line, 0) + 1
    threshold = max(3, int(n_pages * 0.4))
    boilerplate = {l for l, c in counts.items() if c >= threshold and len(l) < 120}
    cleaned = []
    for text in page_texts:
        lines = [l for l in text.split("\n") if _norm_line(l) not in boilerplate]
        cleaned.append("\n".join(lines))
    return cleaned


# ---------- PDF ----------

def extract_pdf_chunks(path: str) -> tuple[list[dict], dict]:
    reader = PdfReader(path)
    page_texts = [(page.extract_text() or "") for page in reader.pages]
    if sum(len(t.strip()) for t in page_texts) < 50:
        raise IngestError(
            "В PDF нет текстового слоя (похоже, это скан). OCR не поддерживается в MVP."
        )
    page_texts = strip_boilerplate_lines(page_texts)
    chunks: list[dict] = []
    skipped = 0
    for page_num, text in enumerate(page_texts, start=1):
        for piece in split_text(text):
            if is_garbage_chunk(piece):
                skipped += 1
                continue
            chunks.append({"content": piece, "locator": {"page": page_num}})
    if not chunks:
        raise IngestError("После фильтрации в PDF не осталось содержательного текста")
    return chunks, {"pages": len(page_texts), "skipped_garbage": skipped}


# ---------- CSV ----------

def _decode_csv(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise IngestError("Не удалось определить кодировку CSV (поддерживаются UTF-8 и CP1251)")


def _detect_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def parse_csv(data: bytes) -> tuple[list[str], list[list[str]], dict]:
    """Возвращает (заголовок, строки, meta{encoding, delimiter})."""
    text, encoding = _decode_csv(data)
    delimiter = _detect_delimiter(text[:4000])
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        raise IngestError("CSV-файл пуст")
    header, data_rows = rows[0], rows[1:]
    return header, data_rows, {"encoding": encoding, "delimiter": delimiter, "rows": len(data_rows)}


def extract_csv_chunks(path: str, file_name: str) -> tuple[list[dict], dict]:
    with open(path, "rb") as f:
        header, data_rows, meta = parse_csv(f.read())

    header_line = " | ".join(header)
    chunks: list[dict] = []
    i = 0
    while i < len(data_rows):
        lines: list[str] = []
        row_from = i + 1  # строки данных нумеруем с 1
        chars = 0
        while i < len(data_rows) and len(lines) < CSV_ROWS_PER_CHUNK and chars < CSV_CHUNK_MAX_CHARS:
            line = " | ".join(cell.strip() for cell in data_rows[i])
            lines.append(f"строка {i + 1}: {line}")
            chars += len(line)
            i += 1
        content = (
            f"Таблица «{file_name}». Колонки: {header_line}\n" + "\n".join(lines)
        )
        chunks.append({"content": content, "locator": {"row_from": row_from, "row_to": i}})
    return chunks, meta


# ---------- Аудио / Видео ----------

async def _run_cmd(*cmd: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise IngestError(
            f"Ошибка обработки медиафайла ({cmd[0]}): {stderr.decode(errors='replace')[-400:]}"
        )
    return stdout.decode(errors="replace")


async def probe_duration(path: str) -> float:
    out = await _run_cmd(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path,
    )
    try:
        return float(out.strip())
    except ValueError:
        return 0.0


async def extract_audio_segments(src_path: str, work_dir: str) -> list[str]:
    """Извлекает аудиодорожку (mono 16kHz mp3) и режет на куски по 10 минут."""
    pattern = str(Path(work_dir) / "seg_%04d.mp3")
    await _run_cmd(
        "ffmpeg", "-y", "-i", src_path,
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k",
        "-f", "segment", "-segment_time", str(MEDIA_SEGMENT_SECONDS),
        "-reset_timestamps", "1",
        pattern,
    )
    segments = sorted(str(p) for p in Path(work_dir).glob("seg_*.mp3"))
    if not segments:
        raise IngestError("Не удалось извлечь аудиодорожку из файла")
    return segments


async def transcribe_media(path: str) -> tuple[list[dict], str, float]:
    """Транскрибация с таймкодами. Возвращает (сегменты, язык, длительность)."""
    duration = await probe_duration(path)
    all_segments: list[dict] = []
    language = ""
    with tempfile.TemporaryDirectory() as work_dir:
        parts = await extract_audio_segments(path, work_dir)
        for idx, part in enumerate(parts):
            offset = idx * MEDIA_SEGMENT_SECONDS
            result = await llm.transcribe_file(part)
            language = language or result["language"]
            if result["segments"]:
                for seg in result["segments"]:
                    all_segments.append(
                        {
                            "text": seg["text"],
                            "t_start": round(seg["t_start"] + offset, 2),
                            "t_end": round(seg["t_end"] + offset, 2),
                        }
                    )
            elif result["text"].strip():
                # Провайдер не вернул таймкоды — один сегмент на весь кусок
                part_duration = await probe_duration(part)
                all_segments.append(
                    {
                        "text": result["text"].strip(),
                        "t_start": float(offset),
                        "t_end": round(offset + part_duration, 2),
                    }
                )
    if not all_segments:
        raise IngestError("Транскрибация не дала текста (пустая запись?)")
    return all_segments, language, duration


def transcript_to_chunks(segments: list[dict]) -> list[dict]:
    """Группирует сегменты транскрипта в чанки с сохранением таймкодов."""
    chunks: list[dict] = []
    buf: list[dict] = []
    chars = 0
    for seg in segments:
        buf.append(seg)
        chars += len(seg["text"])
        if chars >= TRANSCRIPT_CHUNK_MAX_CHARS:
            chunks.append(_merge_segments(buf))
            buf, chars = [], 0
    if buf:
        chunks.append(_merge_segments(buf))
    return chunks


def _merge_segments(segments: list[dict]) -> dict:
    return {
        "content": " ".join(s["text"] for s in segments),
        "locator": {"t_start": segments[0]["t_start"], "t_end": segments[-1]["t_end"]},
    }


# ---------- Эмбеддинги ----------

def embedding_input(context: str, content: str) -> str:
    """Текст, который реально эмбеддится: контекст-префикс + содержимое чанка."""
    return f"{context}\n{content}" if context else content
