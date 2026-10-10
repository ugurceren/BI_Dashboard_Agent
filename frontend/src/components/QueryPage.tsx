// Sorgu Çalıştır: yetkili tablo / view / dataset gezgini + IntelliSense'li SQL editörü + sonuç tablosu.
// Sorgu backend'de rol yetkisiyle doğrulanır (yalnızca SELECT, yetkili şemalar, PII) ve salt-okunur çalışır (en çok 1000 satır).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { TYPE_BADGE, TYPE_ORDER, TYPE_SHORT, TYPE_TITLE, typeOfKind, type GroupBy, type ObjType } from "../lib/objectTypes";
import type { Api } from "../api/client";
import type { QueryDataset, QueryObject, QueryRunResult, QuerySchema } from "../types";
import { SqlEditor, type SqlEditorHandle } from "./SqlEditor";
import { QueryResultView, toCsv } from "./QueryResult";
import { SendToReportDialog } from "./SendToReportDialog";
import "./query.css";

const LS_SQL = "bi.query.sql";
const LS_EXPLORER = "bi.query.explorerCollapsed";
const LS_GROUPBY = "bi.query.groupBy";
const typeOf = typeOfKind;
interface ExSub { key: string; title: string; type?: ObjType; objs: QueryObject[]; ds: QueryDataset[] }
interface ExGroup { key: string; title: string; type?: ObjType; count: number; subs: ExSub[] }
const lsRead = (k: string) => { try { return localStorage.getItem(k); } catch { return null; } };
const lsWrite = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* yoksay */ } };

const Ico = ({ d, size = 14 }: { d: string; size?: number }) => (
  <svg viewBox="0 0 16 16" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
);

