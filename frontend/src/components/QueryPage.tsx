// Sorgu Çalıştır: yetkili tablo / view / dataset gezgini + IntelliSense'li SQL editörü + sonuç tablosu.
// Sorgu backend'de rol yetkisiyle doğrulanır (yalnızca SELECT, yetkili şemalar, PII) ve salt-okunur çalışır (en çok 1000 satır).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { EditorView, basicSetup } from "codemirror";
import { keymap, placeholder } from "@codemirror/view";
import { EditorState, Prec } from "@codemirror/state";
import { sql as sqlLang, MSSQL } from "@codemirror/lang-sql";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { buildCompletion } from "../lib/sqlComplete";
import { TYPE_BADGE, TYPE_ORDER, TYPE_SHORT, TYPE_TITLE, typeOfKind, type GroupBy, type ObjType } from "../lib/objectTypes";
import { tags as t } from "@lezer/highlight";
import type { Api } from "../api/client";
import type { QueryDataset, QueryObject, QueryProcedure, QueryRunResult, QuerySchema } from "../types";
import "./query.css";

const LS_SQL = "bi.query.sql";
const LS_EXPLORER = "bi.query.explorerCollapsed";
const LS_GROUPBY = "bi.query.groupBy";
const typeOf = typeOfKind;
interface ExSub { key: string; title: string; type?: ObjType; objs: QueryObject[]; ds: QueryDataset[]; sps: QueryProcedure[] }
interface ExGroup { key: string; title: string; type?: ObjType; count: number; subs: ExSub[] }
const lsRead = (k: string) => { try { return localStorage.getItem(k); } catch { return null; } };
const lsWrite = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* yoksay */ } };

const highlight = HighlightStyle.define([
  { tag: t.keyword, color: "var(--sql-kw)", fontWeight: "600" },
  { tag: [t.string, t.special(t.string)], color: "var(--sql-str)" },
  { tag: [t.number, t.bool, t.null], color: "var(--sql-num)" },
  { tag: [t.lineComment, t.blockComment], color: "var(--sql-comment)", fontStyle: "italic" },
  { tag: [t.typeName, t.standard(t.name)], color: "var(--sql-type)" },
  { tag: [t.operator, t.punctuation], color: "var(--text-2)" },
  { tag: t.special(t.name), color: "var(--sql-type)" },
]);

const editorTheme = EditorView.theme({
  "&": { height: "100%", fontSize: "13.5px", backgroundColor: "var(--surface)", color: "var(--text)" },
  ".cm-scroller": { fontFamily: "var(--mono)", lineHeight: "1.55" },
  ".cm-content": { caretColor: "var(--accent)" },
  ".cm-cursor": { borderLeftColor: "var(--accent)" },
  ".cm-gutters": { backgroundColor: "var(--surface-2)", color: "var(--muted)", border: "none", borderRight: "1px solid var(--border)" },
  ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--accent) 6%, transparent)" },
  ".cm-activeLineGutter": { backgroundColor: "color-mix(in srgb, var(--accent) 10%, transparent)", color: "var(--text)" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": { backgroundColor: "color-mix(in srgb, var(--accent) 25%, transparent) !important" },
  ".cm-tooltip": { backgroundColor: "var(--surface)", border: "1px solid var(--border)", borderRadius: "8px", boxShadow: "var(--shadow-md)", color: "var(--text)" },
  ".cm-tooltip-autocomplete > ul > li[aria-selected]": { backgroundColor: "var(--accent-soft)", color: "var(--text)" },
  ".cm-completionDetail": { color: "var(--muted)", fontStyle: "normal", marginLeft: "8px" },
  ".cm-completionInfo": { padding: "6px 10px", maxWidth: "320px", fontSize: "12.5px" },
  ".cm-placeholder": { color: "var(--muted)" },
  ".cm-matchingBracket": { backgroundColor: "color-mix(in srgb, var(--accent) 20%, transparent)", outline: "none" },
});

function fmt(v: unknown, type: string): string {
  if (v === null || v === undefined) return "NULL";
  if (type === "number" && typeof v === "number") return v.toLocaleString("tr-TR", { maximumFractionDigits: 6 });
  return String(v);
}

