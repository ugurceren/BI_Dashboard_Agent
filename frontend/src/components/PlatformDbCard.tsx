// Bağlantı Ayarları → Platform Veritabanı: Vitrin kayıtları (roller, yayınlar, paylaşım, denetim) için SQL Server.
// Kaydedilmezse .env META_ODBC, o da yoksa yerel SQLite dosyası (backend/config/platform.db) kullanılır.
import { useEffect, useState } from "react";
import type { Api } from "../api/client";
import type { ConnFields, PlatformSettings } from "../types";
import { SettingsCardHead, type CardLayout } from "./SettingsCardHead";

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const KEY = "bi.settings.platform";
const readLayout = (): CardLayout => {
  try { return { collapsed: true, wide: false, ...JSON.parse(localStorage.getItem(KEY) ?? "{}") }; } catch { return { collapsed: true, wide: false }; }
};

export function PlatformDbCard({ api }: { api: Api }) {
  const [ps, setPs] = useState<PlatformSettings | null>(null);
  const [f, setF] = useState<ConnFields | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [layout, setLayoutState] = useState<CardLayout>(readLayout);
  const setLayout = (p: Partial<CardLayout>) => {
    const next = { ...layout, ...p };
    setLayoutState(next);
    try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* yok say */ }
  };
  const load = async () => {
    try {
      const p = await api.getPlatformSettings();
      setPs(p);
      setF({ server: p.meta.server ?? "", database: p.meta.database || "BI_Lens_Meta", auth: p.meta.auth === "sql" ? "sql" : "windows",
        username: p.meta.username ?? "", password: null, has_password: p.meta.has_password, encrypt: p.meta.encrypt ?? true,
        trust_server_certificate: p.meta.trust_server_certificate ?? true });
    } catch (e) { setMsg({ ok: false, text: `Platform ayarı alınamadı: ${errText(e)}` }); }
  };
  useEffect(() => { void load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const upd = (p: Partial<ConnFields>) => setF((c) => (c ? { ...c, ...p } : c));
  const save = async () => {
    if (!f) return;
    setBusy(true); setMsg(null);
    try {
      const r = await api.savePlatformSettings(f);
      setMsg(r.ok ? { ok: true, text: "Platform veritabanı kaydedildi; tablolar hazır." } : { ok: false, text: r.error ?? "Kaydedilemedi" });
      if (r.ok) await load();
    } catch (e) { setMsg({ ok: false, text: errText(e) }); }
    setBusy(false);
  };
  const reset = async () => {
    setBusy(true);
    try { const r = await api.resetPlatformSettings(); setMsg(r.ok ? { ok: true, text: "Varsayılana dönüldü." } : { ok: false, text: r.error ?? "" }); await load(); }
    catch (e) { setMsg({ ok: false, text: errText(e) }); }
    setBusy(false);
  };

  const storeText = ps ? (ps.store === "sqlserver" ? "SQL Server" : ps.store === "sqlite" ? "yerel SQLite dosyası" : "bağlı değil") : "…";
  const summary = ps ? `${storeText}${ps.meta.source !== "default" ? ` · ${ps.meta.server}/${ps.meta.database}` : ""} · mod: ${ps.mode === "server" ? "ortak sunucu" : "masaüstü"}` : "…";
  return (
    <section className={`st-card${layout.wide ? " st-card-wide" : ""}${layout.collapsed ? " is-collapsed" : ""}`}>
      <SettingsCardHead icon={<svg viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M2 6.5h12M3 6.5V13.5h10V6.5M2 6.5l1.5-4h9l1.5 4M6.5 13.5V10h3v3.5" /></svg>}
        title="Platform Veritabanı" source={ps?.meta.source === "ui" ? "ui" : ps?.meta.source === "env" ? "env" : undefined}
        desc={<>Vitrin kayıtları: rol atamaları, yayınlanmış rapor sürümleri, paylaşım izinleri ve denetim kaydı. Ortak sunucuda SQL Server
          önerilir (ör. <b>BI_Lens_Meta</b>); kaydedilmezse yerel dosya kullanılır. Şu an: <b>{storeText}</b>.</>}
        summary={summary} layout={layout} onChange={setLayout} />
      {layout.collapsed || !f ? null : <>
        {ps?.error ? <div className="st-result is-bad">{ps.error}</div> : null}
        <div className="st-grid2">
          <div className="st-field">
            <label htmlFor="meta-server">Sunucu / Instance</label>
            <input id="meta-server" className="st-input" value={f.server} placeholder="SUNUCU\INSTANCE" spellCheck={false} onChange={(e) => upd({ server: e.target.value })} />
          </div>
          <div className="st-field">
            <label htmlFor="meta-db">Veritabanı</label>
            <input id="meta-db" className="st-input" value={f.database} placeholder="BI_Lens_Meta" spellCheck={false} onChange={(e) => upd({ database: e.target.value })} />
          </div>
        </div>
        <div className="st-field">
          <label>Kimlik Doğrulama</label>
          <div className="st-seg" role="group">
            <button type="button" className={f.auth === "windows" ? "is-on" : undefined} onClick={() => upd({ auth: "windows" })}>Windows (hizmet hesabı)</button>
            <button type="button" className={f.auth === "sql" ? "is-on" : undefined} onClick={() => upd({ auth: "sql" })}>SQL Server kullanıcısı</button>
          </div>
        </div>
        {f.auth === "sql" ? (
          <div className="st-grid2">
            <div className="st-field">
              <label htmlFor="meta-user">Kullanıcı Adı</label>
              <input id="meta-user" className="st-input" value={f.username} autoComplete="off" onChange={(e) => upd({ username: e.target.value })} />
            </div>
            <div className="st-field">
              <label htmlFor="meta-pwd">Şifre</label>
              <input id="meta-pwd" className="st-input" type="password" autoComplete="new-password" value={f.password ?? ""}
                placeholder={f.has_password ? "•••••• (kayıtlı; değiştirmek için yazın)" : ""} onChange={(e) => upd({ password: e.target.value })} />
            </div>
          </div>
        ) : null}
        <p className="muted small">Hesabın bu veritabanında okuma / yazma yetkisi olmalı; tablolar yoksa ilk kayıtta oluşturulur
          (ya da DBA <code>backend/meta/schema.sql</code> dosyasını çalıştırır). Rapor verisinin okunduğu veritabanına yazılmaz.</p>
        <div className="st-llm-foot">
          {msg ? <div className={`st-result ${msg.ok ? "is-ok" : "is-bad"}`} role="status">{msg.text}</div> : <span />}
          <div className="st-row">
            {ps?.meta.source === "ui" ? <button type="button" className="btn btn-ghost btn-sm" onClick={() => void reset()} disabled={busy}>Varsayılana dön</button> : null}
            <button type="button" className="btn btn-primary btn-sm" onClick={() => void save()} disabled={busy || !f.server.trim() || !f.database.trim()}>
              {busy ? <span className="spinner" /> : null} Bağlan ve kaydet
            </button>
          </div>
        </div>
      </>}
    </section>
  );
}
