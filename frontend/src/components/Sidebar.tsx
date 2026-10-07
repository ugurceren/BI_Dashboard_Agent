// Sol menü: uygulama bölümleri + en altta bağlı kullanıcı (Windows oturumu / LDAP).
import type { ReactNode } from "react";
import type { Me, VitrinCard } from "../types";
import { counts, SCOPE_LABEL, type VitrinFilter, type VitrinScope } from "../lib/vitrin";
import "./sidebar.css";

export type Page = "home" | "access" | "model" | "query" | "settings" | "designer" | "viewer" | "vitrin" | "vitrin-report" | "admin";

const ICONS: Record<string, ReactNode> = {
  home: <path d="M2.5 7 8 2.5 13.5 7v6.5h-3.8V9.5H6.3v4H2.5z" />,
  designer: <path d="M2.5 13.5h11M4 11V7M8 11V3.5M12 11V6" />,
  access: <path d="M5 7V5a3 3 0 0 1 6 0v2M3.5 7h9v6.5h-9zM8 9.5v2" />,
  query: <path d="M2.5 3.5h11v9h-11zM5 6.5l2 1.5-2 1.5M8.5 10h2.5" />,
  settings: <path d="M8 10a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4" />,
  model: <path d="M2.5 3.5h4v3h-4zM9.5 3.5h4v3h-4zM6 10h4v3H6zM4.5 6.5v2h7v-2M8 8.5V10" />,
  vitrin: <path d="M2 6.5h12M3 6.5V13.5h10V6.5M2 6.5l1.5-4h9l1.5 4M6.5 13.5V10h3v3.5" />,
  all: <path d="M2.5 2.5h4.5v4.5H2.5zM9 2.5h4.5v4.5H9zM2.5 9h4.5v4.5H2.5zM9 9h4.5v4.5H9z" />,
  shared: <path d="M5.5 7a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM1.5 13c0-2.2 1.8-3.5 4-3.5s4 1.3 4 3.5M11 7.5a1.7 1.7 0 1 0 0-3.4M12 9.6c1.5.3 2.5 1.4 2.5 3.4" />,
  mine: <path d="M8 7.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5ZM3 14c0-2.8 2.2-4.5 5-4.5s5 1.7 5 4.5" />,
  recent: <path d="M8 14.5a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13ZM8 4.5V8l2.5 1.5" />,
  retired: <path d="M2.5 4.5h11v2h-11zM3.5 6.5v7h9v-7M6.5 9h3" />,
  domain: <path d="M2.5 8.5 8.5 2.5h5v5l-6 6zM10.8 5.2h.01" />,
  admin: <path d="M8 1.8 13.5 4v4c0 3-2.3 5.3-5.5 6.2C4.8 13.3 2.5 11 2.5 8V4zM5.8 8l1.6 1.6L10.5 6.5" />,
};

function Icon({ name }: { name: string }) {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      {ICONS[name]}
    </svg>
  );
}

const SCOPE_ICON: Record<VitrinScope, string> = { all: "all", shared: "shared", mine: "mine", recent: "recent", retired: "retired" };

const ROLE_LABEL: Record<string, string> = { admin: "Yönetici", builder: "Rapor tasarımcısı", viewer: "İzleyici" };

const SOURCE: Record<string, string> = {
  ldap: "LDAP / Active Directory", windows: "Windows oturumu", header: "Kurumsal oturum açma (proxy)",
};