function toCsv(res: QueryRunResult): string {
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[";\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [(res.columns ?? []).map(esc).join(";"), ...(res.rows ?? []).map((r) => r.map(esc).join(";"))];
  return "﻿" + lines.join("\r\n");
}

const Ico = ({ d, size = 14 }: { d: string; size?: number }) => (
  <svg viewBox="0 0 16 16" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
);

export function QueryPage({ api, theme }: { api: Api; theme: string }) {
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
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const runRef = useRef<() => void>(() => {});

  useEffect(() => {
    let alive = true;
    api.querySchema().then((s) => alive && setSchema(s)).catch((e) => alive && setLoadError(e instanceof Error ? e.message : String(e)));
    return () => { alive = false; };
  }, [api]);

  const run = useCallback(async () => {
    const v = view.current;
    if (!v || running) return;
    const sel = v.state.selection.main;
    const text = (sel.empty ? v.state.doc.toString() : v.state.sliceDoc(sel.from, sel.to)).trim();
    if (!text) return;
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
  runRef.current = () => void run();

  // editör: şema gelince (IntelliSense için) kurulur
  useEffect(() => {
    if (!host.current || !schema) return;
    const { source, ns } = buildCompletion(schema);
    let initial = "";
    try { initial = localStorage.getItem(LS_SQL) ?? ""; } catch { /* yoksay */ }
    const first = schema.objects.find((o) => o.kind === "fact") ?? schema.objects[0];
    if (!initial && first) {
      const cols = first.columns.filter((c) => !c.blocked).slice(0, 5).map((c) => c.name).join(", ");
      initial = `-- Ctrl+Enter / F5: çalıştır (seçili metin varsa yalnızca o). En çok ${schema.max_rows} satır döner.\nSELECT TOP 100 ${cols || "*"}\nFROM ${first.name};\n`;
    }
    const lang = sqlLang({ dialect: MSSQL, schema: ns, upperCaseKeywords: true });
    const ev = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: initial,
        extensions: [
          Prec.highest(keymap.of([
            { key: "Mod-Enter", run: () => { runRef.current(); return true; } },
            { key: "F5", run: () => { runRef.current(); return true; }, preventDefault: true },
          ])),
          basicSetup,
          lang,
          MSSQL.language.data.of({ autocomplete: source }),
          syntaxHighlighting(highlight),
          editorTheme,
          EditorView.lineWrapping,
          placeholder("SELECT … FROM dbo.Tablo"),
          EditorView.updateListener.of((u) => {
            if (u.docChanged) { try { localStorage.setItem(LS_SQL, u.state.doc.toString()); } catch { /* yoksay */ } }
          }),
        ],
      }),
    });
    view.current = ev;
    return () => { ev.destroy(); view.current = null; };
  }, [schema]);

  const insert = (text: string) => {
    const v = view.current;
    if (!v) return;
    const sel = v.state.selection.main;
    v.dispatch({ changes: { from: sel.from, to: sel.to, insert: text }, selection: { anchor: sel.from + text.length } });
    v.focus();
  };
  const replaceAll = (text: string, runNow = false) => {
    const v = view.current;
    if (!v) return;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: text }, selection: { anchor: text.length } });
    v.focus();
    if (runNow) setTimeout(() => runRef.current(), 0);
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
    const leaves: { type: ObjType; domain: string; o?: QueryObject; d?: QueryDataset; p?: QueryProcedure }[] = [
      ...schema.objects.filter(match).map((o) => ({ type: typeOf(o.kind), domain: o.subject_area || "Diğer", o })),
      ...(schema.procedures ?? []).filter((p) => !q || `${p.name} ${p.description ?? ""} ${p.subject_area} ${p.parameters.join(" ")}`.toLocaleLowerCase("tr").includes(q))
        .map((p) => ({ type: "procedure" as ObjType, domain: p.subject_area || "Diğer", p })),
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
          sps: ls.filter((l) => l.p).map((l) => l.p!),
        };
      });
      return { key: `${groupBy}:${g}`, title: groupBy === "domain" ? g : TYPE_TITLE[g as ObjType],
        type: groupBy === "type" ? (g as ObjType) : undefined, count: subs.reduce((n, x) => n + x.objs.length + x.ds.length + x.sps.length, 0), subs };
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
  const renderSp = (sp: QueryProcedure) => (
    <div key={sp.id} className="qp-obj">
      <div className="qp-obj-row">
        <span className="qp-caret" aria-hidden="true" />
        <button type="button" className="qp-obj-name" onClick={() => insert(sp.name)}
          title={[sp.description, sp.parameters.length ? `Parametreler: ${sp.parameters.join(", ")}` : "Parametre yok",
            sp.tables.length ? `Kullandığı tablolar: ${sp.tables.join(", ")}` : "",
            "Salt-okunur sorgu ekranında SP çalıştırılamaz (EXEC engelli). Tıkla: adını editöre ekle"].filter(Boolean).join("\n")}>
          <span className="qp-kind k-procedure">SP</span>
          <span className="qp-obj-label">{sp.name}</span>
        </button>
      </div>
      <div className="qp-obj-bn muted">{sp.parameters.length ? sp.parameters.join(", ") : sp.description || "Parametre yok"}</div>
    </div>
  );
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
                      <span className="qp-sub-count">{sub.objs.length + sub.ds.length + sub.sps.length}</span>
                    </div>
                  )}
                  {sub.objs.map(renderObj)}
                  {sub.sps.map(renderSp)}
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
          <span className="qp-spacer" />
          <span className="qp-badge" title="Sorgular yalnızca okuma yetkisiyle çalışır; INSERT/UPDATE/DELETE/DDL ve yetkisiz şemalar engellenir.">
            <Ico size={12} d="M5 7V5a3 3 0 0 1 6 0v2M3.5 7h9v6.5h-9z" /> Salt-okunur · en çok {schema?.max_rows ?? 1000} satır
          </span>
        </div>
        <div className="qp-editor" ref={host} />

        <div className="qp-results">
          {!result ? <div className="qp-empty muted">Sorgu sonucu burada görünecek.</div> : null}
          {result && !result.ok ? (
            <div className="qp-msg is-bad" role="alert">
              <b>Sorgu çalıştırılmadı</b>
              <ul>{(result.errors ?? []).map((e, i) => <li key={i}>{e}</li>)}</ul>
            </div>
          ) : null}
          {result?.warnings?.length ? (
            <div className="qp-msg is-warn"><b>Uyarı</b><ul>{result.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></div>
          ) : null}
          {result?.ok ? (
            <>
              <div className="qp-res-head">
                <span><b>{(result.rows?.length ?? 0).toLocaleString("tr-TR")}</b> satır · {result.columns?.length} kolon · {result.elapsed_ms} ms</span>
                {result.truncated ? <span className="qp-trunc">İlk {result.row_limit?.toLocaleString("tr-TR")} satır gösteriliyor (sınır). Daha azı için WHERE / TOP kullanın.</span> : null}
                <span className="qp-spacer" />
                <button type="button" className="btn btn-secondary btn-sm" onClick={download} title="Sonucu CSV (Excel) olarak indir">
                  <Ico d="M8 2.5v8M4.5 7 8 10.5 11.5 7M3 13.5h10" />CSV
                </button>
              </div>
              <div className="qp-grid-wrap">
                <table className="qp-grid">
                  <thead>
                    <tr><th className="qp-rn">#</th>{result.columns?.map((c, i) => <th key={i} className={result.types?.[i] === "number" ? "is-num" : undefined}>{c}</th>)}</tr>
                  </thead>
                  <tbody>
                    {result.rows?.map((r, ri) => (
                      <tr key={ri}>
                        <td className="qp-rn">{ri + 1}</td>
                        {r.map((v, ci) => {
                          const ty = result.types?.[ci] ?? "string";
                          return <td key={ci} className={`${ty === "number" ? "is-num" : ""}${v === null ? " is-null" : ""}`}>{fmt(v, ty)}</td>;
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!result.rows?.length ? <div className="qp-empty muted">Sorgu satır döndürmedi.</div> : null}
              </div>
            </>
          ) : null}
          {result && ranSql ? <details className="qp-ran"><summary className="muted small">Çalıştırılan SQL</summary><pre>{ranSql}</pre></details> : null}
        </div>
      </section>
    </div>
  );
}
