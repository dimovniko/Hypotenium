"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import MessageList from "@/components/MessageList";
import SourcePanel from "@/components/SourcePanel";
import { api, ApiError } from "@/lib/api";
import type { Citation, Message } from "@/lib/types";

type SharedChat = {
  chat: { id: string; title: string };
  messages: Message[];
};

export default function SharedChatPage() {
  const { token } = useParams<{ token: string }>();
  const [data, setData] = useState<SharedChat | null>(null);
  const [error, setError] = useState("");
  const [citation, setCitation] = useState<Citation | null>(null);

  useEffect(() => {
    api<SharedChat>(`/shared/${token}`)
      .then(setData)
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Не удалось открыть чат")
      );
  }, [token]);

  if (error)
    return (
      <div className="hello-screen">
        <h1>Чат недоступен</h1>
        <p>{error}</p>
      </div>
    );
  if (!data) return <div className="page-loading">Загрузка…</div>;

  return (
    <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
      <div className="chat-area">
        <div className="chat-scroll">
          <MessageList messages={data.messages} streamingText={null} onCite={setCitation} />
        </div>
        <header className="chat-header">
          <div className="chat-header-inner">
            <h1>{data.chat.title}</h1>
            <div className="chat-header-actions">
              <span className="badge badge-neutral">общий доступ · только чтение</span>
            </div>
          </div>
        </header>
      </div>
      <div className={`panel-slot${citation ? " open" : ""}`}>
        {citation && <SourcePanel citation={citation} onClose={() => setCitation(null)} />}
      </div>
    </div>
  );
}
