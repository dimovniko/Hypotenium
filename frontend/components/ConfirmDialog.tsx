"use client";

import Squircle from "@/components/Squircle";

/** Диалог подтверждения вместо системного confirm(). */
export default function ConfirmDialog({
  title,
  text,
  confirmLabel = "Удалить",
  onConfirm,
  onCancel,
}: {
  title: string;
  text?: string;
  confirmLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <Squircle radius={16} className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        {text && <p className="muted small">{text}</p>}
        <div className="modal-actions">
          <button className="btn" onClick={onCancel}>Отмена</button>
          <button className="btn btn-danger" onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </Squircle>
    </div>
  );
}
