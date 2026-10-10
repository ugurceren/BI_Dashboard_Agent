// Sorgu modu (İhtiyaç / Veri fazı): kullanıcı hazır SQL sorgularını yapıştırır / yazar, önizler ve "Dashboard'a geç" ile
// veri kümesi olarak kaydeder → Tasarım fazı. Önizleme, dataset kaydı ve dashboard ile aynı doğrulamadan geçer.
// Taslaklar oturumda saklanır (sayfa yenilense ya da sohbet moduna geçilse de kaybolmaz).
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, type Api } from "../api/client";
import type { QueryDraft, QueryRunResult, QuerySchema, SessionState } from "../types";
import { slugifyId } from "../lib/slug";
import { SqlEditor, type SqlEditorHandle } from "./SqlEditor";
import { QueryResultView } from "./QueryResult";
import "./query.css";
import "./querymode.css";

interface Draft extends QueryDraft { key: string }

let schemaCache: Promise<QuerySchema> | null = null;
const loadSchema = (api: Api) => {
  if (!schemaCache) schemaCache = api.querySchema().catch((e) => { schemaCache = null; throw e; });
  return schemaCache;
};
let keySeq = 0;
const newKey = () => `q${++keySeq}`;
const toDrafts = (list: QueryDraft[] | undefined): Draft[] =>
  (list?.length ? list : [{ id: "sorgu_1", title: "", sql: "" }]).map((d) => ({ ...d, key: newKey() }));
const nextName = (drafts: Draft[]) => {
  let i = drafts.length + 1;
  while (drafts.some((d) => d.id === `sorgu_${i}`)) i++;
  return `sorgu_${i}`;
};

