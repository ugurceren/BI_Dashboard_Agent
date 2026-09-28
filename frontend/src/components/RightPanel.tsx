// Sağ panel: Dashboard / Veri / Model / Spec sekmeleri.
import { useEffect, useMemo, useRef, useState } from "react";
import type { DashboardData, Dataset, DictionaryHit, ReportSpec, SessionState } from "../types";
import { DashboardRenderer } from "../dashboard/DashboardRenderer";
import { formatValue } from "../dashboard/format";
import { ApiError, type Api } from "../api/client";
import { ModelTab } from "./ModelTab";
import type { ModelFiltering } from "../dashboard/DashboardRenderer";

export type Tab = "dashboard" | "data" | "model" | "spec";

export function RightPanel({ api, state, data, dataLoading, dataError, tab, setTab, onSpecApplied, onReloadData, model }: {
  api: Api;
  state: SessionState | null;
  data: DashboardData | null;
  dataLoading: boolean;
  dataError: string | null;
  tab: Tab;
  setTab: (t: Tab) => void;
  onSpecApplied: (s: SessionState) => void;
  onReloadData: () => void;
  /** model filtreleri (Power BI tarzı); yoksa istemci tarafı filtre */
  model?: ModelFiltering;
}) {
  const spec = state?.spec ?? null;
  const datasetCount = useMemo(() => mergeDatasets(state).length, [state]);
  return (
    <section className="right">
      <div className="right-bar">
        <div className="tabs" role="tablist">
          <TabBtn id="dashboard" tab={tab} setTab={setTab} label="Dashboard" />
          <TabBtn id="data" tab={tab} setTab={setTab} label="Veri" count={datasetCount || undefined} />
          <TabBtn id="model" tab={tab} setTab={setTab} label="Model" />
          <TabBtn id="spec" tab={tab} setTab={setTab} label="Spec" />
        </div>
        <div className="right-tools">
          {state?.spec_version ? <span className="muted small">sürüm {state.spec_version}</span> : null}
          <button type="button" className="btn btn-ghost btn-sm" onClick={onReloadData} disabled={!state || dataLoading} title="Veriyi yeniden çek">
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" className={dataLoading ? "spin" : undefined}><path d="M13.5 8a5.5 5.5 0 1 1-1.7-4M13.5 2.5v3h-3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
            <span className="btn-label">Yenile</span>
          </button>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={!state || !spec}
            onClick={() => state && window.open(api.exportUrl(state.id), "_blank", "noopener")}
            title={spec ? "Bağımsız HTML dosyası olarak indir" : "Önce bir dashboard oluşturun"}
          >
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M8 2v8.5M4.5 7 8 10.5 11.5 7M2.5 13.5h11" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
            <span className="btn-label">HTML indir</span>
          </button>
        </div>
      </div>
      <div className="right-body">
        {tab === "dashboard" ? (
          <DashboardTab spec={spec} data={data} loading={dataLoading} error={dataError} phase={state?.phase} model={model} />
        ) : tab === "data" ? (
          <DataTab api={api} state={state} data={data} />
        ) : tab === "model" ? (
          <ModelTab api={api} state={state} />
        ) : (
          <SpecTab api={api} state={state} onApplied={onSpecApplied} />
        )}
      </div>
    </section>
  );
}

function TabBtn({ id, tab, setTab, label, count }: { id: Tab; tab: Tab; setTab: (t: Tab) => void; label: string; count?: number }) {
  return (
    <button type="button" role="tab" aria-selected={tab === id} className={`tab${tab === id ? " is-on" : ""}`} onClick={() => setTab(id)}>
      {label}
      {count ? <span className="tab-count">{count}</span> : null}
    </button>
  );
}

function mergeDatasets(state: SessionState | null): Dataset[] {
  if (!state) return [];
  const map = new Map<string, Dataset>();
  for (const d of state.datasets ?? []) map.set(d.id, d);
  for (const d of state.spec?.datasets ?? []) map.set(d.id, { ...map.get(d.id), ...d });
  return [...map.values()];
}

// ---------- Dashboard ----------

function DashboardTab({ spec, data, loading, error, phase, model }: {
  spec: ReportSpec | null; data: DashboardData | null; loading: boolean; error: string | null; phase?: string; model?: ModelFiltering;
}) {
  if (!spec) {
    return (
      <div className="panel-empty">
        <div className="panel-empty-art" aria-hidden="true">
          <span style={{ gridColumn: "1 / span 1" }} /><span /><span /><span />
          <span className="wide" /><span className="tall" />
        </div>
        <h3>Henüz dashboard yok</h3>
        <p>
          {phase === "design"
            ? "Tasarımı tarif edin ya da örnek bir dashboard görseli yapıştırın; agent rapor tanımını üretecek."
            : "Agent önce ihtiyacı netleştirir, ardından veriyi hazırlar. Tasarım fazında dashboard burada görünecek."}
        </p>
      </div>
    );
  }
  return (
    <div className="dash-scroll">
      {error ? <div className="banner-error">Veri alınamadı: {error}</div> : null}
      {data ? (
        <DashboardRenderer spec={spec} data={data} loading={loading} model={model} />
      ) : (
        <div className="panel-empty"><span className="spinner" /> <p>Veri yükleniyor…</p></div>
      )}
    </div>
  );
}

