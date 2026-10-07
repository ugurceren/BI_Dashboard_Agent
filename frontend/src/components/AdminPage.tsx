// Yönetim (admin): kullanıcı / AD grubu rolleri, tüm yayınlar (paylaşım, kaldırma, sahiplik devri), denetim kaydı.
import { useCallback, useEffect, useState } from "react";
import type { Api } from "../api/client";
import type { AdminOverview, AuditEvent, PlatformRole } from "../types";
import "./vitrin.css";

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));
const fmt = (iso: string) => new Date(iso).toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" });

const ROLE_LABEL: Record<PlatformRole, string> = { admin: "Yönetici", builder: "Rapor tasarımcısı", viewer: "İzleyici" };
const EVENT_LABEL: Record<string, string> = {
  report_publish: "Yayınlandı", report_retire: "Yayından kaldırıldı", report_view: "Rapor açıldı", report_export: "Dışa aktarıldı",
  grants_change: "Paylaşım değişti", role_assign: "Rol atandı", role_unassign: "Rol kaldırıldı", owner_transfer: "Sahiplik devredildi",
  status_change: "Statü değişti", session_delete: "Tasarım silindi", settings_platform_db: "Platform veritabanı değişti",
};

type Tab = "roles" | "reports" | "audit";

export function AdminPage({ api, onOpenReport }: { api: Api; onOpenReport: (id: string) => void }) {
  const [tab, setTab] = useState<Tab>("roles");
  const [ov, setOv] = useState<AdminOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => api.adminOverview().then((o) => { setOv(o); setError(null); }).catch((e) => setError(errMsg(e))), [api]);
  useEffect(() => { void load(); }, [load]);

  return (
    <div className="home admin">
      <div className="vt-hero">
        <div>
          <h1 className="home-title">Yönetim</h1>
          <p className="muted home-sub">
            {ov ? <>Mod: <b>{ov.mode === "server" ? "Ortak sunucu" : "Masaüstü (tek kullanıcı)"}</b> · Kayıtlar: <b>{ov.store === "sqlserver" ? "SQL Server" : "yerel SQLite"}</b>
              {ov.platform_admins.length ? <> · Sabit yöneticiler (.env): {ov.platform_admins.join(", ")}</> : null}</> : "Roller, yayınlar ve denetim kaydı"}
          </p>
        </div>
      </div>
      <div className="st-filter vt-scope" role="tablist">
        {([["roles", "Roller"], ["reports", "Yayınlar"], ["audit", "Denetim kaydı"]] as [Tab, string][]).map(([k, l]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} className={`st-chip${tab === k ? " is-on" : ""}`} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      {error ? <div className="banner-error">{error}</div> : null}
      {!ov && !error ? <div className="panel-empty"><span className="spinner" /></div> : null}
      {ov && tab === "roles" ? <RolesTab api={api} ov={ov} reload={load} /> : null}
      {ov && tab === "reports" ? <ReportsTab api={api} ov={ov} reload={load} onOpen={onOpenReport} /> : null}
      {tab === "audit" ? <AuditTab api={api} /> : null}
    </div>
  );
}

function RolesTab({ api, ov, reload }: { api: Api; ov: AdminOverview; reload: () => void }) {
  const [type, setType] = useState<"user" | "group">("group");
  const [principal, setPrincipal] = useState("");
  const [platformRole, setPlatformRole] = useState<PlatformRole | "">("builder");
  const [dataRole, setDataRole] = useState("");
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    if (!principal.trim()) return;
    try {
      await api.setAssignment({ principal_type: type, principal: principal.trim(), platform_role: platformRole || null, data_role: dataRole || null });
      setPrincipal(""); setError(null); reload();
    } catch (e) { setError(errMsg(e)); }
  };
  const remove = async (t: string, p: string) => {
    try { await api.deleteAssignment(t, p); reload(); } catch (e) { setError(errMsg(e)); }
  };
  return (
    <section className="adm-section">
      <p className="muted small">
        <b>Platform rolü</b> uygulamada neye erişileceğini belirler: <i>İzleyici</i> yalnız Vitrin'i, <i>Rapor tasarımcısı</i> ayrıca rapor
        tasarlamayı ve yayınlamayı, <i>Yönetici</i> her şeyi. <b>Veri rolü</b> hangi verinin görüleceğini belirler (policy.toml: şema / tablo
        izinleri, kişisel veri, satır limiti). Kullanıcıya verilen atama gruba verilenden önce gelir; atama yoksa varsayılan İzleyici.
      </p>
      <form className="adm-form" onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <select className="vt-input" value={type} onChange={(e) => setType(e.target.value as "user" | "group")} aria-label="Tür">
          <option value="group">AD grubu</option><option value="user">Kullanıcı</option>
        </select>
        <input className="vt-input" value={principal} onChange={(e) => setPrincipal(e.target.value)} maxLength={256}
          placeholder={type === "group" ? "Grup adı, ör. BI_Tasarimcilar" : "DOMAIN\\kullanici"} aria-label="Kullanıcı ya da grup" />
        <select className="vt-input" value={platformRole} onChange={(e) => setPlatformRole(e.target.value as PlatformRole | "")} aria-label="Platform rolü">
          <option value="">Platform rolü: değiştirme</option>
          {ov.platform_roles.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
        </select>
        <select className="vt-input" value={dataRole} onChange={(e) => setDataRole(e.target.value)} aria-label="Veri rolü">
          <option value="">Veri rolü: değiştirme</option>
          {ov.data_roles.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <button type="submit" className="btn btn-primary btn-sm" disabled={!principal.trim() || (!platformRole && !dataRole)}>Ata</button>
      </form>
      {error ? <div className="banner-error">{error}</div> : null}
      <div className="rl-wrap">
        <table className="rl adm-table">
          <thead><tr><th>Tür</th><th>Kullanıcı / grup</th><th>Platform rolü</th><th>Veri rolü</th><th>Atayan</th><th aria-label="İşlem" /></tr></thead>
          <tbody>
            {ov.assignments.length ? ov.assignments.map((a) => (
              <tr key={`${a.principal_type}:${a.principal}`}>
                <td>{a.principal_type === "group" ? "AD grubu" : "Kullanıcı"}</td>
                <td><b>{a.principal}</b></td>
                <td>{a.platform_role ? ROLE_LABEL[a.platform_role] : <span className="muted">—</span>}</td>
                <td>{a.data_role ?? <span className="muted">—</span>}</td>
                <td className="muted small">{a.granted_by} · {fmt(a.granted_at)}</td>
                <td><button type="button" className="btn btn-ghost btn-sm rc-del" onClick={() => void remove(a.principal_type, a.principal)}>Kaldır</button></td>
              </tr>
            )) : <tr><td colSpan={6} className="muted">Henüz atama yok: herkes varsayılan rolle (İzleyici) bağlanır.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ReportsTab({ api, ov, reload, onOpen }: { api: Api; ov: AdminOverview; reload: () => void; onOpen: (id: string) => void }) {
  const [error, setError] = useState<string | null>(null);
  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); setError(null); reload(); } catch (e) { setError(errMsg(e)); }
  };
  return (
    <section className="adm-section">
      {error ? <div className="banner-error">{error}</div> : null}
      <div className="rl-wrap">
        <table className="rl adm-table">
          <thead><tr><th>Rapor</th><th>Sahip</th><th>Sürüm</th><th>Durum</th><th>Paylaşım</th><th>Güncellendi</th><th aria-label="İşlemler" /></tr></thead>
          <tbody>
            {ov.reports.length ? ov.reports.map((r) => (
              <tr key={r.report_id}>
                <td><button type="button" className="adm-link" onClick={() => onOpen(r.report_id)}>{r.title}</button></td>
                <td title={r.owner}>{r.owner_name || r.owner}</td>
                <td>v{r.current_version}</td>
                <td><span className={`pill ${r.status === "active" ? "pill-ok" : "pill-muted"}`}>{r.status === "active" ? "Yayında" : "Kaldırıldı"}</span></td>
                <td className="small">{r.grants.length ? r.grants.map((g) => `${g.principal_type === "group" ? "Grup" : "Kişi"}: ${g.principal}${g.can_export ? " (indirir)" : ""}`).join(", ") : <span className="muted">yalnız sahibi</span>}</td>
                <td className="muted small">{fmt(r.updated_at)}</td>
                <td className="adm-actions">
                  <button type="button" className="btn btn-ghost btn-sm" onClick={() => {
                    const owner = window.prompt("Yeni sahip (DOMAIN\\kullanici):", r.owner);
                    if (owner && owner.trim() && owner.trim() !== r.owner) void act(() => api.transferOwner(r.report_id, owner.trim()));
                  }}>Sahibini değiştir</button>
                  {r.status === "active" ? <button type="button" className="btn btn-ghost btn-sm rc-del" onClick={() => void act(() => api.retireReport(r.report_id))}>Kaldır</button> : null}
                </td>
              </tr>
            )) : <tr><td colSpan={7} className="muted">Henüz yayınlanmış rapor yok.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function AuditTab({ api }: { api: Api }) {
  const [rows, setRows] = useState<AuditEvent[] | null>(null);
  const [user, setUser] = useState("");
  const [event, setEvent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api.audit({ limit: 300, user: user.trim() || undefined, event: event || undefined })
      .then((r) => { setRows(r); setError(null); }).catch((e) => setError(errMsg(e)));
  }, [api, user, event]);
  useEffect(() => { load(); }, [load]);
  return (
    <section className="adm-section">
      <form className="adm-form" onSubmit={(e) => { e.preventDefault(); load(); }}>
        <input className="vt-input" value={user} onChange={(e) => setUser(e.target.value)} placeholder="Kullanıcı (DOMAIN\kullanici)" aria-label="Kullanıcıya göre" />
        <select className="vt-input" value={event} onChange={(e) => setEvent(e.target.value)} aria-label="Olay türü">
          <option value="">Tüm olaylar</option>
          {Object.entries(EVENT_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <button type="submit" className="btn btn-secondary btn-sm">Yenile</button>
      </form>
      {error ? <div className="banner-error">{error}</div> : null}
      <div className="rl-wrap">
        <table className="rl adm-table">
          <thead><tr><th>Zaman</th><th>Kullanıcı</th><th>Olay</th><th>Rapor</th><th>Ayrıntı</th></tr></thead>
          <tbody>
            {rows === null ? <tr><td colSpan={5}><span className="spinner" /></td></tr> : rows.length ? rows.map((r) => (
              <tr key={r.id}>
                <td className="muted small">{fmt(r.ts)}</td>
                <td>{r.username}</td>
                <td>{EVENT_LABEL[r.event] ?? r.event}</td>
                <td className="small">{r.report_id ?? <span className="muted">—</span>}</td>
                <td className="small muted adm-details">{r.details ? JSON.stringify(r.details) : ""}</td>
              </tr>
            )) : <tr><td colSpan={5} className="muted">Kayıt yok.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}
