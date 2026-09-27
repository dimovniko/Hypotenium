"use client";

import { useRef, useState } from "react";
import Squircle from "@/components/Squircle";

export default function ChatInput({
  onSend,
  disabled,
  autoFocus,
  placeholder,
}: {
  onSend: (text: string) => void;
  disabled?: boolean;
  autoFocus?: boolean;
  placeholder?: string;
}) {
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const submit = () => {
    const value = text.trim();
    if (!value || disabled) return;
    setText("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    onSend(value);
  };

  return (
    <div className="chat-input-shadow">
      <Squircle radius={12} className="chat-input">
        <textarea
          ref={textareaRef}
          rows={1}
          value={text}
          placeholder={placeholder || "Задайте вопрос или гипотезу…"}
          autoFocus={autoFocus}
          onChange={(e) => {
            setText(e.target.value);
            e.target.style.height = "auto";
            e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
        />
        <button className="btn-send" onClick={submit} disabled={disabled || !text.trim()} title="Отправить">
          <img src="/img/icon-send.svg" alt="Отправить" />
        </button>
      </Squircle>
    </div>
  );
}
