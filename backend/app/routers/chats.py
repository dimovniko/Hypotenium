import json
import re
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import llm, rag
from ..auth import require_active_user
from ..db import async_session, get_session
from ..models import Chat, Citation, Message, User, utcnow
from ..serializers import chat_to_dict, message_to_dict
from ..tools import active_tools, guarded, tool_for_action, tool_for_mode

router = APIRouter(tags=["chats"])

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class MessageIn(BaseModel):
    content: str


class ChatPatch(BaseModel):
    title: str


class ActionIn(BaseModel):
    message_id: int
    action_id: str


async def _own_chat(chat_id: uuid.UUID, user: User, session: AsyncSession) -> Chat:
    chat = await session.scalar(select(Chat).where(Chat.id == chat_id))
    if chat is None or chat.user_id != user.id:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return chat


# ---------- CRUD чатов ----------

@router.get("/chats")
async def list_chats(
    session: AsyncSession = Depends(get_session), user: User = Depends(require_active_user)
):
    chats = (
        await session.scalars(
            select(Chat).where(Chat.user_id == user.id).order_by(Chat.updated_at.desc())
        )
    ).all()
    return [chat_to_dict(c) for c in chats]


@router.post("/chats")
async def create_chat(
    session: AsyncSession = Depends(get_session), user: User = Depends(require_active_user)
):
    chat = Chat(user_id=user.id)
    session.add(chat)
    await session.commit()
    return chat_to_dict(chat)


@router.get("/chats/{chat_id}")
async def get_chat(
    chat_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_active_user),
):
    chat = await _own_chat(chat_id, user, session)
    return chat_to_dict(chat)


@router.patch("/chats/{chat_id}")
async def rename_chat(
    chat_id: uuid.UUID,
    body: ChatPatch,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_active_user),
):
    chat = await _own_chat(chat_id, user, session)
    chat.title = body.title.strip()[:255] or "Новый чат"
    await session.commit()
    return chat_to_dict(chat)


@router.delete("/chats/{chat_id}")
async def delete_chat(
    chat_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_active_user),
):
    chat = await _own_chat(chat_id, user, session)
    await session.delete(chat)
    await session.commit()
    return {"ok": True}


# ---------- Шеринг ----------

@router.post("/chats/{chat_id}/share")
async def share_chat(
    chat_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_active_user),
):
    chat = await _own_chat(chat_id, user, session)
    if not chat.share_token:
        chat.share_token = secrets.token_urlsafe(24)
        await session.commit()
    return {"share_token": chat.share_token}


@router.delete("/chats/{chat_id}/share")
async def unshare_chat(
    chat_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_active_user),
):
    chat = await _own_chat(chat_id, user, session)
    chat.share_token = None
    await session.commit()
    return {"ok": True}


@router.get("/shared/{token}")
async def view_shared_chat(
    token: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_active_user),
):
    chat = await session.scalar(
        select(Chat)
        .where(Chat.share_token == token)
        .options(selectinload(Chat.messages).selectinload(Message.citations))
    )
    if chat is None:
        raise HTTPException(status_code=404, detail="Чат не найден или доступ отозван")
    return {
        "chat": {"id": str(chat.id), "title": chat.title},
        "messages": [message_to_dict(m) for m in chat.messages],
    }


# ---------- Сообщения ----------