// ---------- Veri ----------

function DataTab({ api, state, data }: { api: Api; state: SessionState | null; data: DashboardData | null }) {
  const datasets = useMemo(() => mergeDatasets(state), [state]);
  return (
    <div className="data-tab">
      <DictionarySearch api={api} />
      {datasets.length === 0 ? (
        <div className="panel-empty small-empty">
          <h3>Kayıtlı veri kümesi yok</h3>
          <p>Veri fazında agent veri sözlüğünü arayıp SQL yazdıkça veri kümeleri burada listelenir.</p>
        </div>
      ) : (
        datasets.map((d) => <DatasetCard key={d.id} ds={d} data={data} usedInSpec={!!state?.spec?.datasets?.some((x) => x.id === d.id)} />)
      )}
    </div>
  );
}

function DatasetCard({ ds, data, usedInSpec }: { ds: Dataset; data: DashboardData | null; usedInSpec: boolean }) {
  const [showSql, setShowSql] = useState(false);
  const d = data?.datasets?.[ds.id];
  const rows = d?.rows?.slice(0, 50) ?? [];
  const fieldMap = new Map((ds.fields ?? []).map((f) => [f.name, f]));
  return (
    <div className="ds-card">
      <div className="ds-head">
        <div>
          <div className="ds-id"><code>{ds.id}</code>{usedInSpec ? <span className="pill">spec</span> : <span className="pill pill-muted">taslak</span>}</div>
          {ds.description ? <div className="ds-desc">{ds.description}</div> : null}
        </div>
        <div className="ds-meta muted small">
          {d && !d.error ? `${d.rows.length.toLocaleString("tr-TR")} satır · ${d.columns.length} kolon` : null}
        </div>
      </div>
      {ds.fields?.length ? (
        <div className="ds-fields">
          {ds.fields.map((f) => (
            <span key={f.name} className="field-chip" title={`${f.name} · ${f.type}${f.format ? ` · ${f.format}` : ""}`}>
              <span className={`ft ft-${f.type}`}>{f.type === "number" ? "#" : f.type === "date" ? "▦" : "Aa"}</span>
              {f.label || f.name}
            </span>
          ))}
        </div>
      ) : null}
      <button type="button" className="link-btn" onClick={() => setShowSql((s) => !s)} aria-expanded={showSql}>
        <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true" style={{ transform: showSql ? "rotate(90deg)" : undefined, transition: "transform .15s" }}><path d="M4.5 3 7.5 6l-3 3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
        SQL
      </button>
      {showSql ? <pre className="code-block sql">{ds.sql}</pre> : null}
      {d?.error ? <div className="banner-error">Sorgu hatası: {d.error}</div> : null}
      {d && !d.error ? (
        rows.length ? (
          <div className="preview-wrap">
            <table className="preview">
              <thead>
                <tr>{d.columns.map((c) => <th key={c} className={fieldMap.get(c)?.type === "number" ? "num" : undefined}>{fieldMap.get(c)?.label || c}</th>)}</tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i}>
                    {r.map((v, j) => {
                      const f = fieldMap.get(d.columns[j]);
                      const num = typeof v === "number";
                      return (
                        <td key={j} className={num ? "num" : undefined}>
                          {v === null ? <span className="muted">null</span> : num ? formatValue(v, { format: f?.format === "percent" ? "percent" : f?.format === "currency" ? "currency" : "number", decimals: f?.format === "currency" ? 2 : undefined }) : String(v)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            {d.rows.length > 50 ? <div className="muted small preview-more">İlk 50 satır gösteriliyor ({d.rows.length.toLocaleString("tr-TR")} toplam)</div> : null}
          </div>
        ) : (
          <div className="muted small">Sorgu satır döndürmedi.</div>
        )
      ) : !d ? (
        <div className="muted small">Önizleme verisi yok.</div>
      ) : null}
    </div>
  );
}

function DictionarySearch({ api }: { api: Api }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<DictionaryHit[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const reqId = useRef(0);
  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) {
      setHits(null);
      setErr(null);
      return;
    }
    const id = ++reqId.current;
    const t = setTimeout(async () => {
      setLoading(true);
      try {
        const r = await api.searchDictionary(term);
        if (id === reqId.current) {
          setHits(r);
          setErr(null);
        }
      } catch (e) {
        if (id === reqId.current) setErr(e instanceof Error ? e.message : String(e));
      } finally {
        if (id === reqId.current) setLoading(false);
      }
    }, 300);
    return () => clearTimeout(t);
  }, [q, api]);
  return (
    <div className="dict">
      <div className="dict-input">
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M7 12.5a5.5 5.5 0 1 1 0-11 5.5 5.5 0 0 1 0 11Zm4-1.5 3.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Veri sözlüğünde ara… (ör. kart işlem tutarı, şube, bölge)" />
        {loading ? <span className="spinner" /> : null}
      </div>
      {err ? <div className="banner-error">{err}</div> : null}
      {hits ? (
        hits.length ? (
          <div className="dict-hits">
            {hits.map((h) => (
              <div className="dict-hit" key={h.table}>
                <div className="dict-hit-head">
                  <code>{h.table}</code>
                  <span className="dict-bn">{h.business_name}</span>
                  <span className="dict-score" title="Eşleşme skoru">{formatValue(h.score, { format: "number", decimals: 2 })}</span>
                </div>
                {h.description ? <div className="muted small">{h.description}</div> : null}
                {h.columns?.length ? (
                  <div className="ds-fields">
                    {h.columns.map((c) => (
                      <span className="field-chip" key={c.name} title={`${c.name} · ${c.role}`}>
                        <span className={`ft ft-role-${c.role}`}>{c.role === "measure" ? "#" : c.role === "key" ? "⚿" : "Aa"}</span>
                        {c.business_name || c.name}
                        <code className="field-col">{c.name}</code>
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        ) : (
          <div className="muted small dict-none">Eşleşme bulunamadı.</div>
        )
      ) : null}
    </div>
  );
}

// ---------- Spec ----------

function SpecTab({ api, state, onApplied }: { api: Api; state: SessionState | null; onApplied: (s: SessionState) => void }) {
  const pretty = useMemo(() => (state?.spec ? JSON.stringify(state.spec, null, 2) : ""), [state?.spec]);
  const [text, setText] = useState(pretty);
  const [dirty, setDirty] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [ok, setOk] = useState(false);
  useEffect(() => {
    if (!dirty) setText(pretty);
  }, [pretty, dirty]);

  const parseError = useMemo(() => {
    if (!text.trim()) return null;
    try {
      JSON.parse(text);
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : String(e);
    }
  }, [text]);

  const apply = async () => {
    if (!state || parseError) return;
    setSaving(true);
    setErrors([]);
    setOk(false);
    try {
      const s = await api.putSpec(state.id, JSON.parse(text));
      setDirty(false);
      setOk(true);
      onApplied(s);
      setTimeout(() => setOk(false), 2500);
    } catch (e) {
      if (e instanceof ApiError && e.detail.length) setErrors(e.detail);
      else setErrors([e instanceof Error ? e.message : String(e)]);
    } finally {
      setSaving(false);
    }
  };

  if (!state) return null;
  return (
    <div className="spec-tab">
      <div className="spec-bar">
        <div className="muted small">
          {state.spec ? "Rapor tanımını (ReportSpec) doğrudan düzenleyin." : "Henüz spec yok — bir ReportSpec JSON'u yapıştırıp uygulayabilirsiniz."}
          {dirty ? <span className="pill pill-warn">kaydedilmedi</span> : null}
          {ok ? <span className="pill pill-ok">uygulandı</span> : null}
        </div>
        <div className="spec-actions">
          <button type="button" className="btn btn-ghost btn-sm" disabled={!dirty || saving} onClick={() => { setText(pretty); setDirty(false); setErrors([]); }}>
            Geri al
          </button>
          <button type="button" className="btn btn-primary btn-sm" disabled={!dirty || !!parseError || saving || !text.trim()} onClick={apply}>
            {saving ? <span className="spinner spinner-light" /> : null}
            Uygula
          </button>
        </div>
      </div>
      {parseError ? <div className="banner-error">JSON hatası: {parseError}</div> : null}
      {errors.length ? (
        <div className="banner-error">
          <b>Spec doğrulanamadı:</b>
          <ul>{errors.map((e, i) => <li key={i}>{e}</li>)}</ul>
        </div>
      ) : null}
      <textarea
        className="spec-editor"
        spellCheck={false}
        value={text}
        onChange={(e) => { setText(e.target.value); setDirty(true); }}
        onKeyDown={(e) => {
          if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); void apply(); }
          if (e.key === "Tab") {
            e.preventDefault();
            const el = e.currentTarget;
            const s = el.selectionStart, en = el.selectionEnd;
            const v = el.value.slice(0, s) + "  " + el.value.slice(en);
            setText(v); setDirty(true);
            requestAnimationFrame(() => { el.selectionStart = el.selectionEnd = s + 2; });
          }
        }}
        placeholder='{ "version": 1, "title": "...", ... }'
      />
    </div>
  );
}
