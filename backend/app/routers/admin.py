import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File as FastAPIFile, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import require_admin
from ..config import models_config
from ..db import get_session
from ..models import Chunk, File, Study, User
from ..storage import delete_file_storage, save_upload

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

EXT_TO_KIND = {
    ".pdf": ("report_pdf", "application/pdf"),
    ".csv": ("raw_csv", "text/csv"),
    ".mp3": ("audio", "audio/mpeg"),
    ".wav": ("audio", "audio/wav"),
    ".m4a": ("audio", "audio/mp4"),
    ".ogg": ("audio", "audio/ogg"),
    ".flac": ("audio", "audio/flac"),
    ".aac": ("audio", "audio/aac"),
    ".mp4": ("video", "video/mp4"),
    ".mov": ("video", "video/quicktime"),
    ".webm": ("video", "video/webm"),
    ".mkv": ("video", "video/x-matroska"),
    ".avi": ("video", "video/x-msvideo"),
}


def file_to_dict(f: File) -> dict:
    return {
        "id": str(f.id),
        "kind": f.kind,
        "original_name": f.original_name,
        "comment": f.comment,
        "size_bytes": f.size_bytes,
        "status": f.status,
        "error_message": f.error_message,
        "meta": f.meta or {},
        "created_at": f.created_at.isoformat(),
    }


def study_status(files: list[File]) -> str:
    if any(f.status in ("uploaded", "processing") for f in files):
        return "processing"
    if any(f.status == "error" for f in files):
        return "error"
    return "ready"


def study_to_dict(s: Study, with_files: bool = False) -> dict:
    data = {
        "id": str(s.id),
        "title": s.title,
        "conducted_at": s.conducted_at.isoformat(),
        "comment": s.comment,
        "created_at": s.created_at.isoformat(),
        "files_count": len(s.files),
        "status": study_status(s.files),
    }
    if with_files:
        data["files"] = [file_to_dict(f) for f in s.files]
    return data


async def _save_and_enqueue(
    request: Request,
    session: AsyncSession,
    study: Study,
    uploads: list[UploadFile],
    comments: list[str] | None = None,
) -> list[File]:
    created: list[File] = []
    comments = comments or []
    for i, upload in enumerate(uploads):
        name = Path(upload.filename or "file").name
        ext = Path(name).suffix.lower()
        if ext not in EXT_TO_KIND:
            raise HTTPException(
                status_code=400,
                detail=f"Файл «{name}»: поддерживаются PDF, CSV, аудио и видео",
            )
        kind, mime = EXT_TO_KIND[ext]
        file_id = uuid.uuid4()
        path, size = await save_upload(upload, file_id, name)
        file = File(
            id=file_id,
            study_id=study.id,
            kind=kind,
            original_name=name,
            comment=(comments[i].strip() if i < len(comments) else ""),
            storage_path=path,
            size_bytes=size,
            mime=upload.content_type or mime,
        )
        session.add(file)
        created.append(file)
    await session.commit()
    for file in created:
        await request.app.state.arq.enqueue_job("process_file", str(file.id))
    return created


# ---------- Исследования ----------

@router.get("/studies")
async def list_studies(session: AsyncSession = Depends(get_session)):
    studies = (
        await session.scalars(
            select(Study).options(selectinload(Study.files)).order_by(Study.conducted_at.desc())
        )
    ).all()
    return [study_to_dict(s) for s in studies]


@router.post("/studies")
async def create_study(
    request: Request,
    title: str = Form(...),
    conducted_at: str = Form(...),
    comment: str = Form(""),
    files: list[UploadFile] = FastAPIFile(...),
    file_comments: list[str] = Form([]),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
):
    try:
        conducted = date.fromisoformat(conducted_at)
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректная дата проведения")
    if not title.strip():
        raise HTTPException(status_code=400, detail="Название обязательно")

    study = Study(title=title.strip(), conducted_at=conducted, comment=comment.strip(), created_by=admin.id)
    session.add(study)
    await session.flush()
    await _save_and_enqueue(request, session, study, files, file_comments)
    full = await session.scalar(
        select(Study).where(Study.id == study.id).options(selectinload(Study.files))
    )
    return study_to_dict(full, with_files=True)