@router.get("/chats/{chat_id}/messages")
async def list_messages(
    chat_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_active_user),
):
    await _own_chat(chat_id, user, session)
    messages = (
        await session.scalars(
            select(Message)
            .where(Message.chat_id == chat_id)
            .options(selectinload(Message.citations))
            .order_by(Message.id)
        )
    ).all()
    return [message_to_dict(m) for m in messages]


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chats/{chat_id}/messages")
async def send_message(
    chat_id: uuid.UUID,
    body: MessageIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_active_user),
):
    chat = await _own_chat(chat_id, user, session)
    question = body.content.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Пустое сообщение")

    # Чат в режиме инструмента (например, брифинг задачи) — сообщение идет инструменту
    tool = tool_for_mode(chat.mode)
    if tool is not None:
        return StreamingResponse(
            guarded(tool.handle_message(chat_id, question, user.email)),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )
    if chat.mode != "normal":
        # Инструмент выключили в конфиге, а чат остался в его режиме — возвращаем в обычный
        chat.mode = "normal"
        chat.tool_state = None
        await session.commit()

    return StreamingResponse(
        _answer_stream(chat_id, question),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/chats/{chat_id}/actions")
async def send_action(
    chat_id: uuid.UUID,
    body: ActionIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_active_user),
):
    """Клик по кнопке-действию под сообщением ассистента. Кнопки одноразовые."""
    await _own_chat(chat_id, user, session)
    msg = await session.scalar(
        select(Message).where(Message.id == body.message_id, Message.chat_id == chat_id)
    )
    if msg is None:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    action = next((a for a in (msg.actions or []) if a.get("id") == body.action_id), None)
    if action is None:
        raise HTTPException(status_code=409, detail="Эта кнопка уже неактуальна")
    tool = tool_for_action(body.action_id)
    if tool is None:
        raise HTTPException(status_code=400, detail="Неизвестное действие")
    msg.actions = None
    await session.commit()

    return StreamingResponse(
        guarded(tool.handle_action(chat_id, action, user.email)),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


async def _answer_stream(chat_id: uuid.UUID, question: str):
    """Генератор SSE: сохраняет вопрос, ищет источники, стримит ответ, сохраняет цитаты."""
    try:
        # 1. История + сохранение вопроса пользователя
        async with async_session() as session:
            history = (
                await session.scalars(
                    select(Message).where(Message.chat_id == chat_id).order_by(Message.id)
                )
            ).all()
            is_first_exchange = not any(m.role == "assistant" for m in history)
            user_msg = Message(chat_id=chat_id, role="user", content=question)
            session.add(user_msg)
            await session.commit()
            yield _sse("user_message", {"id": user_msg.id})

        # 2. Переформулировка запроса и поиск
        rewritten = await rag.rewrite_query(list(history), question)
        query_vectors, embedding_model = await llm.embed_texts([rewritten["query"]])

        async with async_session() as session:
            sources = await rag.search_chunks(
                session,
                query_vectors[0],
                embedding_model,
                rewritten["date_from"],
                rewritten["date_to"],
            )

        # 3. Ответ: либо честное «не найдено», либо стриминг LLM.
        # Номера цитат сразу переводятся в сквозную нумерацию чата.
        async with async_session() as session:
            prior_citations = (
                await session.execute(
                    select(Citation)
                    .join(Message, Message.id == Citation.message_id)
                    .where(Message.chat_id == chat_id)
                    .order_by(Citation.id)
                )
            ).scalars().all()
        prior_by_ordinal = {c.ordinal: c for c in prior_citations}
        last_ordinal = max(prior_by_ordinal, default=0)
        renumberer = rag.CitationRenumberer(len(sources), start_from=last_ordinal + 1)

        if not sources:
            answer = rag.NO_RESULTS_ANSWER
            yield _sse("token", {"t": answer})
        else:
            messages = rag.build_answer_messages(list(history), question, sources)
            parts: list[str] = []
            async for delta in llm.chat_completion_stream(messages):
                piece = renumberer.feed(delta)
                if piece:
                    parts.append(piece)
                    yield _sse("token", {"t": piece})
            tail = renumberer.flush()
            if tail:
                parts.append(tail)
                yield _sse("token", {"t": tail})
            answer = "".join(parts).strip()
            if not answer:
                answer = rag.NO_RESULTS_ANSWER
                yield _sse("token", {"t": answer})

        # 3.5 Инструменты: если информации в базе нет (поиск пуст, ответ без единой
        # ссылки на источник или текст «ничего не найдено» — модель иногда ставит
        # ссылки даже в пустой ответ) — инструмент предлагает следующий шаг кнопками
        has_markers = bool(renumberer.mapping) or any(
            int(m.group(1)) in prior_by_ordinal for m in re.finditer(r"\[(\d{1,3})\]", answer)
        )
        has_info = has_markers and not rag.looks_like_no_info(answer)
        offer_actions = None
        for tool in active_tools():
            offer = tool.check_trigger(question, rewritten["query"], has_info)
            if offer:
                yield _sse("token", {"t": "\n\n" + offer["text"]})
                answer = f"{answer}\n\n{offer['text']}"
                offer_actions = offer["actions"]
                break

        # 4. Сохранение ответа и цитат
        async with async_session() as session:
            assistant_msg = Message(
                chat_id=chat_id, role="assistant", content=answer, actions=offer_actions
            )
            session.add(assistant_msg)
            await session.flush()
            new_ordinals = set(renumberer.mapping.values())
            for source_index, ordinal in sorted(renumberer.mapping.items(), key=lambda kv: kv[1]):
                s = sources[source_index - 1]
                session.add(
                    Citation(
                        message_id=assistant_msg.id,
                        ordinal=ordinal,
                        study_id=s["study_id"],
                        file_id=s["file_id"],
                        study_title=s["study_title"],
                        conducted_at=s["conducted_at"],
                        file_name=s["file_name"],
                        file_kind=s["file_kind"],
                        locator=s["locator"],
                        # Чанк целиком: обрезанный сниппет не позволял проверить цитату по панели
                        snippet=s["content"],
                    )
                )
            # Модель может сослаться на номер из ПРОШЛЫХ сообщений чата — прикрепляем
            # копию той цитаты, чтобы маркер остался кликабельным и вел на тот же источник
            attached_stale: set[int] = set()
            for marker in re.finditer(r"\[(\d{1,3})\]", answer):
                n = int(marker.group(1))
                if n in new_ordinals or n in attached_stale:
                    continue
                prior = prior_by_ordinal.get(n)
                if prior is None:
                    continue
                attached_stale.add(n)
                session.add(
                    Citation(
                        message_id=assistant_msg.id,
                        ordinal=n,
                        study_id=prior.study_id,
                        file_id=prior.file_id,
                        study_title=prior.study_title,
                        conducted_at=prior.conducted_at,
                        file_name=prior.file_name,
                        file_kind=prior.file_kind,
                        locator=prior.locator,
                        snippet=prior.snippet,
                    )
                )

            chat = await session.scalar(select(Chat).where(Chat.id == chat_id))
            new_title = None
            if chat is not None:
                chat.updated_at = utcnow()
                if is_first_exchange:
                    new_title = await rag.generate_chat_title(question)
                    chat.title = new_title
            await session.commit()

            full = await session.scalar(
                select(Message)
                .where(Message.id == assistant_msg.id)
                .options(selectinload(Message.citations))
            )
            payload = {"message": message_to_dict(full)}
            if new_title:
                payload["title"] = new_title
            yield _sse("done", payload)

    except llm.LLMError as e:
        yield _sse(
            "error",
            {"detail": "Сервис ответов временно недоступен, попробуйте позже.", "debug": str(e)[:300]},
        )
    except Exception as e:  # noqa: BLE001 — ошибка должна дойти до пользователя, не оборвать SSE
        yield _sse(
            "error",
            {"detail": "Внутренняя ошибка сервиса.", "debug": f"{type(e).__name__}: {e}"[:300]},
        )
