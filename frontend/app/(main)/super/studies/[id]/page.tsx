"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import FilePicker, { appendPickedFiles, type PickedFile } from "@/components/FilePicker";
import SuperTabs, { STATUS_BADGES } from "@/components/SuperTabs";
import { API, api, ApiError, formatBytes, uploadForm } from "@/lib/api";
import type { Study, StudyFile } from "@/lib/types";
import { KIND_LABELS } from "@/lib/types";

export default function StudyDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [study, setStudy] = useState<Study | null>(null);
  const [title, setTitle] = useState("");
  const [conductedAt, setConductedAt] = useState("");
  const [comment, setComment] = useState("");
  const [saving, setSaving] = useState(false);
  const [newFiles, setNewFiles] = useState<PickedFile[]>([]);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const s = await api<Study>(`/admin/studies/${id}`);
      setStudy(s);
      setTitle((prev) => (prev === "" ? s.title : prev));
      setConductedAt((prev) => (prev === "" ? s.conducted_at : prev));
      setComment((prev) => (prev === "" ? s.comment : prev));
    } catch {
      router.replace("/super/studies");
    }
  }, [id, router]);

  useEffect(() => {
    load();
  }, [load]);

  // Пока идет обработка — обновляем статусы
  useEffect(() => {
    if (study?.status !== "processing") return;
    const t = setTimeout(load, 3000);
    return () => clearTimeout(t);
  }, [study, load]);

  const saveMeta = async () => {
    setSaving(true);
    setError("");
    try {
      await api(`/admin/studies/${id}`, {
        method: "PATCH",
        body: { title, conducted_at: conductedAt, comment },
      });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить");
    } finally {
      setSaving(false);
    }
  };

  const uploadNewFiles = async () => {
    if (newFiles.length === 0) return;
    setError("");
    setProgress(0);
    const fd = new FormData();
    appendPickedFiles(fd, newFiles);
    try {
      await uploadForm(`/admin/studies/${id}/files`, fd, setProgress);
      setNewFiles([]);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить файлы");
    } finally {
      setProgress(null);
    }
  };

  const editFileComment = async (f: StudyFile) => {
    const value = prompt(
      "Комментарий к файлу (участвует в поиске — что внутри, сегмент, респондент):",
      f.comment
    );
    if (value === null || value.trim() === f.comment) return;
    await api(`/admin/files/${f.id}`, { method: "PATCH", body: { comment: value } });
    await load();
  };

  const deleteFile = async (f: StudyFile) => {
    if (!confirm(`Удалить файл «${f.original_name}»? Он пропадет из поиска, в старых чатах останется пометка «Источник удален».`)) return;
    await api(`/admin/files/${f.id}`, { method: "DELETE" });
    await load();
  };

  const retryFile = async (f: StudyFile) => {
    await api(`/admin/files/${f.id}/retry`, { method: "POST" });
    await load();
  };

  const deleteStudy = async () => {
    if (!confirm(`Удалить исследование «${study?.title}» целиком со всеми файлами?`)) return;
    await api(`/admin/studies/${id}`, { method: "DELETE" });
    router.push("/super/studies");
  };

  if (!study) {
    return (
      <SuperTabs>
        <p className="muted">Загрузка…</p>
      </SuperTabs>
    );
  }

  return (
    <SuperTabs>
      <div className="toolbar">
        <button className="btn btn-sm" onClick={() => router.push("/super/studies")}>← К списку</button>
        <div className="spacer" />
        <button className="btn btn-sm btn-danger" onClick={deleteStudy}>Удалить исследование</button>
      </div>

      <div className="card" style={{ maxWidth: 680 }}>
        <h2>Параметры</h2>
        <div className="field">
          <label>Название</label>
          <input value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div className="field">
          <label>Дата проведения</label>
          <input type="date" value={conductedAt} onChange={(e) => setConductedAt(e.target.value)} />
        </div>
        <div className="field">
          <label>Комментарий</label>
          <textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} />
        </div>
        {error && <p className="error-text">{error}</p>}
        <button className="btn btn-primary" onClick={saveMeta} disabled={saving}>
          {saving ? "Сохраняем…" : "Сохранить"}
        </button>
      </div>

      <div className="card">
        <h2>Файлы</h2>
        <table className="table" style={{ marginBottom: 14 }}>
          <thead>
            <tr>
              <th>Файл</th>
              <th>Комментарий</th>
              <th>Размер</th>
              <th>Статус</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {(study.files || []).map((f) => {
              const badge = STATUS_BADGES[f.status] || STATUS_BADGES.ready;
              return (
                <tr key={f.id}>
                  <td>
                    <a href={`${API}/files/${f.id}/download`}>{f.original_name}</a>
                    <div className="muted small">{KIND_LABELS[f.kind] || f.kind}</div>
                  </td>
                  <td>
                    <span className={f.comment ? "" : "muted small"}>
                      {f.comment || "нет"}
                    </span>{" "}
                    <button className="icon-btn" title="Изменить комментарий" onClick={() => editFileComment(f)}>
                      ✎
                    </button>
                  </td>
                  <td className="muted">{formatBytes(f.size_bytes)}</td>
                  <td>
                    <span className={badge.cls}>{badge.label}</span>
                    {f.status === "error" && (
                      <div className="error-text small" style={{ marginTop: 4 }}>{f.error_message}</div>
                    )}
                  </td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {f.status === "error" && (
                      <button className="btn btn-sm" onClick={() => retryFile(f)} style={{ marginRight: 6 }}>
                        Повторить
                      </button>
                    )}
                    <button className="btn btn-sm btn-danger" onClick={() => deleteFile(f)}>Удалить</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        <h2 style={{ marginTop: 8 }}>Добавить файлы</h2>
        <FilePicker items={newFiles} onChange={setNewFiles} disabled={progress !== null} />
        {newFiles.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <button className="btn btn-primary" onClick={uploadNewFiles} disabled={progress !== null}>
              Загрузить и обработать
            </button>
          </div>
        )}
        {progress !== null && (
          <div style={{ maxWidth: 400, marginTop: 10 }}>
            <span className="muted small">Загрузка: {progress}%</span>
            <div className="progress-track">
              <div className="progress-fill" style={{ width: `${progress}%` }} />
            </div>
          </div>
        )}
      </div>
    </SuperTabs>
  );
}
