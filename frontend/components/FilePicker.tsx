"use client";

import { formatBytes } from "@/lib/api";

export const FILE_ACCEPT = ".pdf,.csv,.mp3,.wav,.m4a,.ogg,.flac,.aac,.mp4,.mov,.webm,.mkv,.avi";

export type PickedFile = { file: File; comment: string };

/** Накопительный выбор файлов: можно добавлять по одному или пачками,
 *  к каждому файлу — комментарий для более точной разметки в поиске. */
export default function FilePicker({
  items,
  onChange,
  disabled,
}: {
  items: PickedFile[];
  onChange: (items: PickedFile[]) => void;
  disabled?: boolean;
}) {
  const add = (list: FileList | null) => {
    if (!list) return;
    const added = Array.from(list).map((file) => ({ file, comment: "" }));
    onChange([...items, ...added]);
  };

  const setComment = (index: number, comment: string) => {
    onChange(items.map((it, i) => (i === index ? { ...it, comment } : it)));
  };

  const remove = (index: number) => {
    onChange(items.filter((_, i) => i !== index));
  };

  return (
    <div className="file-picker">
      {items.map((it, i) => (
        <div key={`${it.file.name}-${i}`} className="file-pick-row">
          <div className="file-pick-info">
            <span className="file-pick-name" title={it.file.name}>{it.file.name}</span>
            <span className="muted small">{formatBytes(it.file.size)}</span>
            <button
              type="button"
              className="icon-btn"
              title="Убрать файл"
              onClick={() => remove(i)}
              disabled={disabled}
            >
              ✕
            </button>
          </div>
          <input
            className="file-pick-comment"
            placeholder="Комментарий к файлу: что внутри, какой сегмент, кто респондент… (необязательно)"
            value={it.comment}
            onChange={(e) => setComment(i, e.target.value)}
            disabled={disabled}
          />
        </div>
      ))}

      <label className={`btn${disabled ? " disabled" : ""}`} style={{ display: "inline-flex" }}>
        + Добавить файл{items.length > 0 ? "ы" : ""}
        <input
          type="file"
          multiple
          accept={FILE_ACCEPT}
          style={{ display: "none" }}
          disabled={disabled}
          onChange={(e) => {
            add(e.target.files);
            e.target.value = "";
          }}
        />
      </label>
      {items.length > 0 && (
        <span className="muted small" style={{ marginLeft: 10 }}>
          {items.length} файл(ов), {formatBytes(items.reduce((s, it) => s + it.file.size, 0))}
        </span>
      )}
    </div>
  );
}

/** Собирает FormData c параллельными полями files / file_comments. */
export function appendPickedFiles(fd: FormData, items: PickedFile[]) {
  for (const it of items) {
    fd.append("files", it.file);
    fd.append("file_comments", it.comment);
  }
}
