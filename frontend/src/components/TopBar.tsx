// Üst çubuk. Rapor açıkken (tasarım / görüntüleme) sade rapor çubuğu: ← Envanter · rapor adı · statü ·
// Düzenle | Görüntüle · ⋯ (başka rapor, adlandır, sil) · Yayınla · bağlantı noktası. Diğer sayfalarda uygulama modu,
// konum ve bağlantı durumu. Tema seçimi sol menünün altındadır.
import { useEffect, useRef, useState } from "react";
import type { Health, SessionSummary } from "../types";
import { HealthBadge } from "./HealthBadge";
import { PHASES } from "./Chat";
import { StatusPicker } from "./StatusPicker";
import { ConfirmDialog } from "./ConfirmDialog";
import "./vitrin.css";

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
  settings: ["Sistem", "Bağlantı Ayarları"], admin: ["Sistem", "Yönetim"],
  vitrin: ["Vitrin", "Yayınlanmış raporlar"], "vitrin-report": ["Vitrin", "Yayınlanmış raporlar"],
};

const PencilIcon = ({ size = 13 }: { size?: number }) => (
  <svg viewBox="0 0 16 16" width={size} height={size} aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M3 11.5V13h1.5l7-7L10 4.5l-7 7ZM9.5 5 11 6.5" /></svg>
);

interface TopBarProps {
  /** uygulama modu: vitrin (yayınlar) | design (tasarım çalışma alanı); onAppMode yoksa anahtar gösterilmez (izleyici) */
  appMode?: "vitrin" | "design";
  onAppMode?: (m: "vitrin" | "design") => void;
  /** Vitrin'e yayınla (rapor açıkken, dashboard varsa) */
  onPublish?: () => void;
  published?: SessionSummary["published"];
  health: Health | null;
  healthError: string | null;
  onHealthClick: () => void;
  status?: string | null;
  onStatus: (s: string) => void;
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
}

export function TopBar(p: TopBarProps) {
  const { page, view, appMode = "design", onAppMode, busy, health, healthError, onHealthClick, currentId } = p;
  if ((view === "designer" || view === "viewer") && currentId) return <ReportBar {...p} currentId={currentId} />;

  const [section, pageLabel] = PAGE_CRUMBS[page] ?? PAGE_CRUMBS.home;
  return (
    <header className="topbar">
      {onAppMode ? (
        <div className="app-mode-seg" role="group" aria-label="Uygulama modu">
          <button type="button" className={appMode === "vitrin" ? "is-on" : undefined} aria-pressed={appMode === "vitrin"} disabled={busy}
            onClick={() => onAppMode("vitrin")} title="Vitrin: yayınlanmış raporları gör">
            <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M2 6.5h12M3 6.5V13.5h10V6.5M2 6.5l1.5-4h9l1.5 4M6.5 13.5V10h3v3.5" /></svg>
            <span>Vitrin</span>
          </button>
          <button type="button" className={appMode === "design" ? "is-on" : undefined} aria-pressed={appMode === "design"} disabled={busy}
            onClick={() => onAppMode("design")} title="Tasarım: rapor tasarla, veri ve ayarlar">
            <PencilIcon />
            <span>Tasarım</span>
          </button>
        </div>
      ) : null}
      <nav className="crumbs" aria-label="Konum">
        <span className="crumb"><span className="crumb-muted">{section}</span></span>
        <span className="crumb"><span className="crumb-sep" aria-hidden="true">/</span><span className="crumb-current">{pageLabel}</span></span>
      </nav>
      <div className="topbar-spacer" />
      {appMode === "design" ? <HealthBadge health={health} healthError={healthError} inline onClick={onHealthClick} /> : null}
    </header>
  );
}

