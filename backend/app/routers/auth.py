from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    clear_session_cookie,
    create_session_token,
    get_current_user,
    hash_password,
    set_session_cookie,
    verify_password,
)
from ..db import get_session
from ..models import User

router = APIRouter(prefix="/auth", tags=["auth"])


class Credentials(BaseModel):
    email: EmailStr
    password: str


def user_to_dict(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "role": user.role,
        "status": user.status,
    }


@router.post("/register")
async def register(
    body: Credentials, response: Response, session: AsyncSession = Depends(get_session)
):
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Пароль должен быть не короче 8 символов")
    email = body.email.lower()
    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Пользователь с таким email уже существует")
    user = User(email=email, password_hash=hash_password(body.password))
    session.add(user)
    await session.commit()
    set_session_cookie(response, create_session_token(user.id))
    return user_to_dict(user)


@router.post("/login")
async def login(
    body: Credentials, response: Response, session: AsyncSession = Depends(get_session)
):
    user = await session.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный email или пароль")
    set_session_cookie(response, create_session_token(user.id))
    return user_to_dict(user)


@router.post("/logout")
async def logout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    return user_to_dict(user)
