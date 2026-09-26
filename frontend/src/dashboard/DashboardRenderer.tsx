// Saf bileşen: <DashboardRenderer spec data /> — API çağrısı yok; bağımsız görüntüleyicide de kullanılır.
import { useEffect, useMemo, useRef, useState } from "react";
import type { DashboardData, Filter, ReportSpec } from "../types";
import { buildTables, filterOptions, type FilterState } from "./data";
import { deriveTheme, normalizeTheme, themeCssVars } from "./theme";
import { ErrorCard, VisualBoundary, VisualSwitch } from "./visuals";
import "./dashboard.css";
import { registerFonts } from "./fonts";

registerFonts();

export interface DashboardRendererProps {
  spec: ReportSpec;
  data: DashboardData;
  /** yeniden yüklenirken önceki çizimi soluk tut */
  loading?: boolean;
}

export function DashboardRenderer({ spec, data, loading }: DashboardRendererProps) {
  const theme = useMemo(() => deriveTheme(normalizeTheme(spec?.theme)), [spec?.theme]);
  const [filterState, setFilterState] = useState<FilterState>({});
  const filters = useMemo(() => (Array.isArray(spec?.filters) ? spec.filters : []), [spec?.filters]);
  const safeData = useMemo<DashboardData>(() => (data && data.datasets ? data : { datasets: {} }), [data]);

  // filtre seçenekleri değişince artık olmayan değerleri temizle
  const options = useMemo(() => Object.fromEntries(filters.map((f) => [f.id, filterOptions(f, safeData)])), [filters, safeData]);
  useEffect(() => {
    setFilterState((s) => {
      let changed = false;
      const next: FilterState = {};
      for (const [id, vals] of Object.entries(s)) {
        const opts = options[id];
        if (!opts) { changed = true; continue; }
        const keep = vals.filter((v) => opts.includes(v));
        if (keep.length !== vals.length) changed = true;
        next[id] = keep;
      }
      return changed ? next : s;
    });
  }, [options]);

  const tables = useMemo(() => buildTables(spec, safeData, filterState), [spec, safeData, filterState]);
  const activeFilters = filters.filter((f) => (filterState[f.id]?.length ?? 0) > 0);

  const visuals = useMemo(() => {
    const vs = Array.isArray(spec?.visuals) ? spec.visuals : [];
    return [...vs].sort((a, b) => (a.position?.y ?? 0) - (b.position?.y ?? 0) || (a.position?.x ?? 0) - (b.position?.x ?? 0));
  }, [spec?.visuals]);

  const rowHeight = Math.max(40, Number(spec?.layout?.rowHeight) || 90);
  const datasets = Array.isArray(spec?.datasets) ? spec.datasets : [];
  const banner = theme.headerStyle === "banner";

  return (
    <div
      className={`db-root db-card-${theme.cardStyle ?? "elevated"} db-density-${theme.density ?? "comfortable"}${theme.isDark ? " is-dark" : ""}${loading ? " is-loading" : ""}`}
      style={{ ...themeCssVars(theme), ["--db-row" as string]: `${rowHeight}px` }}
    >
      <header className={`db-header${banner ? " db-header--banner" : ""}`}>
        <div className="db-header-titles">
          <h1 className="db-title">{spec?.title || "Başlıksız rapor"}</h1>
          {spec?.subtitle ? <p className="db-subtitle">{spec.subtitle}</p> : null}
        </div>
        {filters.length ? (
          <div className="db-filters">
            {filters.map((f) => (
              <FilterControl
                key={f.id}
                filter={f}
                options={options[f.id] ?? []}
                value={filterState[f.id] ?? []}
                onChange={(vals) => setFilterState((s) => ({ ...s, [f.id]: vals }))}
              />
            ))}
            {activeFilters.length ? (
              <button type="button" className="db-filter-clear" onClick={() => setFilterState({})}>
                Temizle
              </button>
            ) : null}
          </div>
        ) : null}
      </header>

      <div className="db-grid">
        {visuals.map((v, i) => {
          const pos = v.position ?? { x: 0, y: i * 3, w: 12, h: 3 };
          const x = clamp(Math.round(pos.x ?? 0), 0, 11);
          const w = clamp(Math.round(pos.w ?? 12), 1, 12 - x);
          const h = clamp(Math.round(pos.h ?? 3), 1, 24);
          const y = Math.max(0, Math.round(pos.y ?? 0));
          const table = v.datasetId ? tables[v.datasetId] : undefined;
          const unaffected =
            v.type !== "text" && table && !table.error && activeFilters.length
              ? activeFilters.filter((f) => !table.columns.includes(f.field)).map((f) => f.label)
              : [];
          const note = unaffected.length ? `Bu görselin verisinde ${unaffected.join(", ")} alanı yok; filtre uygulanmadı.` : undefined;
          const key = `${v.id ?? i}`;
          return (
            <div
              key={key}
              className={`db-cell db-cell--${v.type}`}
              style={{
                ["--x" as string]: x + 1,
                ["--w" as string]: w,
                ["--y" as string]: y + 1,
                ["--h" as string]: h,
                ["--order" as string]: i,
              }}
            >
              <VisualBoundary title={v.title} resetKey={JSON.stringify(v) + (v.datasetId ? String(safeData.datasets[v.datasetId]?.rows?.length ?? "x") : "")}>
                {v.type !== "text" && !v.datasetId ? (
                  <ErrorCard title={v.title} message="datasetId tanımlı değil." />
                ) : (
                  <VisualSwitch
                    visual={v}
                    dataset={datasets.find((d) => d.id === v.datasetId)}
                    table={table}
                    tables={tables}
                    datasets={datasets}
                    theme={theme}
                    filterNote={note}
                  />
                )}
              </VisualBoundary>
            </div>
          );
        })}
      </div>
      {visuals.length === 0 ? <div className="db-empty-note db-empty-note--page">Bu raporda henüz görsel yok.</div> : null}
    </div>
  );
}

