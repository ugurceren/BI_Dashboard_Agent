// Üst çubuk: uygulama adı, oturum seçici, sağlık göstergesi.
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { SessionSummary } from "../types";
import { PHASES } from "./Chat";
import { StatusPicker } from "./StatusPicker";
import { ConfirmDialog } from "./ConfirmDialog";

export type ThemePref = "light" | "dark";

function relTime(iso: string): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 60) return "az önce";
  if (s < 3600) return `${Math.floor(s / 60)} dk önce`;
  if (s < 86400) return `${Math.floor(s / 3600)} sa önce`;
  return new Date(t).toLocaleDateString("tr-TR", { day: "numeric", month: "short" });
}

const PAGE_CRUMBS: Record<string, [string, string]> = {
  home: ["Raporlar", "Rapor Envanteri"], designer: ["Raporlar", "Rapor Envanteri"], viewer: ["Raporlar", "Rapor Envanteri"],
  access: ["Veri", "Veri Erişimim"], query: ["Veri", "Sorgu Çalıştır"], model: ["Veri", "Veri Modeli"],
  settings: ["Sistem", "Bağlantı Ayarları"],
};

export function TopBar({ onMode, canView, page, mock, sessions, currentId, currentTitle, onSelect, onDelete, busy, view, onHome, onRename, status, onStatus, theme, onTheme }: {
  status?: string | null;
  onStatus: (s: string) => void;
  theme: ThemePref;
  onTheme: (t: ThemePref) => void;
  view: "home" | "designer" | "viewer";
  onMode?: (m: "designer" | "viewer") => void;
  canView?: boolean;
  onHome: () => void;
  onRename: (title: string) => Promise<boolean>;
  page: string;
  mock: boolean;
  sessions: SessionSummary[];
  currentId: string | null;
  currentTitle?: string;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState("");
  const [askDelete, setAskDelete] = useState<SessionSummary | null>(null);
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

  const [section, pageLabel] = PAGE_CRUMBS[page] ?? PAGE_CRUMBS.home;
  const inReport = view === "designer" || view === "viewer";
  const crumbs: { label: string; onClick?: () => void }[] = inReport
    ? [{ label: section }, { label: pageLabel, onClick: onHome }]
    : [{ label: section }, { label: pageLabel }];
  const title = currentTitle || sessions.find((s) => s.id === currentId)?.title || "Oturum";

  return (
    <header className="topbar">
      <div className="brand" style={{ display: "none" }}>
        <span className="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 16 16" width="14" height="14"><path d="M3 13V9M6.5 13V5M10 13V7.5M13.5 13V3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
        </span>
        <span className="brand-name">BI Lens</span>
        {mock ? <span className="pill pill-warn" title="Backend olmadan sahte veriyle çalışıyor">mock</span> : null}
      </div>

      <nav className="crumbs" aria-label="Konum">
        {crumbs.map((c, i) => (
          <span key={i} className="crumb">
            {i ? <span className="crumb-sep" aria-hidden="true">/</span> : null}
            {c.onClick ? <button type="button" className="crumb-link" onClick={c.onClick} disabled={busy}>{c.label}</button>
              : <span className={i === crumbs.length - 1 && !inReport ? "crumb-current" : "crumb-muted"}>{c.label}</span>}
          </span>
        ))}
        {inReport ? <span className="crumb-sep" aria-hidden="true">/</span> : null}
      </nav>

      {inReport && renaming ? (
        <form className="title-rename" onSubmit={async (e) => { e.preventDefault(); if (await onRename(draft.trim())) setRenaming(false); }}>
          <input autoFocus onFocus={(e) => e.currentTarget.select()} className="dict-input" value={draft} maxLength={120} onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && setRenaming(false)} aria-label="Rapor adı" />
          <button type="submit" className="btn btn-primary btn-sm" disabled={!draft.trim()}>Kaydet</button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setRenaming(false)}>Vazgeç</button>
        </form>
      ) : null}
      <div className="session-picker" ref={ref} style={view === "home" || renaming ? { display: "none" } : undefined}>
        <button type="button" className="session-btn" onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open} disabled={!sessions.length && !currentId}
          onDoubleClick={() => { if (!busy && currentId) { setOpen(false); setDraft(title); setRenaming(true); } }} title="Rapor listesi · çift tıkla: yeniden adlandır">
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /></svg>
          <span className="session-title">{title}</span>
          <svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true"><path d="M3 4.5 6 7.5l3-3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>
        {view === "viewer" ? null : <button type="button" className="btn btn-secondary btn-sm topbar-rename" onClick={() => { setDraft(title); setRenaming(true); }} disabled={busy || !currentId} title="Raporu yeniden adlandır">
          <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M3 11.5V13h1.5l7-7L10 4.5l-7 7ZM9.5 5 11 6.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /></svg>
          <span>Adlandır</span>
        </button>}
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
                    onClick={() => { setOpen(false); setAskDelete(s); }}
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

      {inReport && currentId && onMode ? (
        <div className="mode-seg" role="group" aria-label="Rapor görünümü">
          <button type="button" className={view === "designer" ? "is-on" : undefined} onClick={() => onMode("designer")} disabled={busy} aria-pressed={view === "designer"}
            title="Tasarım modu: agent ile sohbet, İhtiyaç / Veri / Tasarım adımları">
            <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M3 11.5V13h1.5l7-7L10 4.5l-7 7ZM9.5 5 11 6.5" /></svg>
            <span className="mode-label">Tasarım</span>
          </button>
          <button type="button" className={view === "viewer" ? "is-on" : undefined} onClick={() => onMode("viewer")} disabled={busy || !canView} aria-pressed={view === "viewer"}
            title={canView ? "Canlı görünüm: raporu tam sayfa gör" : "Henüz dashboard yok"}>
            <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8Z" /><circle cx="8" cy="8" r="2" /></svg>
            <span className="mode-label">Canlı</span>
          </button>
        </div>
      ) : null}
      <div className="topbar-spacer" />
      {inReport && currentId ? <StatusPicker value={status} onChange={(s) => onStatus(s)} disabled={busy} align="right" /> : null}
      <div className="theme-seg" role="group" aria-label="Görünüm">
        {([["light", "Gündüz modu", <path key="l" d="M8 5.2a2.8 2.8 0 1 0 0 5.6 2.8 2.8 0 0 0 0-5.6ZM8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1" />],
           ["dark", "Gece modu", <path key="d" d="M13 9.6A5.5 5.5 0 0 1 6.4 3a5.5 5.5 0 1 0 6.6 6.6Z" />]] as [ThemePref, string, ReactNode][]).map(([k, l, icon]) => (
          <button key={k} type="button" className={theme === k ? "is-on" : undefined} onClick={() => onTheme(k)} title={l} aria-label={l} aria-pressed={theme === k}>
            <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{icon}</svg>
          </button>
        ))}
      </div>
      {askDelete ? (
        <ConfirmDialog danger title="Rapor silinsin mi?" confirmLabel="Evet, sil"
          message={`"${askDelete.title || "Başlıksız"}" raporu ve tüm konuşma geçmişi kalıcı olarak silinecek. Bu işlem geri alınamaz. Emin misiniz?`}
          onCancel={() => setAskDelete(null)} onConfirm={() => { const id = askDelete.id; setAskDelete(null); onDelete(id); }} />
      ) : null}
    </header>
  );
}
