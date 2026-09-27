"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { useApp } from "@/components/AppContext";
import ChatInput from "@/components/ChatInput";
import MessageList from "@/components/MessageList";
import SourcePanel from "@/components/SourcePanel";
import { api } from "@/lib/api";
import { streamAction, streamMessage } from "@/lib/sse";
import type { Chat, Citation, Message, MessageAction } from "@/lib/types";

export default function ChatPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { reloadChats } = useApp();

  const [chat, setChat] = useState<Chat | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [streaming, setStreaming] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [citation, setCitation] = useState<Citation | null>(null);
  const [shareOpen, setShareOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const sentInitial = useRef(false);

  const shareUrl =
    typeof window !== "undefined" && chat?.share_token
      ? `${window.location.origin}/shared/${chat.share_token}`
      : "";

  const send = useCallback(
    async (text: string) => {
      setSending(true);
      setError("");
      setMessages((prev) => [
        ...prev,
        { id: -Date.now(), role: "user", content: text, created_at: "", citations: [] },
      ]);
      setStreaming("");
      await streamMessage(id, text, {
        onToken: (t) => setStreaming((prev) => (prev ?? "") + t),
        onDone: (payload) => {
          setMessages((prev) => [...prev, payload.message]);
          setStreaming(null);
          setSending(false);
          if (payload.title) {
            setChat((c) => (c ? { ...c, title: payload.title! } : c));
            reloadChats();
          }
        },
        onError: (detail) => {
          setError(detail);
          setStreaming(null);
          setSending(false);
        },
      });
    },
    [id, reloadChats]
  );

  // Клик по кнопке-действию инструмента: кнопки одноразовые, выбор отображаем
  // репликой пользователя, ответ приходит тем же SSE-стримом
  const sendAction = useCallback(
    async (msg: Message, action: MessageAction) => {
      setSending(true);
      setError("");
      setMessages((prev) =>
        prev.map((m) => (m.id === msg.id ? { ...m, actions: null } : m))
      );
      setMessages((prev) => [
        ...prev,
        { id: -Date.now(), role: "user", content: action.label, created_at: "", citations: [] },
      ]);
      setStreaming("");
      await streamAction(id, msg.id, action.id, {
        onToken: (t) => setStreaming((prev) => (prev ?? "") + t),
        onDone: (payload) => {
          setMessages((prev) => [...prev, payload.message]);
          setStreaming(null);
          setSending(false);
        },
        onError: (detail) => {
          setError(detail);
          setStreaming(null);
          setSending(false);
        },
      });
    },
    [id]
  );

  useEffect(() => {
    sentInitial.current = false;
    setCitation(null);
    setError("");
    (async () => {
      try {
        const [chatData, msgs] = await Promise.all([
          api<Chat>(`/chats/${id}`),
          api<Message[]>(`/chats/${id}/messages`),
        ]);
        setChat(chatData);
        setMessages(msgs);
        const initial = sessionStorage.getItem(`initial:${id}`);
        if (initial && !sentInitial.current) {
          sentInitial.current = true;
          sessionStorage.removeItem(`initial:${id}`);
          send(initial);
        }
      } catch {
        router.replace("/");
      }
    })();
  }, [id, router, send]);

  const openShare = async () => {
    if (!chat) return;
    if (!chat.share_token) {
      const res = await api<{ share_token: string }>(`/chats/${id}/share`, { method: "POST" });
      setChat((c) => (c ? { ...c, share_token: res.share_token } : c));
    }
    setCopied(false);
    setShareOpen(true);
  };

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      /* буфер недоступен — ссылку можно выделить в поле */
    }
  };

  const revoke = async () => {
    await api(`/chats/${id}/share`, { method: "DELETE" });
    setChat((c) => (c ? { ...c, share_token: null } : c));
    setShareOpen(false);
  };

  if (!chat) return <div className="page-loading">Загрузка…</div>;

  return (
    <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
      <div className="chat-area">
        <div className="chat-scroll">
          <MessageList
            messages={messages}
            streamingText={streaming}
            onCite={setCitation}
            onAction={sendAction}
            actionsDisabled={sending}
          />
          {error && (
            <div className="chat-messages" style={{ paddingTop: 0 }}>
              <p className="error-text">{error}</p>
            </div>
          )}
        </div>
        <header className="chat-header">
          <div className="chat-header-inner">
            <h1 title={chat.title}>{chat.title}</h1>
            <div className="chat-header-actions">
              <button className="btn btn-primary" onClick={openShare}>
                Поделиться
              </button>
              {shareOpen && (
                <>
                  <div className="popover-backdrop" onClick={() => setShareOpen(false)} />
                  <div className="share-popover">
                    <p className="popover-title">Доступ по ссылке</p>
                    <p className="muted small">
                      Чат увидит любой авторизованный пользователь сервиса.
                    </p>
                    <div className="share-link-row">
                      <input readOnly value={shareUrl} onFocus={(e) => e.target.select()} />
                      <button className="btn btn-primary btn-sm" onClick={copyLink}>
                        {copied ? "Скопировано" : "Скопировать"}
                      </button>
                    </div>
                    <button className="btn btn-danger btn-sm" onClick={revoke}>
                      Отозвать доступ
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </header>
        <div className="chat-input-wrap">
          <ChatInput onSend={send} disabled={sending} />
        </div>
      </div>
      <div className={`panel-slot${citation ? " open" : ""}`}>
        {citation && <SourcePanel citation={citation} onClose={() => setCitation(null)} />}
      </div>
    </div>
  );
}
