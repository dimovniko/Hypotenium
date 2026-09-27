"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import SuperTabs, { STATUS_BADGES } from "@/components/SuperTabs";
import { api, formatDate } from "@/lib/api";
import type { Study } from "@/lib/types";

type ModelsInfo = {
  needs_reindex: boolean;
  chunks_outdated: number;
  chunks_total: number;
  slots: { embedding?: { model?: string } };
};

export default function StudiesPage() {
  const router = useRouter();
  const [studies, setStudies] = useState<Study[] | null>(null);
  const [models, setModels] = useState<ModelsInfo | null>(null);
  const [search, setSearch] = useState("");
  const [reindexing, setReindexing] = useState(false);

  const load = () => {
    api<Study[]>("/admin/studies").then(setStudies).catch(() => setStudies([]));
    api<ModelsInfo>("/admin/settings/models").then(setModels).catch(() => {});
  };

  useEffect(load, []);

  // Пока есть обрабатывающиеся исследования — обновляем список
  useEffect(() => {
    if (!studies?.some((s) => s.status === "processing")) return;
    const t = setTimeout(load, 3000);
    return () => clearTimeout(t);
  }, [studies]);

  const reindex = async () => {
    if (!confirm("Запустить переиндексацию всех чанков текущей embedding-моделью?")) return;
    setReindexing(true);
    await api("/admin/reindex", { method: "POST" });
    alert("Переиндексация запущена в фоне. Прогресс виден по убыванию счетчика устаревших чанков.");
    setReindexing(false);
    load();
  };

  const filtered = (studies || []).filter((s) =>
    s.title.toLowerCase().includes(search.trim().toLowerCase())
  );

  return (
    <SuperTabs>
      {models?.needs_reindex && (
        <div className="banner banner-warn">
          <span>
            ⚠ Embedding-модель изменилась ({models.slots.embedding?.model}). Устаревших чанков:{" "}
            {models.chunks_outdated} из {models.chunks_total} — они не участвуют в поиске, пока не
            переиндексированы.
          </span>
          <button className="btn btn-sm" onClick={reindex} disabled={reindexing}>
            Переиндексировать
          </button>
        </div>
      )}

      <div className="toolbar">
        <input
          placeholder="Поиск по названию…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ padding: "8px 12px", border: "1px solid var(--border)", borderRadius: 8, width: 260 }}
        />
        <div className="spacer" />
        <Link href="/super/studies/new" className="btn btn-primary">
          + Добавить исследование
        </Link>
      </div>

      {studies === null ? (
        <p className="muted">Загрузка…</p>
      ) : filtered.length === 0 ? (
        <p className="muted">{search ? "Ничего не найдено." : "Исследований пока нет — добавьте первое."}</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Название</th>
              <th>Дата проведения</th>
              <th>Файлов</th>
              <th>Статус</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((s) => {
              const badge = STATUS_BADGES[s.status] || STATUS_BADGES.ready;
              return (
                <tr key={s.id} className="clickable" onClick={() => router.push(`/super/studies/${s.id}`)}>
                  <td>{s.title}</td>
                  <td>{formatDate(s.conducted_at)}</td>
                  <td>{s.files_count}</td>
                  <td><span className={badge.cls}>{badge.label}</span></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </SuperTabs>
  );
}
