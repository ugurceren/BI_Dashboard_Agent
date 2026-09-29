// Üst çubuk: uygulama adı, oturum seçici, sağlık göstergesi.
import { useEffect, useRef, useState } from "react";
import type { Health, SessionSummary } from "../types";
import { PHASES } from "./Chat";

function relTime(iso: string): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 60) return "az önce";
  if (s < 3600) return `${Math.floor(s / 60)} dk önce`;
  if (s < 86400) return `${Math.floor(s / 3600)} sa önce`;
  return new Date(t).toLocaleDateString("tr-TR", { day: "numeric", month: "short" });
}

export function TopBar({ mock, health, healthError, sessions, currentId, currentTitle, onSelect, onNew, onDelete, busy, view, onHome, onRename }: {
  view: "home" | "designer";
  onHome: () => void;
  onRename: (title: string) => Promise<boolean>;
  mock: boolean;
  health: Health | null;
  healthError: string | null;
  sessions: SessionSummary[];
  currentId: string | null;
  currentTitle?: string;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    const k = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", h);
    document.addEventListener("keydown", k);
    return () => {
      document.removeEventListener("mousedown", h);
      document.removeEventListener("keydown", k);
    };
  }, [open]);

  const llmOk = !!health?.llm?.reachable;
  const title = currentTitle || sessions.find((s) => s.id === currentId)?.title || "Oturum";
  const healthTitle = healthError
    ? `Backend'e ulaşılamadı: ${healthError}`
    : health
      ? [
          `LLM: ${health.llm.model} (${health.llm.reachable ? "erişilebilir" : "erişilemiyor"})`,
          health.llm.base_url ? `Adres: ${health.llm.base_url}` : "",
          health.llm.error ? `Hata: ${health.llm.error}` : "",
          `Görsel model: ${health.vision.configured ? health.vision.model : "yapılandırılmamış"}`,
          `Veri: ${health.data.ok ? "bağlı" : "hata"} (${health.data.dialect})`,
        ].filter(Boolean).join("\n")
      : "Kontrol ediliyor…";

  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 16 16" width="14" height="14"><path d="M3 13V9M6.5 13V5M10 13V7.5M13.5 13V3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
        </span>
        <span className="brand-name">BI Rapor Agent</span>
        {mock ? <span className="pill pill-warn" title="Backend olmadan sahte veriyle çalışıyor">mock</span> : null}
      </div>

      {view === "home" ? <div className="topbar-spacer" /> : (
        <button type="button" className="btn btn-ghost btn-sm topbar-back" onClick={onHome} disabled={busy} title="Rapor envanterine dön">
          <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M9.5 3.5 5 8l4.5 4.5" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>
          <span className="btn-label">Raporlar</span>
        </button>
      )}

      {view === "designer" && renaming ? (
        <form className="title-rename" onSubmit={async (e) => { e.preventDefault(); if (await onRename(draft.trim())) setRenaming(false); }}>
          <input autoFocus className="dict-input" value={draft} maxLength={120} onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && setRenaming(false)} aria-label="Rapor adı" />
          <button type="submit" className="btn btn-primary btn-sm" disabled={!draft.trim()}>Kaydet</button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setRenaming(false)}>Vazgeç</button>
        </form>
      ) : null}
      <div className="session-picker" ref={ref} style={view === "home" || renaming ? { display: "none" } : undefined}>
        <button type="button" className="session-btn" onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open} disabled={!sessions.length && !currentId}>
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /></svg>
          <span className="session-title">{title}</span>
          <svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true"><path d="M3 4.5 6 7.5l3-3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setDraft(title); setRenaming(true); }} disabled={busy || !currentId} title="Raporu yeniden adlandır">
          <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M3 11.5V13h1.5l7-7L10 4.5l-7 7ZM9.5 5 11 6.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /></svg>
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onNew} disabled={busy} title="Yeni rapor">
          <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" /></svg>
          <span className="btn-label">Yeni</span>
        </button>
        {open ? (
          <div className="menu" role="menu">
            <div className="menu-head">Oturumlar</div>
            <div className="menu-list">
              {sessions.map((s) => (
                <div key={s.id} className={`menu-item${s.id === currentId ? " is-on" : ""}`} role="menuitem">
                  <button type="button" className="menu-main" onClick={() => { onSelect(s.id); setOpen(false); }} disabled={busy && s.id !== currentId}>
                    <span className="menu-title">{s.title || "Başlıksız"}</span>
                    <span className="menu-sub">
                      {PHASES.find((p) => p.id === s.phase)?.label ?? s.phase} · {relTime(s.updatedAt)}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="menu-del"
                    title="Oturumu sil"
                    aria-label="Oturumu sil"
                    disabled={busy && s.id === currentId}
                    onClick={() => { if (window.confirm(`"${s.title || "Başlıksız"}" oturumu silinsin mi?`)) onDelete(s.id); }}
                  >
                    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" /></svg>
                  </button>
                </div>
              ))}
              {!sessions.length ? <div className="menu-empty">Oturum yok</div> : null}
            </div>
          </div>
        ) : null}
      </div>

      {view === "home" ? (
        <button type="button" className="btn btn-primary btn-sm" onClick={onNew} title="Yeni rapor tasarla">
          <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
          <span>Yeni rapor</span>
        </button>
      ) : null}
      <div className="health" title={healthTitle}>
        <span className={`dot ${healthError ? "bad" : health ? (llmOk ? "ok" : "bad") : "wait"}`} />
        <span className="health-model">{healthError ? "Backend yok" : health ? health.llm.model : "…"}</span>
        {health ? (
          <span className={`health-vision${health.vision.configured ? "" : " is-off"}`}>
            <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8Z" fill="none" stroke="currentColor" strokeWidth="1.3" /><circle cx="8" cy="8" r="2" fill="none" stroke="currentColor" strokeWidth="1.3" />{health.vision.configured ? null : <path d="M2.5 13.5l11-11" stroke="currentColor" strokeWidth="1.3" />}</svg>
            {health.vision.configured ? "Görsel" : "Görsel yok"}
          </span>
        ) : null}
      </div>
    </header>
  );
}
