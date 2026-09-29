// Veri erişimim: rol ve politika, yetkili tablolar (PII), onaylı view'lar, raporlardaki dataset'ler.
import { useEffect, useMemo, useState } from "react";
import type { AccessInfo } from "../types";
import type { Api } from "../api/client";

const KIND: Record<string, string> = { fact: "Fact", dimension: "Boyut", bridge: "Köprü", view: "View" };
type Tab = "tables" | "views" | "datasets";

export function AccessPage({ api, onOpenReport }: { api: Api; onOpenReport: (id: string) => void }) {
  const [info, setInfo] = useState<AccessInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("tables");
  const [q, setQ] = useState("");
  const [onlyMine, setOnlyMine] = useState(true);

  useEffect(() => {
    api.myAccess().then(setInfo).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [api]);

  const groups = useMemo(() => {
    if (!info) return [];
    const ql = q.trim().toLocaleLowerCase("tr");
    const rows = info.tables.filter((t) => (!onlyMine || t.accessible)
      && (!ql || `${t.name} ${t.business_name} ${t.description}`.toLocaleLowerCase("tr").includes(ql)));
    const m = new Map<string, typeof rows>();
    for (const t of rows) m.set(t.subject_area, [...(m.get(t.subject_area) ?? []), t]);
    return [...m.entries()];
  }, [info, q, onlyMine]);

  if (error) return <div className="access"><div className="banner-error">Erişim bilgisi alınamadı: {error}</div></div>;
  if (!info) return <div className="access"><div className="panel-empty"><span className="spinner" /><p>Yükleniyor…</p></div></div>;

  const accessible = info.tables.filter((t) => t.accessible).length;
  const piiTables = info.tables.filter((t) => t.accessible && t.pii_blocked).length;

  return (
    <div className="access">
      <div className="access-inner">
        <div className="acc-summary">
          <div className="acc-stat"><div className="k">Rol</div><div className="v">{info.role}</div></div>
          <div className="acc-stat"><div className="k">İzinli şemalar</div><div className="v">{info.policy.allowed_schemas.join(", ")}</div></div>
          <div className="acc-stat"><div className="k">Erişilebilir tablo</div><div className="v">{accessible} / {info.tables.length}</div></div>
          <div className="acc-stat"><div className="k">Kişisel veri (PII)</div><div className="v">{info.policy.allow_pii ? "Görülebilir" : `Engelli (${piiTables} tabloda)`}</div></div>
          <div className="acc-stat"><div className="k">Sorgu satır limiti</div><div className="v">{info.policy.max_rows.toLocaleString("tr-TR")}</div></div>
          <div className="acc-stat"><div className="k">Onaylı view</div><div className="v">{info.views.length}</div></div>
        </div>

        <div className="acc-tabs" role="tablist">
          {([["tables", `Tablolar (${info.tables.length})`], ["views", `Onaylı view'lar (${info.views.length})`],
            ["datasets", `Rapor dataset'leri (${info.datasets.length})`]] as [Tab, string][]).map(([k, l]) => (
            <button key={k} type="button" role="tab" aria-selected={tab === k} className={`acc-tab${tab === k ? " is-on" : ""}`} onClick={() => setTab(k)}>{l}</button>
          ))}
        </div>

        {tab === "tables" ? (
          <>
            <div className="acc-tools">
              <input className="dict-input" style={{ width: 280 }} placeholder="Tablo ara…" value={q} onChange={(e) => setQ(e.target.value)} />
              <label className="er-toggle"><input type="checkbox" checked={onlyMine} onChange={(e) => setOnlyMine(e.target.checked)} /> Yalnız erişebildiklerim</label>
            </div>
            {groups.map(([area, rows]) => (
              <div className="acc-group" key={area}>
                <h3>{area}</h3>
                <table className="acc-table">
                  <thead><tr><th>Tablo</th><th>Açıklama</th><th>Tür</th><th>Satır</th><th>Kolon</th><th>Kişisel veri</th><th>Erişim</th></tr></thead>
                  <tbody>
                    {rows.map((t) => (
                      <tr key={t.id} className={t.accessible ? undefined : "is-denied"}>
                        <td><code>{t.name}</code><div className="acc-note">{t.business_name}</div></td>
                        <td className="acc-note">{t.description}</td>
                        <td>{KIND[t.kind ?? ""] ?? t.kind}</td>
                        <td>{t.row_count != null ? t.row_count.toLocaleString("tr-TR") : "—"}</td>
                        <td>{t.column_count}</td>
                        <td>{t.pii_columns.length ? (
                          <span className={t.pii_blocked ? "acc-lock" : undefined} title={t.pii_columns.join(", ")}>
                            {t.pii_blocked ? "🔒 " : ""}{t.pii_columns.length} kolon{t.pii_blocked ? " engelli" : ""}
                          </span>) : "—"}</td>
                        <td>{t.accessible ? <span className="acc-ok">✓ Var</span> : <span className="acc-no" title={t.reason ?? ""}>✗ {t.reason}</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
            {!groups.length ? <p className="muted">Eşleşen tablo yok.</p> : null}
          </>
        ) : null}

        {tab === "views" ? (
          info.views.length ? (
            <table className="acc-table">
              <thead><tr><th>View</th><th>Açıklama</th><th>Kolonlar</th><th>Kaynak rapor</th><th>Oluşturma</th></tr></thead>
              <tbody>
                {info.views.map((v) => (
                  <tr key={v.name}>
                    <td><code>{v.name}</code></td>
                    <td className="acc-note">{v.business_name}</td>
                    <td className="acc-note">{v.columns.map((c) => c.label || c.name).join(", ")}</td>
                    <td>{v.session_id ? <button type="button" className="link-btn" onClick={() => onOpenReport(v.session_id!)}>{v.dataset_id}</button> : v.dataset_id}</td>
                    <td className="acc-note">{v.created_at ? new Date(v.created_at).toLocaleDateString("tr-TR") : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <p className="muted">Henüz onaylı view yok. Rapor sayfasında Veri sekmesinden bir dataset'i “View olarak kalıcılaştır” ile ekleyebilirsiniz.</p>
        ) : null}

        {tab === "datasets" ? (
          <table className="acc-table">
            <thead><tr><th>Rapor</th><th>Dataset</th><th>Kaynak tablolar</th><th>Kaynak</th><th>Erişim</th></tr></thead>
            <tbody>
              {info.datasets.map((d) => (
                <tr key={`${d.report_id}.${d.id}`} className={d.accessible ? undefined : "is-denied"}>
                  <td><button type="button" className="link-btn" onClick={() => onOpenReport(d.report_id)}>{d.report_title}</button></td>
                  <td><code>{d.id}</code><div className="acc-note">{d.description}</div></td>
                  <td className="acc-note">{d.tables.join(", ")}</td>
                  <td>{d.view ? <span className="pill pill-ok">view: {d.view}</span> : <span className="pill pill-muted">anlık SQL</span>}</td>
                  <td>{d.accessible ? <span className="acc-ok">✓</span> : <span className="acc-no" title={d.reason ?? ""}>✗ yetki yok</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </div>
    </div>
  );
}
