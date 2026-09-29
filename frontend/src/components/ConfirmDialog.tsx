// Uygulama içi onay penceresi (tarayıcının confirm() kutusu bazı ortamlarda otomatik onaylanabiliyor).
import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import "./confirm.css";

export function ConfirmDialog({ title, message, confirmLabel = "Onayla", danger = false, onConfirm, onCancel }: {
  title: string;
  message: string;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancelRef.current?.focus(); // yanlışlıkla Enter'a basınca silinmesin: odak "Vazgeç"te
    const k = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  }, [onCancel]);

  return (
    createPortal(<div className="cd-backdrop" onKeyDown={(e) => e.stopPropagation()} onClick={(e) => { e.stopPropagation(); if (e.target === e.currentTarget) onCancel(); }}>
      <div className="cd-box" role="alertdialog" aria-modal="true" aria-labelledby="cd-title" aria-describedby="cd-msg">
        <div className={`cd-icon${danger ? " is-danger" : ""}`} aria-hidden="true">
          <svg viewBox="0 0 16 16" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            {danger ? <path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5M6.8 7v3.5M9.2 7v3.5" /> : <path d="M8 5v3.5M8 11h.01M8 1.8 14.5 13.5h-13z" />}
          </svg>
        </div>
        <h2 id="cd-title" className="cd-title">{title}</h2>
        <p id="cd-msg" className="cd-msg">{message}</p>
        <div className="cd-actions">
          <button ref={cancelRef} type="button" className="btn btn-secondary" onClick={onCancel}>Vazgeç</button>
          <button type="button" className={`btn ${danger ? "btn-danger" : "btn-primary"}`} onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </div>
    </div>, document.body)
  );
}
