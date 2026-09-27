import logging
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from .auth import hash_password
from .config import settings
from .db import async_session, init_db
from .models import User
from .routers import admin, auth, chats, files


async def _seed_admin() -> None:
    async with async_session() as session:
        existing = await session.scalar(select(User).where(User.email == settings.admin_email.lower()))
        if existing is None:
            session.add(
                User(
                    email=settings.admin_email.lower(),
                    password_hash=hash_password(settings.admin_password),
                    role="admin",
                    status="active",
                )
            )
            await session.commit()


log = logging.getLogger("uvicorn.error")


def _warn_default_secrets() -> None:
    if settings.jwt_secret in ("dev-secret-change-me", "change-me-to-random-string"):
        log.warning("JWT_SECRET is the default dev value — set a random secret in .env before exposing the service")
    if settings.admin_password == "admin12345":
        log.warning("ADMIN_PASSWORD is the default dev value — change it in .env before exposing the service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _warn_default_secrets()
    await init_db()
    await _seed_admin()
    app.state.arq = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    yield
    await app.state.arq.aclose()


app = FastAPI(title="Hypotenium API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(chats.router)
app.include_router(files.router)
app.include_router(admin.router)


@app.get("/health")
async def health():
    return {"ok": True}
