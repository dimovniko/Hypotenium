"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useApp } from "@/components/AppContext";
import ChatInput from "@/components/ChatInput";
import { api } from "@/lib/api";
import type { Chat } from "@/lib/types";

export default function HomePage() {
  const router = useRouter();
  const { reloadChats } = useApp();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const start = async (text: string) => {
    setBusy(true);
    setError("");
    try {
      const chat = await api<Chat>("/chats", { method: "POST" });
      sessionStorage.setItem(`initial:${chat.id}`, text);
      await reloadChats();
      router.push(`/chat/${chat.id}`);
    } catch {
      setError("Не удалось создать чат. Попробуйте еще раз.");
      setBusy(false);
    }
  };

  return (
    <div className="hello-screen">
      <h1>Что выяснить в исследованиях?</h1>
      <p>
        Задайте вопрос или гипотезу — я найду ответ в отчетах, сырых данных и интервью
        и сошлюсь на конкретные места в источниках.
      </p>
      <ChatInput onSend={start} disabled={busy} autoFocus />
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}
