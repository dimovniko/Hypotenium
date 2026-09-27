"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useApp } from "@/components/AppContext";

export default function SuperTabs({ children }: { children: React.ReactNode }) {
  const { user } = useApp();
  const pathname = usePathname();

  if (user.role !== "admin") {
    return (
      <div className="hello-screen">
        <h1>Нет доступа</h1>
        <p>Раздел «Супердоступ» доступен только администраторам.</p>
      </div>
    );
  }

  return (
    <div className="page-wrap">
      <div className="page-inner">
        <h1>Супердоступ</h1>
        <nav className="tabs">
          <Link href="/super/studies" className={pathname.startsWith("/super/studies") ? "active" : ""}>
            Исследования
          </Link>
          <Link href="/super/users" className={pathname.startsWith("/super/users") ? "active" : ""}>
            Администрирование
          </Link>
        </nav>
        {children}
      </div>
    </div>
  );
}

export const STATUS_BADGES: Record<string, { cls: string; label: string }> = {
  ready: { cls: "badge badge-ready", label: "готово" },
  processing: { cls: "badge badge-processing", label: "обработка…" },
  uploaded: { cls: "badge badge-processing", label: "в очереди" },
  error: { cls: "badge badge-error", label: "ошибка" },
};
