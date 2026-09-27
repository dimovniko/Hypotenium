import os
import re
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://researcher:researcher@localhost:5432/researcher"
    redis_url: str = "redis://localhost:6379"
    jwt_secret: str = "dev-secret-change-me"
    admin_email: str = "admin@example.com"
    admin_password: str = "admin12345"
    frontend_origin: str = "http://localhost:3000"
    files_dir: str = "./data/files"
    tasks_dir: str = "./tasks"
    models_config_path: str = "./config/models.yaml"
    max_file_size_mb: int = 500


settings = Settings()

_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z0-9_]+)\}")


class ModelsConfig:
    """Читает config/models.yaml с горячей перезагрузкой по mtime файла.

    Если рядом лежит models.local.yaml — берется он: в репозитории живет
    дефолтный конфиг, а личная настройка провайдеров остается вне git.
    """

    _DEFAULT_RAG = {"top_k": 12, "min_similarity": 0.2, "history_messages": 10}

    def __init__(self, path: str):
        self.path = Path(path)
        self._active: Path | None = None
        self._mtime: float | None = None
        self._data: dict = {}

    def _resolve(self) -> Path:
        local = self.path.with_name(f"{self.path.stem}.local{self.path.suffix}")
        return local if local.exists() else self.path

    def _interpolate(self, value):
        if isinstance(value, str):
            return _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
        if isinstance(value, dict):
            return {k: self._interpolate(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._interpolate(v) for v in value]
        return value

    def get(self) -> dict:
        path = self._resolve()
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            return self._data
        if path != self._active or mtime != self._mtime:
            with open(path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            self._data = self._interpolate(raw)
            self._active = path
            self._mtime = mtime
        return self._data

    def tools(self) -> dict:
        """Флаги инструментов чата: {имя: bool}. Не указанный — выключен."""
        return self.get().get("tools") or {}

    def slot(self, name: str) -> dict:
        cfg = self.get().get(name) or {}
        if not cfg.get("base_url") or not cfg.get("model"):
            raise RuntimeError(
                f"Слот '{name}' не настроен в {self.path} (нужны base_url и model)"
            )
        return cfg

    def rag(self) -> dict:
        return {**self._DEFAULT_RAG, **(self.get().get("rag") or {})}


models_config = ModelsConfig(settings.models_config_path)
