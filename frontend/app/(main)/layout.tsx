"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AppContext } from "@/components/AppContext";
import Sidebar from "@/components/Sidebar";
import { api } from "@/lib/api";
import type { Chat, User } from "@/lib/types";

export default function MainLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [chats, setChats] = useState<Chat[]>([]);
  const [loading, setLoading] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    setCollapsed(localStorage.getItem("sidebar-collapsed") === "1");
  }, []);

  const toggleCollapse = () => {
    setCollapsed((v) => {
      localStorage.setItem("sidebar-collapsed", v ? "0" : "1");
      return !v;
    });
  };

  const reloadChats = useCallback(async () => {
    try {
      setChats(await api<Chat[]>("/chats"));
    } catch {
      /* не критично */
    }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const me = await api<User>("/auth/me");
        setUser(me);
        if (me.status === "active") await reloadChats();
        setLoading(false);
      } catch {
        router.replace("/login");
      }
    })();
  }, [router, reloadChats]);

  const logout = async () => {
    await api("/auth/logout", { method: "POST" });
    router.replace("/login");
  };

  if (loading || !user) return <div className="page-loading">Загрузка…</div>;

  if (user.status !== "active") {
    return (
      <div className="auth-page">
        <div className="auth-card">
          <h1>Ожидайте выдачи доступа</h1>
          <p className="subtitle">
            Аккаунт {user.email} создан. Администратор должен выдать вам доступ — после этого
            обновите страницу.
          </p>
          <button className="btn" onClick={logout}>
            Выйти
          </button>
        </div>
      </div>
    );
  }

  return (
    <AppContext.Provider value={{ user, chats, reloadChats }}>
      <div className="app-shell">
        {sidebarOpen && <div className="sidebar-backdrop" onClick={() => setSidebarOpen(false)} />}
        <Sidebar
          open={sidebarOpen}
          collapsed={collapsed}
          onClose={() => setSidebarOpen(false)}
          onToggleCollapse={toggleCollapse}
          onLogout={logout}
        />
        <div className="main-area">
          <MobileHeader onMenu={() => setSidebarOpen(true)} />
          {children}
        </div>
      </div>
    </AppContext.Provider>
  );
}

function MobileHeader({ onMenu }: { onMenu: () => void }) {
  return (
    <button className="icon-btn menu-toggle menu-open" onClick={onMenu} title="Открыть меню">
      <img src="/img/icon-menu.svg" alt="Меню" width={28} height={28} />
    </button>
  );
}
