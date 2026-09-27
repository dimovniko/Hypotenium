"""Сериализация чатов/сообщений в JSON для API.

Вынесена из роутера, чтобы инструменты (app.tools) могли собирать
такой же payload для SSE-события `done`, не импортируя роутер по кругу.
"""

from .models import Chat, Citation, Message


def chat_to_dict(chat: Chat) -> dict:
    return {
        "id": str(chat.id),
        "title": chat.title,
        "mode": chat.mode,
        "share_token": chat.share_token,
        "created_at": chat.created_at.isoformat(),
        "updated_at": chat.updated_at.isoformat(),
    }


def citation_to_dict(c: Citation) -> dict:
    return {
        "ordinal": c.ordinal,
        "study_id": str(c.study_id) if c.study_id else None,
        "study_title": c.study_title,
        "conducted_at": c.conducted_at.isoformat() if c.conducted_at else None,
        "file_id": str(c.file_id) if c.file_id else None,
        "file_name": c.file_name,
        "file_kind": c.file_kind,
        "locator": c.locator or {},
        "snippet": c.snippet,
    }


def message_to_dict(m: Message) -> dict:
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "created_at": m.created_at.isoformat(),
        "citations": [citation_to_dict(c) for c in m.citations],
        "actions": m.actions or None,
    }
