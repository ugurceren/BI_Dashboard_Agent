// Sol menü: uygulama bölümleri + en altta bağlı kullanıcı (Windows oturumu / LDAP).
import type { ReactNode } from "react";
import type { Me } from "../types";
import "./sidebar.css";

export type Page = "home" | "access" | "model" | "designer";

const ICONS: Record<string, ReactNode> = {
  home: <path d="M2.5 7 8 2.5 13.5 7v6.5h-3.8V9.5H6.3v4H2.5z" />,
  new: <path d="M8 3v10M3 8h10" />,
  designer: <path d="M2.5 13.5h11M4 11V7M8 11V3.5M12 11V6" />,
  access: <path d="M5 7V5a3 3 0 0 1 6 0v2M3.5 7h9v6.5h-9zM8 9.5v2" />,
  model: <path d="M2.5 3.5h4v3h-4zM9.5 3.5h4v3h-4zM6 10h4v3H6zM4.5 6.5v2h7v-2M8 8.5V10" />,
};

function Icon({ name }: { name: string }) {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      {ICONS[name]}
    </svg>
  );
}

const SOURCE: Record<string, string> = {
  ldap: "LDAP / Active Directory", windows: "Windows oturumu", header: "Kurumsal oturum açma (proxy)",
};

function initials(name: string) {
  const parts = name.replace(/^.*\\/, "").split(/[\s._-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toLocaleUpperCase("tr");
}

export function Sidebar({ page, collapsed, onToggle, onNavigate, onNew, me, currentReport, busy }: {
  page: Page;
  collapsed: boolean;
  onToggle: () => void;
  onNavigate: (p: Exclude<Page, "designer">) => void;
  onNew: () => void;
  me: Me | null;
  currentReport: { id: string; title: string } | null;
  busy: boolean;
}) {
  const item = (key: string, label: string, active: boolean, onClick: () => void, disabled = false) => (
    <button type="button" className={`sb-item${active ? " is-on" : ""}`} onClick={onClick} disabled={disabled}
      title={collapsed ? label : undefined} aria-current={active ? "page" : undefined}>
      <Icon name={key} />
      <span className="sb-label">{label}</span>
    </button>
  );
  return (
    <nav className={`sidebar${collapsed ? " is-collapsed" : ""}`} aria-label="Ana menü">
      <div className="sb-brand">
        <span className="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 16 16" width="14" height="14"><path d="M3 13V9M6.5 13V5M10 13V7.5M13.5 13V3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
        </span>
        <span className="brand-name sb-label">BI Rapor Agent</span>
        <button type="button" className="sb-toggle" onClick={onToggle} title={collapsed ? "Menüyü genişlet" : "Menüyü daralt"} aria-label="Menüyü daralt/genişlet">
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
            {collapsed ? <path d="M6 3.5 10.5 8 6 12.5" /> : <path d="M10 3.5 5.5 8 10 12.5" />}
          </svg>
        </button>
      </div>

      <div className="sb-section sb-label">Raporlar</div>
      {item("home", "Rapor Envanteri", page === "home", () => onNavigate("home"), busy)}
      {item("new", "Yeni Rapor", false, onNew, busy)}
      {currentReport ? item("designer", currentReport.title || "Açık rapor", page === "designer", () => { window.location.hash = `#/r/${currentReport.id}`; }, busy) : null}

      <div className="sb-section sb-label">Veri</div>
      {item("access", "Veri Erişimim", page === "access", () => onNavigate("access"), busy)}
      {item("model", "Veri Modeli", page === "model", () => onNavigate("model"), busy)}

      <div className="sb-spacer" />
      <div className="sb-user" title={me ? [me.display_name, me.username, me.title, me.department, `Rol: ${me.role}`,
        `Kaynak: ${SOURCE[me.source] ?? me.source}`, me.domain_joined ? "" : "Bu bilgisayar bir etki alanına bağlı değil (yerel hesap)"].filter(Boolean).join("\n") : "Kullanıcı bilgisi alınıyor…"}>
        <span className="sb-avatar" aria-hidden="true">{me ? initials(me.display_name || me.username) : "…"}</span>
        <span className="sb-user-text sb-label">
          <span className="sb-user-name">{me?.display_name ?? "…"}</span>
          <span className="sb-user-sub">{me ? `${me.username} · ${me.role}` : ""}</span>
        </span>
      </div>
    </nav>
  );
}