const clamp = (n: number, a: number, b: number) => Math.max(a, Math.min(b, Number.isFinite(n) ? n : a));

// ---------- filtre kontrolleri ----------

function FilterControl({ filter, options, value, onChange }: {
  filter: Filter; options: string[]; value: string[]; onChange: (v: string[]) => void;
}) {
  if (filter.type === "select") {
    return (
      <label className="db-filter">
        <span className="db-filter-label">{filter.label}</span>
        <span className="db-select">
          <select value={value[0] ?? ""} onChange={(e) => onChange(e.target.value ? [e.target.value] : [])}>
            <option value="">Tümü</option>
            {options.map((o) => (
              <option key={o} value={o}>{o}</option>
            ))}
          </select>
          <Chevron />
        </span>
      </label>
    );
  }
  return <MultiSelect filter={filter} options={options} value={value} onChange={onChange} />;
}

function Chevron() {
  return (
    <svg className="db-chevron" viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">
      <path d="M3 4.5 6 7.5l3-3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function MultiSelect({ filter, options, value, onChange }: {
  filter: Filter; options: string[]; value: string[]; onChange: (v: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  const set = new Set(value);
  const summary = value.length === 0 ? "Tümü" : value.length === 1 ? value[0] : `${value.length} seçili`;
  const visible = q ? options.filter((o) => o.toLocaleLowerCase("tr").includes(q.toLocaleLowerCase("tr"))) : options;
  const toggle = (o: string) => onChange(set.has(o) ? value.filter((v) => v !== o) : [...value, o]);
  return (
    <div className="db-filter" ref={ref}>
      <span className="db-filter-label">{filter.label}</span>
      <button
        type="button"
        className={`db-select db-select-btn${value.length ? " is-active" : ""}`}
        onClick={() => setOpen((s) => !s)}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className="db-select-text">{summary}</span>
        <Chevron />
      </button>
      {open ? (
        <div className="db-pop" role="listbox" aria-multiselectable="true">
          {options.length > 8 ? (
            <input className="db-pop-search" placeholder="Ara…" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
          ) : null}
          <div className="db-pop-list">
            {visible.map((o) => (
              <button type="button" key={o} className={`db-pop-item${set.has(o) ? " is-on" : ""}`} onClick={() => toggle(o)} role="option" aria-selected={set.has(o)}>
                <span className="db-check" aria-hidden="true">
                  {set.has(o) ? <svg viewBox="0 0 12 12" width="10" height="10"><path d="M2.5 6.2 5 8.5l4.5-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg> : null}
                </span>
                <span>{o}</span>
              </button>
            ))}
            {!visible.length ? <div className="db-pop-empty">Sonuç yok</div> : null}
          </div>
          <div className="db-pop-foot">
            <button type="button" onClick={() => onChange([])} disabled={!value.length}>Tümü</button>
            <button type="button" onClick={() => setOpen(false)}>Kapat</button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default DashboardRenderer;
