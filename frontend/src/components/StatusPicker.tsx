// Rapor statüsü seçici: renkli rozet + sayfayla uyumlu açılır menü (tarayıcının yerel <select>'i yerine).
// Menü body'ye portal ile açılır: kart / tablo gibi taşmayı gizleyen kapların içinde kırpılmaz.
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { STATUSES, statusInfo, type ReportStatus } from "../lib/status";

const MENU_W = 270;

export function StatusPicker({ value, onChange, disabled, align = "right", size = "md" }: {
  value?: string | null;
  onChange: (s: ReportStatus) => void;
  disabled?: boolean;
  align?: "left" | "right";
  size?: "sm" | "md";
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const cur = statusInfo(value);

  useLayoutEffect(() => {
    if (!open || !btn.current) return;
    const place = () => {
      const r = btn.current!.getBoundingClientRect();
      const left = align === "right" ? r.right - MENU_W : r.left;
      const h = menu.current?.offsetHeight ?? 260;
      const below = r.bottom + 6;
      const top = below + h > window.innerHeight - 8 ? Math.max(8, r.top - 6 - h) : below;
      setPos({ top, left: Math.min(Math.max(8, left), window.innerWidth - MENU_W - 8) });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => { window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); };
  }, [open, align]);

  useEffect(() => {
    if (!open) return;
    const down = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!btn.current?.contains(t) && !menu.current?.contains(t)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("mousedown", down); document.removeEventListener("keydown", key); };
  }, [open]);

  const stop = (e: { stopPropagation: () => void }) => e.stopPropagation();
  return (
    <div className={`sp sp-${size}`} onClick={stop} onKeyDown={stop}>
      <button ref={btn} type="button" className="sp-btn" style={{ ["--st" as string]: cur.color }} disabled={disabled}
        onClick={() => setOpen((o) => !o)} aria-haspopup="listbox" aria-expanded={open} title={`Statü: ${cur.label} — ${cur.hint}`}>
        <i className="sp-dot" />
        <span className="sp-label">{cur.label}</span>
        <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true"><path d="M3 4.5 6 7.5l3-3" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </button>
      {open ? createPortal(
        <div ref={menu} className="sp-menu" role="listbox" aria-label="Rapor statüsü" onClick={stop} onKeyDown={stop}
          style={{ top: pos?.top ?? -9999, left: pos?.left ?? -9999, width: MENU_W }}>
          <div className="sp-head">Rapor statüsü</div>
          {STATUSES.map((s) => (
            <button key={s.id} type="button" role="option" aria-selected={s.id === cur.id}
              className={`sp-item${s.id === cur.id ? " is-on" : ""}`} style={{ ["--st" as string]: s.color }}
              onClick={() => { setOpen(false); if (s.id !== cur.id) onChange(s.id); }}>
              <i className="sp-dot" />
              <span className="sp-item-text">
                <span className="sp-item-label">{s.label}</span>
                <span className="sp-item-hint">{s.hint}</span>
              </span>
              {s.id === cur.id ? (
                <svg className="sp-check" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.5 8.5 6.5 11.5 12.5 4.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
              ) : null}
            </button>
          ))}
        </div>, document.body) : null}
    </div>
  );
}
