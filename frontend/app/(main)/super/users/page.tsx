"use client";

import { useEffect, useState } from "react";
import { useApp } from "@/components/AppContext";
import SuperTabs from "@/components/SuperTabs";
import { api, ApiError, formatDate } from "@/lib/api";
import type { User } from "@/lib/types";

export default function UsersPage() {
  const { user: me } = useApp();
  const [users, setUsers] = useState<User[] | null>(null);
  const [error, setError] = useState("");

  const load = () => {
    api<User[]>("/admin/users").then(setUsers).catch(() => setUsers([]));
  };

  useEffect(load, []);

  const patch = async (u: User, body: { role?: string; status?: string }) => {
    setError("");
    try {
      await api(`/admin/users/${u.id}`, { method: "PATCH", body });
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось изменить пользователя");
    }
  };

  const remove = async (u: User) => {
    if (!confirm(`Удалить пользователя ${u.email}? Все его чаты будут удалены безвозвратно.`)) return;
    setError("");
    try {
      await api(`/admin/users/${u.id}`, { method: "DELETE" });
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось удалить пользователя");
    }
  };

  return (
    <SuperTabs>
      {error && <p className="error-text">{error}</p>}
      {users === null ? (
        <p className="muted">Загрузка…</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Email</th>
              <th>Роль</th>
              <th>Статус</th>
              <th>Регистрация</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>
                  {u.email}
                  {u.id === me.id && <span className="muted small"> (вы)</span>}
                </td>
                <td>
                  {u.role === "admin" ? (
                    <span className="badge badge-admin">админ</span>
                  ) : (
                    <span className="badge badge-neutral">пользователь</span>
                  )}
                </td>
                <td>
                  {u.status === "active" ? (
                    <span className="badge badge-ready">активен</span>
                  ) : (
                    <span className="badge badge-processing">ожидает доступа</span>
                  )}
                </td>
                <td className="muted">{formatDate(u.created_at || null)}</td>
                <td style={{ whiteSpace: "nowrap" }}>
                  {u.status === "pending" && (
                    <button className="btn btn-sm" onClick={() => patch(u, { status: "active" })} style={{ marginRight: 6 }}>
                      Выдать доступ
                    </button>
                  )}
                  {u.status === "active" && u.role === "user" && (
                    <button className="btn btn-sm" onClick={() => patch(u, { role: "admin" })} style={{ marginRight: 6 }}>
                      Сделать админом
                    </button>
                  )}
                  {u.status === "active" && u.role === "admin" && u.id !== me.id && (
                    <button className="btn btn-sm" onClick={() => patch(u, { role: "user" })} style={{ marginRight: 6 }}>
                      Снять админа
                    </button>
                  )}
                  {u.id !== me.id && (
                    <button className="btn btn-sm btn-danger" onClick={() => remove(u)}>
                      Удалить
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </SuperTabs>
  );
}
