"""Фоновый воркер (arq): обработка файлов исследований и переиндексация.

Важно: тяжелая работа (транскрибация, эмбеддинги) выполняется ВНЕ открытых
сессий БД. Долгая транзакция держит блокировки на таблицах и, например,
не дает пройти миграциям при старте API.
"""

import uuid

from arq.connections import RedisSettings
from sqlalchemy import delete, select, update

from . import ingest
from .config import models_config, settings
from .db import async_session
from .llm import LLMError, embed_texts
from .models import Chunk, File, Study, Transcript


def build_chunk_context(study_title: str, file_comment: str) -> str:
    """Контекст-префикс для эмбеддинга: название исследования + комментарий файла."""
    parts = []
    if study_title:
        parts.append(f"Исследование «{study_title}»")
    if file_comment.strip():
        parts.append(file_comment.strip())
    return ". ".join(parts)


async def process_file(ctx: dict, file_id: str) -> str:
    """Полный цикл обработки одного файла: текст → (транскрипт) → чанки → эмбеддинги."""
    fid = uuid.UUID(file_id)

    # 1. Короткая сессия: пометить processing и снять снапшот полей
    async with async_session() as session:
        file = await session.scalar(select(File).where(File.id == fid))
        if file is None:
            return "file not found"
        study = await session.scalar(select(Study).where(Study.id == file.study_id))
        file.status = "processing"
        file.error_message = ""
        await session.commit()
        kind = file.kind
        path = file.storage_path
        name = file.original_name
        context = build_chunk_context(study.title if study else "", file.comment)

    try:
        # 2. Тяжелая работа — без открытой транзакции
        language = ""
        segments: list[dict] | None = None
        if kind == "report_pdf":
            chunks, meta = ingest.extract_pdf_chunks(path)
        elif kind == "raw_csv":
            chunks, meta = ingest.extract_csv_chunks(path, name)
        elif kind in ("audio", "video"):
            segments, language, duration = await ingest.transcribe_media(path)
            chunks = ingest.transcript_to_chunks(segments)
            meta = {"duration": duration, "segments": len(segments)}
        else:
            raise ingest.IngestError(f"Неизвестный тип файла: {kind}")

        vectors, model_name = await embed_texts(
            [ingest.embedding_input(context, c["content"]) for c in chunks]
        )

        # 3. Короткая сессия: записать результаты
        async with async_session() as session:
            file = await session.scalar(select(File).where(File.id == fid))
            if file is None:
                return "file deleted during processing"
            if segments is not None:
                await session.execute(delete(Transcript).where(Transcript.file_id == fid))
                session.add(Transcript(file_id=fid, language=language, segments=segments))
            await session.execute(delete(Chunk).where(Chunk.file_id == fid))
            for chunk_dict, vector in zip(chunks, vectors):
                session.add(
                    Chunk(
                        study_id=file.study_id,
                        file_id=fid,
                        content=chunk_dict["content"],
                        context=context,
                        locator=chunk_dict["locator"],
                        language=language,
                        embedding=vector,
                        embedding_model=model_name,
                    )
                )
            file.meta = {**(file.meta or {}), **meta, "chunks": len(chunks)}
            file.status = "ready"
            await session.commit()
        return f"ok: {len(chunks)} chunks"
    except (ingest.IngestError, LLMError) as e:
        await _mark_error(fid, str(e))
        return f"error: {e}"
    except Exception as e:  # noqa: BLE001 — статус ошибки должен попасть в БД в любом случае
        await _mark_error(fid, f"Внутренняя ошибка обработки: {type(e).__name__}: {e}")
        raise


async def _mark_error(file_id: uuid.UUID, message: str) -> None:
    async with async_session() as session:
        await session.execute(
            update(File)
            .where(File.id == file_id)
            .values(status="error", error_message=message[:1000])
        )
        await session.commit()


async def refresh_file_embeddings(ctx: dict, file_id: str) -> str:
    """Пересчитывает эмбеддинги чанков одного файла (после смены комментария).

    Без повторной транскрибации и извлечения текста — только новый контекст + эмбеддинги.
    """
    fid = uuid.UUID(file_id)
    async with async_session() as session:
        file = await session.scalar(select(File).where(File.id == fid))
        if file is None:
            return "file not found"
        study = await session.scalar(select(Study).where(Study.id == file.study_id))
        context = build_chunk_context(study.title if study else "", file.comment)
        rows = (
            await session.execute(select(Chunk.id, Chunk.content).where(Chunk.file_id == fid))
        ).all()
    if not rows:
        return "no chunks"

    vectors, model_name = await embed_texts(
        [ingest.embedding_input(context, content) for _, content in rows]
    )

    async with async_session() as session:
        for (chunk_id, _), vector in zip(rows, vectors):
            await session.execute(
                update(Chunk)
                .where(Chunk.id == chunk_id)
                .values(embedding=vector, embedding_model=model_name, context=context)
            )
        await session.commit()
    return f"refreshed {len(rows)} chunks"


async def reindex_all(ctx: dict) -> str:
    """Пересчитывает эмбеддинги всех чанков текущей embedding-моделью (без транскрибации)."""
    target_model = models_config.slot("embedding")["model"]
    batch_size = 64
    total = 0
    while True:
        async with async_session() as session:
            rows = (
                await session.execute(
                    select(Chunk.id, Chunk.content, Chunk.context)
                    .where(Chunk.embedding_model != target_model)
                    .limit(batch_size)
                )
            ).all()
        if not rows:
            break

        vectors, model_name = await embed_texts(
            [ingest.embedding_input(context, content) for _, content, context in rows]
        )

        async with async_session() as session:
            for (chunk_id, _, _), vector in zip(rows, vectors):
                await session.execute(
                    update(Chunk)
                    .where(Chunk.id == chunk_id)
                    .values(embedding=vector, embedding_model=model_name)
                )
            await session.commit()
        total += len(rows)
    return f"reindexed {total} chunks"


class WorkerSettings:
    functions = [process_file, refresh_file_embeddings, reindex_all]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    job_timeout = 3 * 3600  # транскрибация длинных записей
    max_jobs = 4
    allow_abort_jobs = True
