"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { useApp } from "@/components/AppContext";
import ConfirmDialog from "@/components/ConfirmDialog";
import { api } from "@/lib/api";
import type { Chat } from "@/lib/types";
import { useSquircle } from "@/lib/useSquircle";

function Logo({ onClick }: { onClick?: () => void }) {
  return (
    <Link href="/" className="logo-link" title="На главную" onClick={onClick}>
      <span className="logo-dots">
        <img src="/img/logo-dot.svg" alt="" />
        <img src="/img/logo-dot.svg" alt="" />
      </span>
      <span className="brand">гипотениум</span>
    </Link>
  );
}

export default function Sidebar({
  open,
  collapsed,
  onClose,
  onToggleCollapse,
  onLogout,
}: {
  open: boolean;
  collapsed: boolean;
  onClose: () => void;
  onToggleCollapse: () => void;
  onLogout: () => void;
}) {
  const { user, chats, reloadChats } = useApp();
  const pathname = usePathname();
  const router = useRouter();
  const squircleRef = useSquircle<HTMLElement>(24);
  const [chatToDelete, setChatToDelete] = useState<Chat | null>(null);

  const confirmDelete = async () => {
    if (!chatToDelete) return;
    const chat = chatToDelete;
    setChatToDelete(null);
    await api(`/chats/${chat.id}`, { method: "DELETE" });
    await reloadChats();
    if (pathname === `/chat/${chat.id}`) router.push("/");
  };

  // На мобильных открытый оверлей всегда показывает полное меню
  const isCollapsed = collapsed && !open;

  return (
    <>
      {/* Невидимый слот: анимирует ширину, освобождая место — контент страницы центрируется плавно */}
      <div className={`sidebar-slot${isCollapsed ? " collapsed" : ""}`} aria-hidden />
      <aside ref={squircleRef} className={`sidebar${open ? " open" : ""}${isCollapsed ? " collapsed" : ""}`}>
        <div className="sidebar-head">
          <Logo onClick={onClose} />
          <button
            className="icon-btn collapse-btn"
            onClick={onToggleCollapse}
            title={isCollapsed ? "Развернуть меню" : "Свернуть меню"}
          >
            <img src="/img/icon-collapse.svg" alt="" width={28} height={28} />
          </button>
          <button className="icon-btn menu-toggle" onClick={onClose} title="Закрыть меню">
            <img src="/img/icon-close.svg" alt="Закрыть" width={28} height={28} />
          </button>
        </div>
        <Link href="/" className="btn btn-primary new-chat" onClick={onClose}>
          + Новый вопрос
        </Link>
        <p className="chats-label">Последние чаты</p>
        <nav className="chat-list">
          {chats.map((chat) => (
            <div key={chat.id} className={`chat-item${pathname === `/chat/${chat.id}` ? " active" : ""}`}>
              <Link href={`/chat/${chat.id}`} onClick={onClose} title={chat.title}>
                {chat.title}
              </Link>
              <span className="row-actions">
                <button className="icon-btn" title="Удалить" onClick={() => setChatToDelete(chat)}>
                  <img src="/img/icon-trash.svg" alt="Удалить" width={28} height={28} />
                </button>
              </span>
            </div>
          ))}
          {chats.length === 0 && <p className="muted small" style={{ padding: "8px" }}>Чатов пока нет</p>}
        </nav>
        <div className="sidebar-foot">
          {user.role === "admin" && (
            <Link href="/super/studies" className="foot-row" onClick={onClose}>
              <img src="/img/icon-super.svg" alt="" />
              <span>Супердоступ</span>
            </Link>
          )}
          <div className="foot-row">
            <span className="user-email" title={user.email}>{user.email}</span>
            <button className="icon-btn" title="Выйти" onClick={onLogout}>
              <img src="/img/icon-logout.svg" alt="Выйти" width={28} height={28} />
            </button>
          </div>
        </div>
      </aside>

      {chatToDelete && (
        <ConfirmDialog
          title="Удалить чат?"
          text={`«${chatToDelete.title}» будет удален безвозвратно вместе со всей историей.`}
          onConfirm={confirmDelete}
          onCancel={() => setChatToDelete(null)}
        />
      )}
    </>
  );
}
