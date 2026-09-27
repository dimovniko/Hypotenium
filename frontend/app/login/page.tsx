"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import Squircle from "@/components/Squircle";
import { api, ApiError } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/auth/login", { method: "POST", body: { email, password } });
      router.replace("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось войти");
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <Squircle as="form" radius={24} className="auth-card" onSubmit={submit}>
        <h1>Hypotenium</h1>
        <p className="subtitle">Вход в сервис поиска по исследованиям</p>
        <div className="field">
          <label>Email</label>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
        </div>
        <div className="field">
          <label>Пароль</label>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </div>
        {error && <p className="error-text">{error}</p>}
        <button className="btn btn-primary" disabled={busy}>
          {busy ? "Входим…" : "Войти"}
        </button>
        <p className="auth-switch">
          Нет аккаунта? <a href="/register">Зарегистрироваться</a>
        </p>
      </Squircle>
    </div>
  );
}