@router.get("/studies/{study_id}")
async def get_study(study_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    study = await session.scalar(
        select(Study).where(Study.id == study_id).options(selectinload(Study.files))
    )
    if study is None:
        raise HTTPException(status_code=404, detail="Исследование не найдено")
    return study_to_dict(study, with_files=True)


class StudyPatch(BaseModel):
    title: str | None = None
    conducted_at: date | None = None
    comment: str | None = None


@router.patch("/studies/{study_id}")
async def update_study(
    study_id: uuid.UUID, body: StudyPatch, session: AsyncSession = Depends(get_session)
):
    study = await session.scalar(
        select(Study).where(Study.id == study_id).options(selectinload(Study.files))
    )
    if study is None:
        raise HTTPException(status_code=404, detail="Исследование не найдено")
    if body.title is not None:
        study.title = body.title.strip() or study.title
    if body.conducted_at is not None:
        study.conducted_at = body.conducted_at
    if body.comment is not None:
        study.comment = body.comment.strip()
    await session.commit()
    return study_to_dict(study, with_files=True)


@router.delete("/studies/{study_id}")
async def delete_study(study_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    study = await session.scalar(
        select(Study).where(Study.id == study_id).options(selectinload(Study.files))
    )
    if study is None:
        raise HTTPException(status_code=404, detail="Исследование не найдено")
    file_ids = [f.id for f in study.files]
    await session.delete(study)  # каскадом удаляет файлы, транскрипты и чанки
    await session.commit()
    for file_id in file_ids:
        delete_file_storage(file_id)
    return {"ok": True}


@router.post("/studies/{study_id}/files")
async def add_files(
    study_id: uuid.UUID,
    request: Request,
    files: list[UploadFile] = FastAPIFile(...),
    file_comments: list[str] = Form([]),
    session: AsyncSession = Depends(get_session),
):
    study = await session.scalar(select(Study).where(Study.id == study_id))
    if study is None:
        raise HTTPException(status_code=404, detail="Исследование не найдено")
    created = await _save_and_enqueue(request, session, study, files, file_comments)
    return [file_to_dict(f) for f in created]


class FilePatch(BaseModel):
    comment: str


@router.patch("/files/{file_id}")
async def update_file(
    file_id: uuid.UUID,
    body: FilePatch,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    file = await session.scalar(select(File).where(File.id == file_id))
    if file is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    new_comment = body.comment.strip()
    changed = new_comment != file.comment
    file.comment = new_comment
    await session.commit()
    # Комментарий участвует в эмбеддингах — пересчитываем их (без повторной транскрибации)
    if changed and file.status == "ready":
        await request.app.state.arq.enqueue_job("refresh_file_embeddings", str(file.id))
    return file_to_dict(file)


@router.delete("/files/{file_id}")
async def delete_file(file_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    file = await session.scalar(select(File).where(File.id == file_id))
    if file is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    await session.delete(file)  # каскадом удаляет транскрипт и чанки
    await session.commit()
    delete_file_storage(file_id)
    return {"ok": True}


@router.post("/files/{file_id}/retry")
async def retry_file(
    file_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)
):
    file = await session.scalar(select(File).where(File.id == file_id))
    if file is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    file.status = "uploaded"
    file.error_message = ""
    await session.commit()
    await request.app.state.arq.enqueue_job("process_file", str(file.id))
    return file_to_dict(file)


# ---------- Пользователи ----------

def _user_row(u: User) -> dict:
    return {
        "id": str(u.id),
        "email": u.email,
        "role": u.role,
        "status": u.status,
        "created_at": u.created_at.isoformat(),
    }


@router.get("/users")
async def list_users(session: AsyncSession = Depends(get_session)):
    users = (await session.scalars(select(User).order_by(User.created_at))).all()
    return [_user_row(u) for u in users]


class UserPatch(BaseModel):
    role: str | None = None    # user | admin
    status: str | None = None  # pending | active


async def _admins_count(session: AsyncSession) -> int:
    return await session.scalar(
        select(func.count()).select_from(User).where(User.role == "admin", User.status == "active")
    )


@router.patch("/users/{user_id}")
async def update_user(
    user_id: uuid.UUID,
    body: UserPatch,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
):
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    demoting_admin = user.role == "admin" and (
        (body.role is not None and body.role != "admin")
        or (body.status is not None and body.status != "active")
    )
    if demoting_admin and await _admins_count(session) <= 1:
        raise HTTPException(status_code=400, detail="Нельзя убрать последнего администратора")

    if body.role is not None:
        if body.role not in ("user", "admin"):
            raise HTTPException(status_code=400, detail="Некорректная роль")
        user.role = body.role
    if body.status is not None:
        if body.status not in ("pending", "active"):
            raise HTTPException(status_code=400, detail="Некорректный статус")
        user.status = body.status
    await session.commit()
    return _user_row(user)


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if user.role == "admin" and await _admins_count(session) <= 1:
        raise HTTPException(status_code=400, detail="Нельзя удалить последнего администратора")
    await session.delete(user)  # каскадом удаляет чаты пользователя
    await session.commit()
    return {"ok": True}


# ---------- Настройки моделей ----------

def _mask(key: str) -> str:
    if not key:
        return ""
    return key[:4] + "…" + key[-4:] if len(key) > 8 else "•••"


@router.get("/settings/models")
async def models_settings(session: AsyncSession = Depends(get_session)):
    config = models_config.get()
    slots = {}
    for name in ("chat", "embedding", "transcription"):
        slot = dict(config.get(name) or {})
        for secret_field in ("api_key", "auth_key"):
            if slot.get(secret_field):
                slot[secret_field] = _mask(slot[secret_field])
        slots[name] = slot

    current_embedding = (config.get("embedding") or {}).get("model", "")
    outdated = await session.scalar(
        select(func.count()).select_from(Chunk).where(Chunk.embedding_model != current_embedding)
    )
    total = await session.scalar(select(func.count()).select_from(Chunk))
    return {
        "slots": slots,
        "rag": models_config.rag(),
        "chunks_total": total,
        "chunks_outdated": outdated,
        "needs_reindex": bool(outdated),
    }


@router.post("/reindex")
async def reindex(request: Request):
    await request.app.state.arq.enqueue_job("reindex_all")
    return {"ok": True}
