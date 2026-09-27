import shutil
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from .config import settings


def files_root() -> Path:
    root = Path(settings.files_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def file_dir(file_id: uuid.UUID) -> Path:
    return files_root() / str(file_id)


async def save_upload(upload: UploadFile, file_id: uuid.UUID, original_name: str) -> tuple[str, int]:
    """Сохраняет загруженный файл, возвращает (путь, размер). Контролирует лимит размера."""
    max_bytes = settings.max_file_size_mb * 1024 * 1024
    dest_dir = file_dir(file_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / original_name
    size = 0
    try:
        with open(dest, "wb") as out:
            while True:
                block = await upload.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Файл «{original_name}» больше лимита {settings.max_file_size_mb} МБ",
                    )
                out.write(block)
    except HTTPException:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise
    return str(dest), size


def delete_file_storage(file_id: uuid.UUID) -> None:
    shutil.rmtree(file_dir(file_id), ignore_errors=True)
