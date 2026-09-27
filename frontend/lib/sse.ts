import { API } from "./api";
import type { Message } from "./types";

export type StreamHandlers = {
  onToken: (t: string) => void;
  onDone: (payload: { message: Message; title?: string }) => void;
  onError: (detail: string) => void;
};

/** Отправляет вопрос и разбирает SSE-стрим ответа. */
export function streamMessage(
  chatId: string,
  content: string,
  handlers: StreamHandlers
): Promise<void> {
  return streamRequest(`/chats/${chatId}/messages`, { content }, handlers);
}

/** Клик по кнопке-действию инструмента — ответ приходит тем же SSE-стримом. */
export function streamAction(
  chatId: string,
  messageId: number,
  actionId: string,
  handlers: StreamHandlers
): Promise<void> {
  return streamRequest(
    `/chats/${chatId}/actions`,
    { message_id: messageId, action_id: actionId },
    handlers
  );
}

async function streamRequest(
  path: string,
  body: unknown,
  handlers: StreamHandlers
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API}${path}`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    handlers.onError("Сеть недоступна. Проверьте соединение.");
    return;
  }
  if (!res.ok || !res.body) {
    let detail = "Не удалось отправить сообщение";
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* не JSON */
    }
    handlers.onError(detail);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let gotDone = false;

  const handleEvent = (raw: string) => {
    let event = "message";
    let data = "";
    for (const line of raw.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (!data) return;
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(data);
    } catch {
      return;
    }
    if (event === "token") handlers.onToken(parsed.t as string);
    else if (event === "done") {
      gotDone = true;
      handlers.onDone(parsed as { message: Message; title?: string });
    } else if (event === "error") {
      gotDone = true;
      handlers.onError((parsed.detail as string) || "Ошибка сервиса");
    }
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const raw = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        handleEvent(raw);
      }
    }
  } catch {
    if (!gotDone) handlers.onError("Соединение прервалось во время ответа.");
    return;
  }
  if (!gotDone) handlers.onError("Ответ оборвался. Попробуйте еще раз.");
}
