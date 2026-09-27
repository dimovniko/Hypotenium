"use client";

import { useEffect, useRef } from "react";
import Squircle from "@/components/Squircle";
import type { Citation, Message, MessageAction } from "@/lib/types";

const USER_RADII = { tl: 12, tr: 12, br: 2, bl: 12 };
const ASSISTANT_RADII = { tl: 12, tr: 12, br: 12, bl: 2 };

/** Текст с кликабельными цитатами [n]. */
function Cited({
  text,
  citations,
  onCite,
}: {
  text: string;
  citations: Citation[];
  onCite: (c: Citation) => void;
}) {
  const byOrdinal = new Map(citations.map((c) => [c.ordinal, c]));
  const parts = text.split(/(\[\d{1,3}\])/g);
  return (
    <>
      {parts.map((part, i) => {
        const m = part.match(/^\[(\d{1,3})\]$/);
        if (m) {
          const citation = byOrdinal.get(parseInt(m[1], 10));
          if (citation) {
            return (
              <button
                key={i}
                className="cite-chip"
                title={`${citation.study_title} — ${citation.file_name}`}
                onClick={() => onCite(citation)}
              >
                {citation.ordinal}
              </button>
            );
          }
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}

/** Инлайн-маркдаун: **жирный**, *курсив*, `код` — с работающими цитатами внутри. */
const INLINE_MD_RE = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\n]+\*)/g;

function InlineMd({
  text,
  citations,
  onCite,
}: {
  text: string;
  citations: Citation[];
  onCite: (c: Citation) => void;
}) {
  const parts = text.split(INLINE_MD_RE);
  return (
    <>
      {parts.map((part, i) => {
        if (/^\*\*[^*]+\*\*$/.test(part)) {
          return (
            <strong key={i}>
              <InlineMd text={part.slice(2, -2)} citations={citations} onCite={onCite} />
            </strong>
          );
        }
        if (/^`[^`]+`$/.test(part)) {
          return (
            <code key={i} className="msg-code">
              {part.slice(1, -1)}
            </code>
          );
        }
        if (/^\*[^*\n]+\*$/.test(part)) {
          return (
            <em key={i}>
              <InlineMd text={part.slice(1, -1)} citations={citations} onCite={onCite} />
            </em>
          );
        }
        return <Cited key={i} text={part} citations={citations} onCite={onCite} />;
      })}
    </>
  );
}

/** Разбор текста ответа на абзацы, списки и заголовки — на уровне сервиса, без участия LLM. */
type Block =
  | { type: "p"; text: string }
  | { type: "h"; text: string }
  | { type: "ul" | "ol"; items: string[] };

function parseBlocks(content: string): Block[] {
  const blocks: Block[] = [];
  let para: string[] = [];
  const flushPara = () => {
    const text = para.join("\n").replace(/\n{3,}/g, "\n\n").trim();
    if (text) blocks.push({ type: "p", text });
    para = [];
  };
  for (const line of content.split("\n")) {
    const heading = line.match(/^\s*#{1,6}\s+(.+)/);
    if (heading) {
      flushPara();
      blocks.push({ type: "h", text: heading[1].trim() });
      continue;
    }
    const ul = line.match(/^\s*[-–—•*]\s+(.+)/);
    const ol = ul ? null : line.match(/^\s*\d{1,3}[.)]\s+(.+)/);
    if (ul || ol) {
      flushPara();
      const type = ul ? "ul" : "ol";
      const item = (ul ? ul[1] : ol![1]).trim();
      const last = blocks[blocks.length - 1];
      if (last && last.type === type) {
        last.items.push(item);
      } else {
        blocks.push({ type, items: [item] });
      }
    } else {
      para.push(line);
    }
  }
  flushPara();
  return blocks;
}

function AssistantContent({
  content,
  citations,
  onCite,
}: {
  content: string;
  citations: Citation[];
  onCite: (c: Citation) => void;
}) {
  const blocks = parseBlocks(content);
  return (
    <>
      {blocks.map((block, i) => {
        if (block.type === "p") {
          return (
            <p key={i} className="msg-p">
              <InlineMd text={block.text} citations={citations} onCite={onCite} />
            </p>
          );
        }
        if (block.type === "h") {
          return (
            <p key={i} className="msg-h">
              <InlineMd text={block.text} citations={citations} onCite={onCite} />
            </p>
          );
        }
        const List = block.type === "ul" ? "ul" : "ol";
        return (
          <List key={i} className="msg-list">
            {block.items.map((item, j) => (
              <li key={j}>
                <InlineMd text={item} citations={citations} onCite={onCite} />
              </li>
            ))}
          </List>
        );
      })}
    </>
  );
}

export default function MessageList({
  messages,
  streamingText,
  onCite,
  onAction,
  actionsDisabled,
}: {
  messages: Message[];
  streamingText: string | null;
  onCite: (c: Citation) => void;
  onAction?: (msg: Message, action: MessageAction) => void;
  actionsDisabled?: boolean;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, streamingText]);

  return (
    <div className="chat-messages">
      {messages.map((msg) =>
        msg.role === "user" ? (
          <Squircle key={msg.id} radius={USER_RADII} className="msg msg-user">{msg.content}</Squircle>
        ) : (
          <Squircle key={msg.id} radius={ASSISTANT_RADII} className="msg msg-assistant">
            <AssistantContent content={msg.content} citations={msg.citations} onCite={onCite} />
            {onAction && msg.actions && msg.actions.length > 0 && (
              <div className="msg-actions">
                {msg.actions.map((action) => (
                  <button
                    key={action.id}
                    className="btn msg-action-btn"
                    disabled={actionsDisabled}
                    onClick={() => onAction(msg, action)}
                  >
                    {action.label}
                  </button>
                ))}
              </div>
            )}
          </Squircle>
        )
      )}
      {streamingText !== null && (
        <Squircle radius={ASSISTANT_RADII} className="msg msg-assistant streaming">
          <AssistantContent content={streamingText} citations={[]} onCite={onCite} />
        </Squircle>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
