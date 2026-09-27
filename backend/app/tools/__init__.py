"""Инструменты (модули) чата.

Инструмент — класс с тремя точками входа:
- check_trigger()  — вызывается после обычного RAG-ответа; может вернуть
  предложение (текст + кнопки), которое дописывается к ответу ассистента;
- handle_action()  — обрабатывает клик по кнопке своего сообщения;
- handle_message() — обрабатывает обычные сообщения, пока чат находится
  в режиме этого инструмента (chat.mode == tool.mode).

Триггеры детерминированные (проверяет код, а не LLM): chat-модель ненадежна
в выборе функций, поэтому LLM работает только ВНУТРИ уже запущенного
инструмента. Ответы инструментов идут тем же SSE-протоколом, что и RAG
(user_message/token/done/error), так что фронтенду достаточно уметь
показывать кнопки `actions` у сообщения.

Новый инструмент: класс-наследник Tool + экземпляр в списке TOOLS внизу файла.
Включаются инструменты флагами в config/models.yaml (секция `tools`), по умолчанию
все выключены — экспериментальные модули не должны влиять на обычный чат.
"""

import json
from typing import AsyncIterator

from .. import llm
from ..config import models_config


class Tool:
    name: str = ""  # префикс id действий: "<name>:<действие>"
    mode: str = ""  # значение chat.mode, при котором сообщения идут в инструмент

    def check_trigger(self, question: str, search_query: str, has_info: bool) -> dict | None:
        """Вернуть {"text": str, "actions": list} — предложение после RAG-ответа."""
        return None

    def owns_action(self, action_id: str) -> bool:
        return action_id.startswith(self.name + ":")

    def handle_action(self, chat_id, action: dict, user_email: str) -> AsyncIterator[str]:
        raise NotImplementedError

    def handle_message(self, chat_id, text: str, user_email: str) -> AsyncIterator[str]:
        raise NotImplementedError


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def guarded(agen: AsyncIterator[str]) -> AsyncIterator[str]:
    """Оборачивает стрим инструмента: любая ошибка уходит SSE-событием, не рвет соединение."""
    try:
        async for event in agen:
            yield event
    except llm.LLMError as e:
        yield sse_event(
            "error",
            {"detail": "Сервис ответов временно недоступен, попробуйте позже.", "debug": str(e)[:300]},
        )
    except Exception as e:  # noqa: BLE001 — ошибка должна дойти до пользователя
        yield sse_event(
            "error",
            {"detail": "Внутренняя ошибка сервиса.", "debug": f"{type(e).__name__}: {e}"[:300]},
        )


from .research_task import ResearchTaskTool  # noqa: E402 — после базовых определений

TOOLS: list[Tool] = [ResearchTaskTool()]


def active_tools() -> list[Tool]:
    """Инструменты, включенные в конфиге (перечитывается на лету)."""
    flags = models_config.tools()
    return [tool for tool in TOOLS if flags.get(tool.name) is True]


def tool_for_mode(mode: str) -> Tool | None:
    for tool in active_tools():
        if tool.mode and tool.mode == mode:
            return tool
    return None


def tool_for_action(action_id: str) -> Tool | None:
    for tool in active_tools():
        if tool.owns_action(action_id):
            return tool
    return None