export function QueryMode({ api, state, disabled, onState, onSubmitted }: {
  api: Api;
  state: SessionState;
  disabled?: boolean;
  onState: (s: SessionState) => void;
  /** veri kümeleri kaydedildi, tasarım fazına geçildi */
  onSubmitted: (ids: string[]) => void;
}) {
  const [schema, setSchema] = useState<QuerySchema | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>(() => toDrafts(state.query_drafts));
  const [active, setActive] = useState(drafts[0]?.key ?? "");
  const [results, setResults] = useState<Record<string, { result: QueryRunResult; ranSql: string }>>({});
  const [running, setRunning] = useState(false);
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const editor = useRef<SqlEditorHandle>(null);
  const sid = state.id;

  useEffect(() => {
    let alive = true;
    loadSchema(api).then((s) => alive && setSchema(s)).catch(() => { /* IntelliSense olmadan da çalışır */ });
    return () => { alive = false; };
  }, [api]);

  // başka rapora geçildi: taslaklar o raporunkiler
  const loadedFor = useRef(sid);
  useEffect(() => {
    if (loadedFor.current === sid) return;
    loadedFor.current = sid;
    const d = toDrafts(state.query_drafts);
    setDrafts(d); setActive(d[0].key); setResults({}); setErrors([]);
  }, [sid, state.query_drafts]);

  // taslaklar oturuma kaydedilir (yazarken sık istek atmamak için gecikmeli)
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    const t = setTimeout(() => {
      void api.saveQueryDrafts(sid, { drafts: drafts.map(({ id, title, sql }) => ({ id, title, sql })) }).catch(() => { /* bir sonraki değişiklikte yeniden */ });
    }, 700);
    return () => clearTimeout(t);
  }, [drafts, api, sid]);

  const cur = drafts.find((d) => d.key === active) ?? drafts[0];
  const patch = (key: string, p: Partial<QueryDraft>) => setDrafts((ds) => ds.map((d) => (d.key === key ? { ...d, ...p } : d)));
  const add = () => {
    const d: Draft = { id: nextName(drafts), title: "", sql: "", key: newKey() };
    setDrafts((ds) => [...ds, d]);
    setActive(d.key);
  };
  const remove = (key: string) => {
    if (drafts.length === 1) { patch(key, { sql: "", title: "" }); return; }
    const idx = drafts.findIndex((d) => d.key === key);
    const rest = drafts.filter((d) => d.key !== key);
    setDrafts(rest);
    setActive(rest[Math.max(0, idx - 1)].key);
  };

  const preview = async () => {
    if (!cur || running) return;
    const text = editor.current?.runText() ?? cur.sql.trim();
    if (!text) return;
    setRunning(true);
    const key = cur.key;
    try {
      const result = await api.queryPreview(sid, text);
      setResults((r) => ({ ...r, [key]: { result, ranSql: text } }));
    } catch (e) {
      setResults((r) => ({ ...r, [key]: { result: { ok: false, errors: [e instanceof Error ? e.message : String(e)] }, ranSql: text } }));
    } finally {
      setRunning(false);
    }
  };

  const ready = drafts.filter((d) => d.sql.trim());
  const ids = ready.map((d) => slugifyId(d.id || d.title));
  const dupes = useMemo(() => ids.filter((x, i) => ids.indexOf(x) !== i), [ids]);
  const submit = async () => {
    if (!ready.length || saving || dupes.length) return;
    setSaving(true);
    setErrors([]);
    try {
      const payload = ready.map((d, i) => ({ id: ids[i], title: d.title.trim(), sql: d.sql }));
      const s = await api.datasetsFromQuery(sid, payload);
      onState(s);
      onSubmitted(payload.map((d) => d.id));
    } catch (e) {
      setErrors(e instanceof ApiError && e.detail.length ? e.detail : [e instanceof Error ? e.message : String(e)]);
      setSaving(false);
    }
  };

  const res = cur ? results[cur.key] : undefined;
  const slug = cur ? slugifyId(cur.id || cur.title) : "";
  return (
    <div className="qm" data-testid="query-mode">
      <div className="qm-tabs" role="tablist" aria-label="Sorgular">
        {drafts.map((d, i) => {
          const r = results[d.key]?.result;
          return (
            <button key={d.key} type="button" role="tab" aria-selected={d.key === cur?.key} className={`qm-tab${d.key === cur?.key ? " is-on" : ""}`}
              onClick={() => setActive(d.key)} title={d.title || d.id}>
              <span className={`qm-dot${r ? (r.ok ? " is-ok" : " is-bad") : ""}`} aria-hidden="true" />
              {d.id || `Sorgu ${i + 1}`}
            </button>
          );
        })}
        <button type="button" className="qm-tab qm-add" onClick={add} disabled={disabled || drafts.length >= 20} title="Yeni sorgu ekle (KPI, trend, detay için ayrı sorgular)">+ Sorgu</button>
      </div>

      {cur ? (
        <>
          <div className="qm-meta">
            <label className="qm-field">
              <span>Ad</span>
              <input value={cur.id} onChange={(e) => patch(cur.key, { id: e.target.value })} disabled={disabled} aria-label="Veri kümesi adı" placeholder="sube_teminat" />
            </label>
            <label className="qm-field qm-field-wide">
              <span>Açıklama</span>
              <input value={cur.title} onChange={(e) => patch(cur.key, { title: e.target.value })} disabled={disabled} aria-label="Açıklama" placeholder="ör. Şube bazlı teminat tutarları" />
            </label>
            <button type="button" className="btn btn-ghost btn-sm qm-del" onClick={() => remove(cur.key)} disabled={disabled} title="Bu sorguyu sil">Sil</button>
          </div>
          {slug && slug !== cur.id ? <div className="qm-hint muted small">Kaydedilecek ad: <code>{slug}</code></div> : null}
          <SqlEditor ref={editor} key={cur.key} schema={schema} value={cur.sql} className="qm-editor" testId="qm-editor"
            placeholderText="Hazır sorgunuzu yapıştırın: SELECT … FROM …"
            onChange={(t) => patch(cur.key, { sql: t })} onRun={() => void preview()} />
          <div className="qm-toolbar">
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => void preview()} disabled={disabled || running || !cur.sql.trim()} title="Önizle (Ctrl+Enter / F5)">
              {running ? <span className="spinner" /> : null}Önizle
            </button>
            <span className="muted small">Ctrl+Enter · ilk 200 satır · salt-okunur</span>
          </div>
          <div className="qm-results">
            <QueryResultView result={res?.result ?? null} ranSql={res?.ranSql} testId="qm-result"
              empty="Önizleme burada görünür. Önizlemede çalışan sorgu dashboard'da da aynı kurallarla çalışır." />
          </div>
        </>
      ) : null}

      <div className="qm-foot">
        {errors.length ? (
          <div className="qp-msg is-bad" role="alert"><b>Veri kümeleri kaydedilemedi</b><ul>{errors.map((e, i) => <li key={i}>{e}</li>)}</ul></div>
        ) : null}
        {dupes.length ? <div className="qp-msg is-warn"><b>Aynı ad birden çok sorguda:</b> {[...new Set(dupes)].join(", ")}</div> : null}
        <div className="qm-foot-row">
          <span className="muted small">{ready.length} sorgu hazır · her biri bir veri kümesi olur</span>
          <span className="qp-spacer" />
          <button type="button" className="btn btn-primary" onClick={() => void submit()} disabled={disabled || saving || !ready.length || dupes.length > 0}
            title="Sorguları veri kümesi olarak kaydet ve tasarım fazına geç">
            {saving ? <span className="spinner" /> : null}Dashboard'a geç →
          </button>
        </div>
      </div>
    </div>
  );
}