export function QueryPage({ api, theme, onOpenReport }: { api: Api; theme: string; onOpenReport: (id: string) => void }) {
  const [schema, setSchema] = useState<QuerySchema | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  // grup açık/kapalı (mod başına): nesne tipi modunda dataset'ler kapalı gelir
  const [groupOpen, setGroupOpen] = useState<Record<string, boolean>>({ "type:dataset": false });
  const [groupBy, setGroupByState] = useState<GroupBy>(() => (lsRead(LS_GROUPBY) === "type" ? "type" : "domain"));
  const setGroupBy = (g: GroupBy) => { setGroupByState(g); lsWrite(LS_GROUPBY, g); };
  const [collapsed, setCollapsedState] = useState(() => lsRead(LS_EXPLORER) === "1");
  const setCollapsed = (v: boolean) => { setCollapsedState(v); lsWrite(LS_EXPLORER, v ? "1" : "0"); };
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<QueryRunResult | null>(null);
  const [ranSql, setRanSql] = useState("");
  const editor = useRef<SqlEditorHandle>(null);
  const [initialSql] = useState(() => lsRead(LS_SQL) ?? "");
  const [sending, setSending] = useState<string | null>(null);   // "Dashboard hazırla" penceresindeki SQL

  useEffect(() => {
    let alive = true;
    api.querySchema().then((s) => alive && setSchema(s)).catch((e) => alive && setLoadError(e instanceof Error ? e.message : String(e)));
    return () => { alive = false; };
  }, [api]);

  // ilk açılış: kayıtlı SQL yoksa ilk tablodan örnek sorgu
  const starter = useMemo(() => {
    if (initialSql || !schema) return initialSql;
    const first = schema.objects.find((o) => o.kind === "fact") ?? schema.objects[0];
    if (!first) return "";
    const cols = first.columns.filter((c) => !c.blocked).slice(0, 5).map((c) => c.name).join(", ");
    return `-- Ctrl+Enter / F5: çalıştır (seçili metin varsa yalnızca o). En çok ${schema.max_rows} satır döner.\nSELECT TOP 100 ${cols || "*"}\nFROM ${first.name};\n`;
  }, [schema, initialSql]);

  const run = useCallback(async () => {
    const text = editor.current?.runText() ?? "";
    if (!text || running) return;
    setRunning(true);
    setRanSql(text);
    try {
      setResult(await api.runQuery(text));
    } catch (e) {
      setResult({ ok: false, errors: [e instanceof Error ? e.message : String(e)] });
    } finally {
      setRunning(false);
    }
  }, [api, running]);

  const insert = (text: string) => editor.current?.insert(text);
  const replaceAll = (text: string, runNow = false) => {
    editor.current?.replaceAll(text);
    if (runNow) setTimeout(() => void run(), 0);
  };
  const preview = (o: QueryObject) => {
    const cols = o.columns.filter((c) => !c.blocked).map((c) => c.name);
    const list = cols.length === o.columns.length ? "*" : cols.join(",\n       ");
    replaceAll(`SELECT TOP 100 ${list}\nFROM ${o.name};\n`, true);
  };

  const groups = useMemo<ExGroup[]>(() => {
    if (!schema) return [];
    const q = filter.trim().toLocaleLowerCase("tr");
    const match = (o: QueryObject) => !q || [o.name, o.business_name, o.subject_area, ...o.columns.map((c) => `${c.name} ${c.business_name ?? ""}`)]
      .join(" ").toLocaleLowerCase("tr").includes(q);
    const leaves: { type: ObjType; domain: string; o?: QueryObject; d?: QueryDataset }[] = [
      ...schema.objects.filter(match).map((o) => ({ type: typeOf(o.kind), domain: o.subject_area || "Diğer", o })),
      ...schema.datasets.filter((d) => !q || `${d.id} ${d.report_title} ${d.description ?? ""} ${d.subject_area ?? ""}`.toLocaleLowerCase("tr").includes(q))
        .map((d) => ({ type: "dataset" as ObjType, domain: d.subject_area || "Diğer", d })),
    ];
    const byTr = (a: string, b: string) => (a === "Diğer" ? 1 : b === "Diğer" ? -1 : a.localeCompare(b, "tr"));
    const outer = new Map<string, Map<string, typeof leaves>>();
    for (const l of leaves) {
      const [g, sub] = groupBy === "domain" ? [l.domain, l.type] : [l.type, l.domain];
      const m = outer.get(g) ?? new Map();
      m.set(sub, [...(m.get(sub) ?? []), l]);
      outer.set(g, m);
    }
    const gKeys = [...outer.keys()].sort(groupBy === "domain" ? byTr : (a, b) => TYPE_ORDER.indexOf(a as ObjType) - TYPE_ORDER.indexOf(b as ObjType));
    return gKeys.map((g) => {
      const m = outer.get(g)!;
      const sKeys = [...m.keys()].sort(groupBy === "domain" ? (a, b) => TYPE_ORDER.indexOf(a as ObjType) - TYPE_ORDER.indexOf(b as ObjType) : byTr);
      const subs: ExSub[] = sKeys.map((k) => {
        const ls = m.get(k)!;
        return {
          key: `${groupBy}:${g}:${k}`, title: groupBy === "domain" ? TYPE_SHORT[k as ObjType] : k,
          type: groupBy === "domain" ? (k as ObjType) : undefined,
          objs: ls.filter((l) => l.o).map((l) => l.o!), ds: ls.filter((l) => l.d).map((l) => l.d!),
        };
      });
      return { key: `${groupBy}:${g}`, title: groupBy === "domain" ? g : TYPE_TITLE[g as ObjType],
        type: groupBy === "type" ? (g as ObjType) : undefined, count: subs.reduce((n, x) => n + x.objs.length + x.ds.length, 0), subs };
    });
  }, [schema, filter, groupBy]);

  const renderObj = (o: QueryObject) => {
    const isOpen = open[o.id] || (!!filter.trim() && o.columns.some((c) => `${c.name} ${c.business_name ?? ""}`.toLocaleLowerCase("tr").includes(filter.trim().toLocaleLowerCase("tr"))));
    return (
      <div key={o.id} className="qp-obj">
        <div className="qp-obj-row">
          <button type="button" className="qp-caret" onClick={() => setOpen((st) => ({ ...st, [o.id]: !isOpen }))} aria-expanded={isOpen} aria-label="Kolonları göster">
            <Ico size={11} d={isOpen ? "M4 6l4 4 4-4" : "M6 4l4 4-4 4"} />
          </button>
          <button type="button" className="qp-obj-name" onClick={() => insert(o.name)} title={`${o.business_name ?? ""}${o.description ? "\n" + o.description : ""}\nTıkla: editöre ekle`}>
            <span className={`qp-kind k-${typeOf(o.kind)}`}>{TYPE_BADGE[typeOf(o.kind)]}</span>
            <span className="qp-obj-label">{o.name}</span>
          </button>
          <button type="button" className="qp-mini" onClick={() => preview(o)} title="İlk 100 satırı getir"><Ico size={12} d="M5 3.5 12 8l-7 4.5z" /></button>
        </div>
        {o.documented === false ? <div className="qp-obj-bn muted"><span className="qp-undoc" title="Veritabanında var, veri sözlüğünde tanımlı değil">sözlükte yok</span>{o.description ? ` ${o.description}` : ""}</div>
          : o.business_name ? <div className="qp-obj-bn muted">{o.business_name}</div> : null}
        {isOpen ? (
          <ul className="qp-cols">
            {o.columns.map((c) => (
              <li key={c.name}>
                <button type="button" className={`qp-col${c.blocked ? " is-blocked" : ""}`} onClick={() => insert(c.name)}
                  title={[c.business_name, c.description, c.blocked ? "Kişisel veri (PII): bu rolle sorgulanamaz" : ""].filter(Boolean).join("\n")}>
                  <span className="qp-col-name">{c.blocked ? "🔒 " : ""}{c.name}</span>
                  <span className="qp-col-type">{c.type}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    );
  };
  const renderDs = (d: QueryDataset) => (
    <button key={`${d.report_id}:${d.id}`} type="button" className="qp-ds" onClick={() => replaceAll(`-- ${d.report_title} · ${d.id}\n${d.sql.trim()}\n`)}
      title={`${d.description ?? ""}${d.domains?.length ? `\nDomain: ${d.domains.join(", ")}` : ""}\nTıkla: SQL'i editöre yükle`}>
      <span className="qp-ds-id"><span className="qp-kind k-dataset">D</span>{d.id}</span>
      <span className="qp-ds-rep muted">{d.report_title}{d.view ? ` · ${d.view}` : ""}</span>
    </button>
  );

  const download = () => {
    if (!result?.ok) return;
    const blob = new Blob([toCsv(result)], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `sorgu_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div className={`qp${collapsed ? " is-collapsed" : ""}`} data-theme-hint={theme}>
      {collapsed ? (
        <aside className="qp-explorer qp-explorer-mini" aria-label="Yetkili nesneler">
          <button type="button" className="qp-ex-toggle" onClick={() => setCollapsed(false)} title="Yetkili nesneleri göster" aria-label="Yetkili nesneleri göster">
            <Ico d="M6 3.5 10.5 8 6 12.5" />
          </button>
          <button type="button" className="qp-ex-vertical" onClick={() => setCollapsed(false)}>Yetkili nesneler</button>
        </aside>
      ) : (
      <aside className="qp-explorer" aria-label="Yetkili nesneler">
        <div className="qp-ex-head">
          <div className="qp-ex-titlebar">
            <div className="qp-ex-title">Yetkili nesneler</div>
            <button type="button" className="qp-ex-toggle" onClick={() => setCollapsed(true)} title="Paneli daralt" aria-label="Paneli daralt">
              <Ico d="M10 3.5 5.5 8 10 12.5" />
            </button>
          </div>
          <div className="qp-groupby" role="group" aria-label="Gruplama">
            <span className="muted small">Grupla</span>
            <div className="qp-seg">
              <button type="button" className={groupBy === "domain" ? "is-on" : undefined} onClick={() => setGroupBy("domain")} aria-pressed={groupBy === "domain"}
                title="Domain (konu alanı) → nesne tipi">Domain</button>
              <button type="button" className={groupBy === "type" ? "is-on" : undefined} onClick={() => setGroupBy("type")} aria-pressed={groupBy === "type"}
                title="Nesne tipi (tablo / view / SP / dataset) → domain">Nesne tipi</button>
            </div>
          </div>
          <label className="qp-search">
            <Ico d="M7 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10ZM10.7 10.7 14 14" />
            <input type="search" placeholder="Tablo, kolon, alan ara…" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Nesne ara" />
          </label>
        </div>
        <div className="qp-tree">
          {loadError ? <p className="qp-err">Şema alınamadı: {loadError}</p> : null}
          {!schema && !loadError ? <p className="muted small qp-pad"><span className="spinner" /> Yükleniyor…</p> : null}
          {schema && !groups.length ? <p className="muted small qp-pad">Aramaya uyan nesne yok.</p> : null}
          {groups.map((g) => {
            const gOpen = filter.trim() ? g.count > 0 : (groupOpen[g.key] ?? true);
            return (
            <section key={g.key} className="qp-group">
              <button type="button" className="qp-group-title" onClick={() => setGroupOpen((st) => ({ ...st, [g.key]: !gOpen }))} aria-expanded={gOpen}>
                <Ico size={10} d={gOpen ? "M4 6l4 4 4-4" : "M6 4l4 4-4 4"} />
                {g.type ? <span className={`qp-kind k-${g.type}`}>{TYPE_BADGE[g.type]}</span> : null}
                <span className="qp-group-name">{g.title}</span>
                <span className="qp-count">{g.count}</span>
              </button>
              {gOpen ? g.subs.map((sub) => (
                <div key={sub.key} className="qp-sub">
                  {(
                    <div className="qp-sub-title">
                      {sub.type ? <span className={`qp-kind qp-kind-sm k-${sub.type}`}>{TYPE_BADGE[sub.type]}</span> : null}
                      <span>{sub.title}</span>
                      <span className="qp-sub-count">{sub.objs.length + sub.ds.length}</span>
                    </div>
                  )}
                  {sub.objs.map(renderObj)}
                  {sub.ds.map(renderDs)}
                </div>
              )) : null}
            </section>
            );
          })}
        </div>
      </aside>
      )}

      <section className="qp-main">
        <div className="qp-toolbar">
          <button type="button" className="btn btn-primary btn-sm" onClick={() => void run()} disabled={running || !schema} title="Çalıştır (Ctrl+Enter / F5)">
            {running ? <span className="spinner" /> : <Ico d="M5 3.5 12 8l-7 4.5z" />}Çalıştır
          </button>
          <span className="qp-hint muted small">Ctrl+Enter · F5 · Ctrl+Space: öneriler · Seçili metin varsa yalnızca o çalışır</span>
          <button type="button" className="btn btn-secondary btn-sm" disabled={!schema}
            onClick={() => { const t = editor.current?.runText() ?? ""; if (t) setSending(t); }}
            title="Bu sorgunun sonucuyla dashboard hazırla: yeni rapor açılır ya da mevcut bir rapora veri kümesi olarak eklenir">
            <Ico d="M2.5 13.5h11M4 11V7M8 11V4M12 11V8.5" />Dashboard hazırla
          </button>
          <span className="qp-spacer" />
          <span className="qp-badge" title="Sorgular yalnızca okuma yetkisiyle çalışır; INSERT/UPDATE/DELETE/DDL ve yetkisiz şemalar engellenir.">
            <Ico size={12} d="M5 7V5a3 3 0 0 1 6 0v2M3.5 7h9v6.5h-9z" /> Salt-okunur · en çok {schema?.max_rows ?? 1000} satır
          </span>
        </div>
        <SqlEditor ref={editor} schema={schema} value={starter} className="qp-editor" testId="sql-editor"
          onRun={() => void run()} onChange={(t) => lsWrite(LS_SQL, t)} />

        <div className="qp-results">
          <QueryResultView result={result} ranSql={ranSql} actions={
            <button type="button" className="btn btn-secondary btn-sm" onClick={download} title="Sonucu CSV (Excel) olarak indir">
              <Ico d="M8 2.5v8M4.5 7 8 10.5 11.5 7M3 13.5h10" />CSV
            </button>
          } />
        </div>
      </section>
      {sending !== null ? (
        <SendToReportDialog api={api} sql={sending} onClose={() => setSending(null)} onDone={(id) => { setSending(null); onOpenReport(id); }} />
      ) : null}
    </div>
  );
}
