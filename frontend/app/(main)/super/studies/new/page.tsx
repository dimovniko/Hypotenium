"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import FilePicker, { appendPickedFiles, type PickedFile } from "@/components/FilePicker";
import SuperTabs from "@/components/SuperTabs";
import { ApiError, uploadForm } from "@/lib/api";
import type { Study } from "@/lib/types";

export default function NewStudyPage() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [conductedAt, setConductedAt] = useState("");
  const [comment, setComment] = useState("");
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState("");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (files.length === 0) {
      setError("Прикрепите хотя бы один файл");
      return;
    }
    setError("");
    setProgress(0);
    const fd = new FormData();
    fd.append("title", title);
    fd.append("conducted_at", conductedAt);
    fd.append("comment", comment);
    appendPickedFiles(fd, files);
    try {
      const study = await uploadForm<Study>("/admin/studies", fd, setProgress);
      router.push(`/super/studies/${study.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить исследование");
      setProgress(null);
    }
  };

  return (
    <SuperTabs>
      <div className="card" style={{ maxWidth: 680 }}>
        <h2>Новое исследование</h2>
        <form onSubmit={submit}>
          <div className="field">
            <label>Название исследования *</label>
            <input value={title} onChange={(e) => setTitle(e.target.value)} required autoFocus />
          </div>
          <div className="field">
            <label>Дата проведения *</label>
            <input type="date" value={conductedAt} onChange={(e) => setConductedAt(e.target.value)} required />
          </div>
          <div className="field">
            <label>Комментарий к исследованию</label>
            <textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} />
          </div>
          <div className="field">
            <label>
              Файлы-исходники * — PDF-отчет, CSV с сырыми данными, аудио/видео интервью (до 500 МБ каждый).
              Комментарий к файлу попадет в поисковый индекс и поможет точнее находить материал.
            </label>
            <FilePicker items={files} onChange={setFiles} disabled={progress !== null} />
          </div>
          {error && <p className="error-text">{error}</p>}
          {progress !== null && (
            <div style={{ marginBottom: 14 }}>
              <span className="muted small">
                {progress < 100 ? `Загрузка файлов: ${progress}%` : "Файлы загружены, создаем исследование…"}
              </span>
              <div className="progress-track">
                <div className="progress-fill" style={{ width: `${progress}%` }} />
              </div>
            </div>
          )}
          <div style={{ display: "flex", gap: 10 }}>
            <button type="submit" className="btn btn-primary" disabled={progress !== null}>
              Добавить исследование
            </button>
            <button type="button" className="btn" onClick={() => router.push("/super/studies")} disabled={progress !== null}>
              Отмена
            </button>
          </div>
          <p className="muted small" style={{ marginTop: 12 }}>
            После добавления автоматически запустится обработка: транскрибация записей, извлечение
            текста и индексация для поиска.
          </p>
        </form>
      </div>
    </SuperTabs>
  );
}
