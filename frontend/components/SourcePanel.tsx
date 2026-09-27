"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API, api, ApiError, formatDate, formatTimecode } from "@/lib/api";
import type { Citation, FileMeta, TranscriptSegment } from "@/lib/types";
import { KIND_LABELS } from "@/lib/types";
import { useSquircle } from "@/lib/useSquircle";

export default function SourcePanel({ citation, onClose }: { citation: Citation; onClose: () => void }) {
  const [meta, setMeta] = useState<FileMeta | null>(null);
  const [deleted, setDeleted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [showSnippet, setShowSnippet] = useState(false);
  const [closing, setClosing] = useState(false);
  const squircleRef = useSquircle<HTMLDivElement>(24);

  const close = () => {
    setClosing(true);
    window.setTimeout(onClose, 180);
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setDeleted(false);
      setMeta(null);
      setShowSnippet(false);
      if (!citation.file_id) {
        setDeleted(true);
        setLoading(false);
        return;
      }
      try {
        const m = await api<FileMeta>(`/files/${citation.file_id}/meta`);
        if (!cancelled) setMeta(m);
      } catch (err) {
        if (!cancelled && err instanceof ApiError && err.status === 404) setDeleted(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [citation]);

  const locatorLabel = () => {
    const l = citation.locator;
    if (l.page) return `страница ${l.page}`;
    if (l.row_from) return `строки ${l.row_from}–${l.row_to}`;
    if (l.t_start !== undefined)
      return `${formatTimecode(l.t_start)}–${formatTimecode(l.t_end ?? l.t_start)}`;
    return "";
  };

  return (
    <div className={`source-panel${closing ? " closing" : ""}`} ref={squircleRef}>
      <div className="source-panel-head">
        <div className="titles">
          <div className="study-title">{citation.study_title}</div>
          <div className="file-line">
            {formatDate(citation.conducted_at)} · {KIND_LABELS[citation.file_kind] || citation.file_kind} ·{" "}
            {citation.file_name}
            {locatorLabel() && <> · {locatorLabel()}</>}
            {citation.snippet && (
              <button
                className={`quote-toggle${showSnippet ? " on" : ""}`}
                onClick={() => setShowSnippet((v) => !v)}
                title={showSnippet ? "Скрыть цитируемый фрагмент" : "Показать цитируемый фрагмент"}
              >
                ❝
              </button>
            )}
          </div>
          {meta?.comment && <div className="file-line">Пометка: {meta.comment}</div>}
        </div>
        <div className="head-actions">
          {meta && (
            <a className="icon-btn" href={`${API}/files/${meta.id}/download`} title="Скачать исходник">
              <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
                <path d="M10 3v9m0 0l-3.5-3.5M10 12l3.5-3.5M4 16h12" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </a>
          )}
          <button className="icon-btn" onClick={close} title="Закрыть">✕</button>
        </div>
      </div>

      <div className="source-panel-body">
        {loading && <p className="muted" style={{ padding: 16 }}>Загрузка…</p>}
        {!loading && deleted && (
          <div className="deleted-source">
            <div className="big">🗑</div>
            <p><b>Источник удален</b></p>
            <p className="small">Файл «{citation.file_name}» больше не доступен в базе исследований.</p>
            {citation.snippet && <div className="snippet-quote" style={{ textAlign: "left" }}>{citation.snippet}</div>}
          </div>
        )}
        {!loading && meta && (
          <>
            {citation.snippet && (
              <div className={`snippet-wrap${showSnippet ? " open" : ""}`}>
                <div className="snippet-clip">
                  <div className="snippet-quote">{citation.snippet}</div>
                </div>
              </div>
            )}
            {meta.kind === "report_pdf" && <PdfView fileId={meta.id} page={citation.locator.page} />}
            {meta.kind === "raw_csv" && <CsvView fileId={meta.id} citation={citation} />}
            {(meta.kind === "audio" || meta.kind === "video") && (
              <MediaView meta={meta} citation={citation} />
            )}
          </>
        )}
      </div>
    </div>
  );
}

function PdfView({ fileId, page }: { fileId: string; page?: number }) {
  return (
    <iframe
      src={`${API}/files/${fileId}/content#page=${page || 1}&toolbar=0&navpanes=0`}
      title="PDF"
    />
  );
}

function CsvView({ fileId, citation }: { fileId: string; citation: Citation }) {
  const [data, setData] = useState<{ header: string[]; rows: { n: number; cells: string[] }[]; total: number } | null>(null);
  const [error, setError] = useState("");
  const from = citation.locator.row_from ?? 1;
  const to = citation.locator.row_to ?? from + 20;

  useEffect(() => {
    const ctx = 3; // строки контекста вокруг цитируемого диапазона
    api<{ header: string[]; rows: { n: number; cells: string[] }[]; total: number }>(
      `/files/${fileId}/csv?row_from=${Math.max(1, from - ctx)}&row_to=${to + ctx}`
    )
      .then(setData)
      .catch(() => setError("Не удалось загрузить фрагмент таблицы"));
  }, [fileId, from, to]);

  if (error) return <p className="error-text" style={{ padding: 16 }}>{error}</p>;
  if (!data) return <p className="muted" style={{ padding: 16 }}>Загрузка…</p>;

  return (
    <div className="csv-wrap">
      <p className="muted small" style={{ marginBottom: 8 }}>
        Показаны строки {data.rows[0]?.n}–{data.rows[data.rows.length - 1]?.n} из {data.total}. Подсвечен
        цитируемый диапазон.
      </p>
      <table>
        <thead>
          <tr>
            <th className="rownum">#</th>
            {data.header.map((h, i) => (
              <th key={i}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row) => (
            <tr key={row.n} className={row.n >= from && row.n <= to ? "hl" : ""}>
              <td className="rownum">{row.n}</td>
              {row.cells.map((cell, i) => (
                <td key={i}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MediaView({ meta, citation }: { meta: FileMeta; citation: Citation }) {
  const mediaRef = useRef<HTMLVideoElement | HTMLAudioElement | null>(null);
  const [segments, setSegments] = useState<TranscriptSegment[] | null>(null);
  const hlRef = useRef<HTMLButtonElement | null>(null);
  const tStart = citation.locator.t_start ?? 0;
  const tEnd = citation.locator.t_end ?? tStart;

  useEffect(() => {
    if (!meta.has_transcript) return;
    api<{ segments: TranscriptSegment[] }>(`/files/${meta.id}/transcript`)
      .then((t) => setSegments(t.segments))
      .catch(() => setSegments(null));
  }, [meta.id, meta.has_transcript]);

  const seek = useCallback((t: number) => {
    const el = mediaRef.current;
    if (el) {
      el.currentTime = t;
      el.play().catch(() => {/* автоплей запрещен — не страшно */});
    }
  }, []);

  // Перемотка на таймкод цитаты при открытии
  useEffect(() => {
    const el = mediaRef.current;
    if (!el) return;
    const setTime = () => { el.currentTime = tStart; };
    if (el.readyState >= 1) setTime();
    else el.addEventListener("loadedmetadata", setTime, { once: true });
    return () => el.removeEventListener("loadedmetadata", setTime);
  }, [tStart, meta.id]);

  useEffect(() => {
    hlRef.current?.scrollIntoView({ block: "center" });
  }, [segments, tStart]);

  const src = `${API}/files/${meta.id}/content`;
  const isHl = (s: TranscriptSegment) => s.t_end >= tStart && s.t_start <= tEnd;
  let hlAssigned = false;

  return (
    <>
      <div className="media-box">
        {meta.kind === "video" ? (
          <video ref={(el) => { mediaRef.current = el; }} src={src} controls preload="metadata" />
        ) : (
          <audio ref={(el) => { mediaRef.current = el; }} src={src} controls preload="metadata" />
        )}
      </div>
      {segments && (
        <div className="transcript-list">
          {segments.map((seg, i) => {
            const hl = isHl(seg);
            const first = hl && !hlAssigned;
            if (first) hlAssigned = true;
            return (
              <button
                key={i}
                ref={first ? hlRef : undefined}
                className={`transcript-seg${hl ? " hl" : ""}`}
                onClick={() => seek(seg.t_start)}
              >
                <span className="tc">{formatTimecode(seg.t_start)}</span>
                <span>{seg.text}</span>
              </button>
            );
          })}
        </div>
      )}
      {!segments && meta.has_transcript && <p className="muted" style={{ padding: 16 }}>Загрузка транскрипта…</p>}
    </>
  );
}
