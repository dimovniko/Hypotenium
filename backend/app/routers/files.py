import os
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_active_user
from ..db import get_session
from ..ingest import parse_csv
from ..models import File, Study, Transcript, User

router = APIRouter(prefix="/files", tags=["files"])

STREAM_CHUNK = 512 * 1024


async def _get_file(file_id: uuid.UUID, session: AsyncSession) -> File:
    file = await session.scalar(select(File).where(File.id == file_id))
    if file is None or not os.path.exists(file.storage_path):
        raise HTTPException(status_code=404, detail="Источник удален")
    return file


@router.get("/{file_id}/meta")
async def file_meta(
    file_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_active_user),
):
    file = await _get_file(file_id, session)
    study = await session.scalar(select(Study).where(Study.id == file.study_id))
    transcript_exists = await session.scalar(
        select(Transcript.id).where(Transcript.file_id == file.id)
    )
    return {
        "id": str(file.id),
        "kind": file.kind,
        "original_name": file.original_name,
        "comment": file.comment,
        "mime": file.mime,
        "size_bytes": file.size_bytes,
        "status": file.status,
        "meta": file.meta or {},
        "has_transcript": transcript_exists is not None,
        "study": {
            "id": str(study.id) if study else None,
            "title": study.title if study else "",
            "conducted_at": study.conducted_at.isoformat() if study else None,
        },
    }


def _iter_file(path: str, start: int, end: int):
    with open(path, "rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            block = f.read(min(STREAM_CHUNK, remaining))
            if not block:
                break
            remaining -= len(block)
            yield block


def _content_disposition(disposition: str, filename: str) -> str:
    return f"{disposition}; filename*=UTF-8''{quote(filename)}"


@router.get("/{file_id}/content")
async def file_content(
    file_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_active_user),
):
    """Отдает файл для просмотра: PDF inline, медиа — с поддержкой Range для перемотки."""
    file = await _get_file(file_id, session)
    size = os.path.getsize(file.storage_path)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": _content_disposition("inline", file.original_name),
    }

    range_header = request.headers.get("range")
    start, end = 0, size - 1
    status_code = 200
    if range_header and range_header.startswith("bytes="):
        try:
            range_value = range_header[len("bytes="):].split(",")[0]
            start_s, _, end_s = range_value.partition("-")
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                raise ValueError
            status_code = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        except ValueError:
            raise HTTPException(status_code=416, detail="Некорректный Range")

    headers["Content-Length"] = str(end - start + 1)
    return StreamingResponse(
        _iter_file(file.storage_path, start, end),
        status_code=status_code,
        media_type=file.mime or "application/octet-stream",
        headers=headers,
    )


@router.get("/{file_id}/download")
async def file_download(
    file_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_active_user),
):
    file = await _get_file(file_id, session)
    size = os.path.getsize(file.storage_path)
    return StreamingResponse(
        _iter_file(file.storage_path, 0, size - 1),
        media_type=file.mime or "application/octet-stream",
        headers={
            "Content-Length": str(size),
            "Content-Disposition": _content_disposition("attachment", file.original_name),
        },
    )


@router.get("/{file_id}/csv")
async def file_csv_fragment(
    file_id: uuid.UUID,
    row_from: int = Query(1, ge=1),
    row_to: int = Query(50, ge=1),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_active_user),
):
    """Фрагмент CSV для сайд-панели (строки нумеруются с 1, без заголовка)."""
    file = await _get_file(file_id, session)
    if file.kind != "raw_csv":
        raise HTTPException(status_code=400, detail="Файл не является CSV")
    with open(file.storage_path, "rb") as f:
        header, data_rows, _ = parse_csv(f.read())
    row_to = min(row_to, len(data_rows))
    rows = [
        {"n": n, "cells": data_rows[n - 1]}
        for n in range(row_from, row_to + 1)
        if 1 <= n <= len(data_rows)
    ]
    return {"header": header, "rows": rows, "total": len(data_rows)}


@router.get("/{file_id}/transcript")
async def file_transcript(
    file_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_active_user),
):
    file = await _get_file(file_id, session)
    transcript = await session.scalar(select(Transcript).where(Transcript.file_id == file.id))
    if transcript is None:
        raise HTTPException(status_code=404, detail="Транскрипт не найден")
    return {"language": transcript.language, "segments": transcript.segments}
