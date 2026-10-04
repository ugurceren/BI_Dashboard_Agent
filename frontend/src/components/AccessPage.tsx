// Veri erişimim: rol ve politika + yetkili nesneler (tablo / view / stored procedure / dataset),
// domain → tip ya da tip → domain olarak gruplanır (Sorgu Çalıştır'daki panel ile aynı yapı).
import { useEffect, useMemo, useState } from "react";
import type { AccessInfo, AccessObject } from "../types";
import type { Api } from "../api/client";
import { TYPE_BADGE, groupTwoLevel, type GroupBy, type ObjType } from "../lib/objectTypes";

const LS_GROUPBY = "bi.access.groupBy";
const lsRead = (k: string) => { try { return localStorage.getItem(k); } catch { return null; } };
const lsWrite = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* yoksay */ } };
const fmtDate = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString("tr-TR") : "");

const Badge = ({ type }: { type: ObjType }) => <span className={`acc-badge k-${type}`}>{TYPE_BADGE[type]}</span>;
const Undoc = ({ o }: { o: AccessObject }) => (o.documented === false
  ? <span className="acc-undoc" title="Veritabanında var, veri sözlüğünde tanımlı değil — açıklama veritabanından (MS_Description)">sözlükte yok</span> : null);

const Access = ({ o }: { o: AccessObject }) => (o.type === "procedure"
  ? <span className="acc-note" title={o.reason ?? ""}>Yalnız liste</span>
  : o.accessible ? <span className="acc-ok">✓ Var</span> : <span className="acc-no" title={o.reason ?? ""}>✗ {o.reason}</span>);

function Pii({ o }: { o: AccessObject }) {
  const cols = o.pii_columns ?? [];
  if (!cols.length) return <>—</>;
  return <span className={o.pii_blocked ? "acc-lock" : undefined} title={cols.join(", ")}>{o.pii_blocked ? "🔒 " : ""}{cols.length} kolon{o.pii_blocked ? " engelli" : ""}</span>;
}

/** Her tipin kendi kolonlarıyla tablosu */
function TypeTable({ type, items, onOpenReport }: { type: ObjType; items: AccessObject[]; onOpenReport: (id: string) => void }) {
  const report = (o: AccessObject) => (o.report_id
    ? <button type="button" className="link-btn" onClick={() => onOpenReport(o.report_id!)}>{o.report_title || o.dataset_id || "rapor"}</button>
    : <span className="muted">—</span>);
  if (type === "table") return (
    <table className="acc-table">
      <thead><tr><th>Tablo</th><th>Açıklama</th><th>Satır</th><th>Kolon</th><th>Kişisel veri</th><th>Erişim</th></tr></thead>
      <tbody>{items.map((o) => (
        <tr key={o.id} className={o.accessible ? undefined : "is-denied"}>
          <td><code>{o.name}</code> <Undoc o={o} />{o.documented !== false ? <div className="acc-note">{o.business_name}</div> : null}</td>
          <td className="acc-note">{o.description}</td>
          <td>{o.row_count != null ? o.row_count.toLocaleString("tr-TR") : "—"}</td>
          <td>{o.column_count}</td>
          <td><Pii o={o} /></td>
          <td><Access o={o} /></td>
        </tr>))}
      </tbody>
    </table>
  );
  if (type === "view") return (
    <table className="acc-table">
      <thead><tr><th>View</th><th>Açıklama</th><th>Kaynak tablolar</th><th>Kolon</th><th>Kaynak rapor</th><th>Oluşturma</th><th>Erişim</th></tr></thead>
      <tbody>{items.map((o) => (
        <tr key={o.id} className={o.accessible ? undefined : "is-denied"}>
          <td><code>{o.name}</code> <Undoc o={o} />{o.documented !== false ? <div className="acc-note">{o.business_name}</div> : null}</td>
          <td className="acc-note">{o.description}</td>
          <td className="acc-note">{o.tables?.length ? o.tables.join(", ") : "—"}</td>
          <td>{o.column_count}</td>
          <td>{report(o)}</td>
          <td className="acc-note">{fmtDate(o.created_at)}</td>
          <td><Access o={o} /></td>
        </tr>))}
      </tbody>
    </table>
  );
  if (type === "procedure") return (
    <table className="acc-table">
      <thead><tr><th>Stored procedure</th><th>Açıklama</th><th>Parametreler</th><th>Kullandığı tablolar</th><th>Erişim</th></tr></thead>
      <tbody>{items.map((o) => (
        <tr key={o.id}>
          <td><code>{o.name}</code></td>
          <td className="acc-note">{o.description || "—"}</td>
          <td className="acc-note">{o.parameters?.length ? o.parameters.join(", ") : "—"}</td>
          <td className="acc-note">{o.tables?.length ? o.tables.join(", ") : "—"}</td>
          <td><Access o={o} /></td>
        </tr>))}
      </tbody>
    </table>
  );
  return (
    <table className="acc-table">
      <thead><tr><th>Dataset</th><th>Rapor</th><th>Kaynak tablolar</th><th>Kaynak</th><th>Erişim</th></tr></thead>
      <tbody>{items.map((o) => (
        <tr key={o.id} className={o.accessible ? undefined : "is-denied"}>
          <td><code>{o.name}</code><div className="acc-note">{o.description}</div></td>
          <td>{report(o)}</td>
          <td className="acc-note">{o.tables?.join(", ") || "—"}</td>
          <td>{o.view ? <span className="pill pill-ok">view: {o.view}</span> : <span className="pill pill-muted">anlık SQL</span>}</td>
          <td>{o.accessible ? <span className="acc-ok">✓</span> : <span className="acc-no" title={o.reason ?? ""}>✗ yetki yok</span>}</td>
        </tr>))}
      </tbody>
    </table>
  );
}