function ReportBar({ onMode, canView, sessions, currentId, currentTitle, onSelect, onDelete, busy, view, onHome, onRename, status, onStatus,
  health, healthError, onHealthClick, onPublish, published }: TopBarProps & { currentId: string }) {
  const [menu, setMenu] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState("");
  const [askDelete, setAskDelete] = useState<SessionSummary | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menu) return;
    const h = (e: MouseEvent) => menuRef.current && !menuRef.current.contains(e.target as Node) && setMenu(false);
    const k = (e: KeyboardEvent) => e.key === "Escape" && setMenu(false);
    document.addEventListener("mousedown", h);
    document.addEventListener("keydown", k);
    return () => {
      document.removeEventListener("mousedown", h);
      document.removeEventListener("keydown", k);
    };
  }, [menu]);

  const current = sessions.find((s) => s.id === currentId);
  const title = currentTitle || current?.title || "Başlıksız rapor";
  const editable = view === "designer";
  const startRename = () => { if (!busy && editable) { setMenu(false); setDraft(title); setRenaming(true); } };
  const others = sessions.filter((s) => s.id !== currentId);

  return (
    <header className="topbar topbar-report">
      <button type="button" className="rb-back" onClick={onHome} disabled={busy} title="Rapor Envanteri'ne dön" aria-label="Rapor Envanteri'ne dön">
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M10 3.5 5.5 8 10 12.5" /></svg>
        <span className="rb-back-label">Envanter</span>
      </button>
      <span className="rb-sep" aria-hidden="true" />

      {renaming ? (
        <form className="title-rename" onSubmit={async (e) => { e.preventDefault(); if (await onRename(draft.trim())) setRenaming(false); }}>
          <input autoFocus onFocus={(e) => e.currentTarget.select()} className="dict-input" value={draft} maxLength={120} onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && setRenaming(false)} aria-label="Rapor adı" />
          <button type="submit" className="btn btn-primary btn-sm" disabled={!draft.trim()}>Kaydet</button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setRenaming(false)}>Vazgeç</button>
        </form>
      ) : editable ? (
        <button type="button" className="rb-title" onClick={startRename} disabled={busy} title="Yeniden adlandırmak için tıklayın">
          <span className="rb-title-text">{title}</span>
          <PencilIcon size={12} />
        </button>
      ) : (
        <span className="rb-title is-static" title={title}><span className="rb-title-text">{title}</span></span>
      )}
      {renaming ? null : <StatusPicker value={status} onChange={(s) => onStatus(s)} disabled={busy} />}

      <div className="topbar-spacer" />

      {onMode ? (
        <div className="mode-seg" role="group" aria-label="Rapor görünümü">
          <button type="button" className={view === "designer" ? "is-on" : undefined} onClick={() => onMode("designer")} disabled={busy} aria-pressed={view === "designer"}
            title="Düzenle: agent ile sohbet, İhtiyaç / Veri / Tasarım adımları">
            <PencilIcon />
            <span className="mode-label">Düzenle</span>
          </button>
          <button type="button" className={view === "viewer" ? "is-on" : undefined} onClick={() => onMode("viewer")} disabled={busy || !canView} aria-pressed={view === "viewer"}
            title={canView ? "Görüntüle: raporu son kullanıcı gibi tam sayfa gör" : "Henüz dashboard yok"}>
            <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8Z" /><circle cx="8" cy="8" r="2" /></svg>
            <span className="mode-label">Görüntüle</span>
          </button>
        </div>
      ) : null}

      <div className="rb-more" ref={menuRef}>
        <button type="button" className="rb-icon-btn" onClick={() => setMenu((o) => !o)} aria-haspopup="menu" aria-expanded={menu} aria-label="Rapor menüsü" title="Rapor menüsü">
          <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" fill="currentColor"><circle cx="3.5" cy="8" r="1.4" /><circle cx="8" cy="8" r="1.4" /><circle cx="12.5" cy="8" r="1.4" /></svg>
        </button>
        {menu ? (
          <div className="menu rb-menu" role="menu" aria-label="Rapor menüsü">
            {editable ? (
              <button type="button" role="menuitem" className="rb-menu-item" onClick={startRename} disabled={busy}>
                <PencilIcon />Yeniden adlandır
              </button>
            ) : null}
            <button type="button" role="menuitem" className="rb-menu-item is-danger" disabled={busy}
              onClick={() => { setMenu(false); if (current) setAskDelete(current); else setAskDelete({ id: currentId, title, phase: "requirements", updatedAt: "" }); }}>
              <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" /></svg>
              Raporu sil
            </button>
            <div className="menu-head">Başka rapor aç</div>
            <div className="menu-list" role="group" aria-label="Başka rapor aç">
              {others.map((s) => (
                <div key={s.id} className="menu-item">
                  <button type="button" role="menuitem" className="menu-main" onClick={() => { onSelect(s.id); setMenu(false); }} disabled={busy}>
                    <span className="menu-title">{s.title || "Başlıksız"}</span>
                    <span className="menu-sub">{PHASES.find((x) => x.id === s.phase)?.label ?? s.phase} · {relTime(s.updatedAt)}</span>
                  </button>
                </div>
              ))}
              {!others.length ? <div className="menu-empty">Başka rapor yok</div> : null}
            </div>
          </div>
        ) : null}
      </div>

      {onPublish ? (
        <button type="button" className="btn btn-primary btn-sm topbar-publish" onClick={onPublish} disabled={busy || !canView}
          title={!canView ? "Önce dashboard oluşturun" : published?.status === "active" ? `Vitrin'de yayında (sürüm ${published.version}) — yeni sürüm yayınla / paylaşımı düzenle` : "Vitrin'e yayınla ve paylaş"}>
          <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M8 10.5V2.5M4.5 6 8 2.5 11.5 6M3 10v3.5h10V10" /></svg>
          <span>{published?.status === "active" ? `Yayında · v${published.version}` : "Yayınla"}</span>
        </button>
      ) : null}
      <span className="rb-health"><HealthBadge health={health} healthError={healthError} compact onClick={onHealthClick} /></span>

      {askDelete ? (
        <ConfirmDialog danger title="Rapor silinsin mi?" confirmLabel="Evet, sil"
          message={`"${askDelete.title || "Başlıksız"}" raporu ve tüm konuşma geçmişi kalıcı olarak silinecek. Bu işlem geri alınamaz. Emin misiniz?`}
          onCancel={() => setAskDelete(null)} onConfirm={() => { const id = askDelete.id; setAskDelete(null); onDelete(id); }} />
      ) : null}
    </header>
  );
}