function initials(name: string) {
  const parts = name.replace(/^.*\\/, "").split(/[\s._-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toLocaleUpperCase("tr");
}

export function Sidebar({ page, mode, collapsed, onToggle, onNavigate, me, currentReport, busy, vitrin }: {
  page: Page;
  /** vitrin: yayınlanmış rapor kataloğu menüsü · design: tasarım çalışma alanı menüsü */
  mode: "vitrin" | "design";
  collapsed: boolean;
  onToggle: () => void;
  onNavigate: (p: Exclude<Page, "designer" | "viewer" | "vitrin-report">) => void;
  me: Me | null;
  currentReport: { id: string; title: string } | null;
  busy: boolean;
  vitrin?: { items: VitrinCard[] | null; filter: VitrinFilter; onFilter: (f: VitrinFilter) => void };
}) {
  // yetkiler /api/me'den; yüklenene kadar bugünkü (masaüstü) menü — backend her uç noktada ayrıca kontrol eder
  const caps = me?.capabilities ?? { design: true, admin: true, vitrin: true };
  const item = (key: string, label: string, active: boolean, onClick: () => void, disabled = false, count?: number) => (
    <button type="button" className={`sb-item${active ? " is-on" : ""}`} onClick={onClick} disabled={disabled}
      title={collapsed ? label : undefined} aria-current={active ? "page" : undefined}>
      <Icon name={key} />
      <span className="sb-label">{label}</span>
      {count !== undefined ? <span className="sb-count sb-label">{count}</span> : null}
    </button>
  );
  const user = (
    <div className="sb-user" title={me ? [me.display_name, me.username, me.title, me.department,
      `Kaynak: ${SOURCE[me.source] ?? me.source}`, me.domain_joined ? "" : "Bu bilgisayar bir etki alanına bağlı değil (yerel hesap)"].filter(Boolean).join("\n") : "Kullanıcı bilgisi alınıyor…"}>
      <span className="sb-avatar" aria-hidden="true">{me ? initials(me.display_name || me.username) : "…"}</span>
      <span className="sb-user-text sb-label">
        <span className="sb-user-name">{me?.display_name ?? "…"}</span>
        <span className="sb-user-sub">{me ? `${me.username}${me.platform_mode === "server" && me.platform_role ? ` · ${ROLE_LABEL[me.platform_role] ?? me.platform_role}` : ""}` : ""}</span>
      </span>
    </div>
  );
  const brand = (
    <div className="sb-brand">
      <span className="brand-mark" aria-hidden="true">
        <svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" strokeLinecap="round"><circle cx="7" cy="7" r="5" strokeWidth="1.7" /><path d="M5 9V7.5M7 9V5M9 9V6.5" strokeWidth="1.5" /><path d="M10.8 10.8 14 14" strokeWidth="2" /></svg>
      </span>
      <span className="brand-name sb-label">BI Lens{mode === "vitrin" ? <span className="sb-brand-sub"> · Vitrin</span> : null}</span>
      <button type="button" className="sb-toggle" onClick={onToggle} title={collapsed ? "Menüyü genişlet" : "Menüyü daralt"} aria-label="Menüyü daralt/genişlet">
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
          {collapsed ? <path d="M6 3.5 10.5 8 6 12.5" /> : <path d="M10 3.5 5.5 8 10 12.5" />}
        </svg>
      </button>
    </div>
  );

  if (mode === "vitrin") {
    const items = vitrin?.items ?? [];
    const f = vitrin?.filter ?? { scope: "all" as VitrinScope, domain: null };
    const c = counts(items);
    const go = (next: VitrinFilter) => {
      vitrin?.onFilter(next);
      if (page !== "vitrin") window.location.hash = "#/vitrin";
    };
    const scopes: VitrinScope[] = ["all", "shared", ...(caps.design ? ["mine" as VitrinScope] : []), "recent", ...(c.retired ? ["retired" as VitrinScope] : [])];
    const domains = Object.entries(c.domains).sort((a, b) => a[0].localeCompare(b[0], "tr"));
    return (
      <nav className={`sidebar sidebar-vitrin${collapsed ? " is-collapsed" : ""}`} aria-label="Vitrin menüsü">
        {brand}
        {scopes.map((s) => item(SCOPE_ICON[s], SCOPE_LABEL[s], page === "vitrin" && f.scope === s && !f.domain, () => go({ scope: s, domain: null }), false, c[s]))}
        {domains.length ? <div className="sb-section sb-label">İş alanları</div> : null}
        {domains.map(([d, n]) => item("domain", d, page === "vitrin" && f.domain === d, () => go({ scope: "all", domain: d }), false, n))}
        <div className="sb-spacer" />
        {user}
      </nav>
    );
  }
  return (
    <nav className={`sidebar${collapsed ? " is-collapsed" : ""}`} aria-label="Ana menü">
      {brand}

      {caps.design ? <>
        <div className="sb-section sb-label">Raporlar</div>
        {item("home", "Rapor Envanteri", page === "home", () => onNavigate("home"), busy)}
        {currentReport ? item("designer", currentReport.title || "Açık rapor", page === "designer" || page === "viewer", () => { window.location.hash = `#/${page === "viewer" ? "v" : "r"}/${currentReport.id}`; }, busy) : null}

        <div className="sb-section sb-label">Veri</div>
        {item("access", "Veri Erişimim", page === "access", () => onNavigate("access"), busy)}
        {item("query", "Sorgu Çalıştır", page === "query", () => onNavigate("query"), busy)}
        {item("model", "Veri Modeli", page === "model", () => onNavigate("model"), busy)}
      </> : null}

      {caps.admin ? <>
        <div className="sb-section sb-label">Sistem</div>
        {item("admin", "Yönetim", page === "admin", () => onNavigate("admin"), busy)}
        {item("settings", "Bağlantı Ayarları", page === "settings", () => onNavigate("settings"), busy)}
      </> : null}

      <div className="sb-spacer" />
      {user}
    </nav>
  );
}