export function AccessPage({ api, onOpenReport }: { api: Api; onOpenReport: (id: string) => void }) {
  const [info, setInfo] = useState<AccessInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [onlyMine, setOnlyMine] = useState(true);
  const [groupBy, setGroupByState] = useState<GroupBy>(() => (lsRead(LS_GROUPBY) === "type" ? "type" : "domain"));
  const setGroupBy = (g: GroupBy) => { setGroupByState(g); lsWrite(LS_GROUPBY, g); };
  const [closed, setClosed] = useState<Record<string, boolean>>({ "type:dataset": true });

  useEffect(() => {
    api.myAccess().then(setInfo).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [api]);

  const groups = useMemo(() => {
    if (!info) return [];
    const ql = q.trim().toLocaleLowerCase("tr");
    const items = info.objects.filter((o) => (!onlyMine || o.accessible)
      && (!ql || [o.name, o.business_name, o.description, o.subject_area, o.report_title, ...(o.parameters ?? [])]
        .join(" ").toLocaleLowerCase("tr").includes(ql)));
    return groupTwoLevel(items, groupBy, (o) => o.type, (o) => o.subject_area);
  }, [info, q, onlyMine, groupBy]);

  if (error) return <div className="access"><div className="banner-error">Erişim bilgisi alınamadı: {error}</div></div>;
  if (!info) return <div className="access"><div className="panel-empty"><span className="spinner" /><p>Yükleniyor…</p></div></div>;

  const of = (t: ObjType) => info.objects.filter((o) => o.type === t);
  const tables = of("table");
  const accessible = tables.filter((t) => t.accessible).length;
  const piiTables = tables.filter((t) => t.accessible && t.pii_blocked).length;

  return (
    <div className="access">
      <div className="access-inner">
        <div className="acc-summary">
          <div className="acc-stat"><div className="k">İzinli şemalar</div><div className="v">{info.policy.allowed_schemas.includes("*") ? "Tümü (veritabanı yetkisi)" : info.policy.allowed_schemas.join(", ")}</div></div>
          <div className="acc-stat"><div className="k">Erişilebilir tablo</div><div className="v">{accessible} / {tables.length}</div></div>
          <div className="acc-stat"><div className="k">View · SP · Dataset</div><div className="v">{of("view").length} · {of("procedure").length} · {of("dataset").length}</div></div>
          <div className="acc-stat"><div className="k">Kişisel veri (PII)</div><div className="v">{info.policy.allow_pii ? "Görülebilir" : `Engelli (${piiTables} tabloda)`}</div></div>
          <div className="acc-stat"><div className="k">Sorgu satır limiti</div><div className="v">{info.policy.max_rows.toLocaleString("tr-TR")}</div></div>
        </div>

        {info.catalog && !info.catalog.ok ? (
          <div className="banner-error">
            Veritabanı kataloğu okunamadı; yetkiler veritabanından alınamadı, yalnızca sözlükteki nesneler gösteriliyor. {info.catalog.error}
          </div>
        ) : info.catalog?.undocumented ? (
          <p className="muted small acc-info">
            Liste bağlandığınız veritabanındaki <b>SELECT yetkiniz olan</b> tablo ve view'lardan oluşur. {info.catalog.undocumented} nesne veri sözlüğünde
            tanımlı değil ("sözlükte yok"); açıklamaları veritabanından gelir, kişisel veri olabilecek kolonlar (e-posta, telefon, adres …) adlarına göre korunur.
          </p>
        ) : null}
        {info.catalog?.missing_count ? (
          <div className="banner-error">
            Veri sözlüğündeki <b>{info.catalog.missing_count}</b> nesne bağlandığınız veritabanında bulunamadı: {info.catalog.missing?.slice(0, 8).join(", ")}
            {info.catalog.missing_count > 8 ? " …" : ""}. Adları veritabanındakiyle aynı mı (şema.nesne), veri kaynağı doğru veritabanı mı?
            Bağlantı Ayarları → Veri sözlüğü → "Bağlantıyı test et" eşleşmeyenlerin tam listesini gösterir.
          </div>
        ) : null}

        <div className="acc-tools">
          <label className="acc-search">
            <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true"><path d="M7 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10ZM10.7 10.7 14 14" /></svg>
            <input type="search" placeholder="Nesne, açıklama, domain ara…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Nesne ara" />
          </label>
          <label className="er-toggle"><input type="checkbox" checked={onlyMine} onChange={(e) => setOnlyMine(e.target.checked)} /> Yalnız erişebildiklerim</label>
          <span className="acc-spacer" />
          <div className="acc-groupby" role="group" aria-label="Gruplama">
            <span className="muted small">Grupla</span>
            <div className="qp-seg">
              <button type="button" className={groupBy === "domain" ? "is-on" : undefined} onClick={() => setGroupBy("domain")} aria-pressed={groupBy === "domain"}>Domain</button>
              <button type="button" className={groupBy === "type" ? "is-on" : undefined} onClick={() => setGroupBy("type")} aria-pressed={groupBy === "type"}>Nesne tipi</button>
            </div>
          </div>
        </div>

        {groups.map((g) => {
          const open = q.trim() ? true : !closed[g.key];
          return (
            <section className="acc-group" key={g.key}>
              <button type="button" className="acc-group-head" onClick={() => setClosed((s) => ({ ...s, [g.key]: open }))} aria-expanded={open}>
                <svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={open ? "M4 6l4 4 4-4" : "M6 4l4 4-4 4"} /></svg>
                {g.type ? <Badge type={g.type} /> : null}
                <span className="acc-group-title">{g.title}</span>
                <span className="acc-count">{g.count}</span>
                {!open ? <span className="acc-group-sub muted">{g.subs.map((s) => `${s.title} ${s.items.length}`).join(" · ")}</span> : null}
              </button>
              {open ? g.subs.map((s) => (
                <div className="acc-sub" key={s.key}>
                  <div className="acc-sub-title">
                    {s.type ? <Badge type={s.type} /> : null}
                    <span>{s.title}</span>
                    <span className="acc-count">{s.items.length}</span>
                  </div>
                  {/* domain → tip: tipi alt başlık belirler; tip → domain: grubun tipi */}
                  <TypeTable type={(s.type ?? g.type)!} items={s.items} onOpenReport={onOpenReport} />
                </div>
              )) : null}
            </section>
          );
        })}
        {!groups.length ? <p className="muted">Eşleşen nesne yok.</p> : null}
      </div>
    </div>
  );
}
