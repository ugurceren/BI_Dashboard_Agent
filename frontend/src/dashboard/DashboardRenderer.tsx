// Saf bileşen: <DashboardRenderer spec data /> — API çağrısı yok; bağımsız görüntüleyicide de kullanılır.
// İki filtre modu:
//  * model (uygulama): filtre durumu dışarıda tutulur, backend seçimleri ilişkiler üzerinden SQL'e uygular
//    (Power BI dilimleyicisi + görselden tıklayarak çapraz filtre). Renderer yalnızca gösterir ve bildirir.
//  * istemci (bağımsız HTML): backend yok; filtre, model kolonuna bağlı dataset alanlarında tarayıcıda uygulanır.
import { useEffect, useMemo, useRef, useState } from "react";
import type { CellValue, CrossSelection, DashboardData, DataDateInfo, Filter, FilterInfo, ReportSpec } from "../types";
import { buildTables, filterOptions, type FilterState } from "./data";
import { formatCategory } from "./format";
import { fieldLabel } from "./charts";
import { deriveTheme, normalizeTheme, themeCssVars } from "./theme";
import { ErrorCard, VisualBoundary, VisualSwitch } from "./visuals";
import "./dashboard.css";
import { registerFonts } from "./fonts";

registerFonts();

export interface ModelFiltering {
  filters: FilterInfo[];
  selections: Record<string, CellValue[]>;          // filtre id → seçili ham değerler
  onSelections: (next: Record<string, CellValue[]>) => void;
  cross: CrossSelection | null;
  onCross: (next: CrossSelection | null) => void;
  applied: Record<string, string[]>;                 // dataset → uygulanan model filtre anahtarları
  bindings: Record<string, Record<string, string>>;  // dataset → alan → model kolonu
  /** günlük anlık görüntü okuyan raporlarda 'itibarıyla' tarihi (seçim: selections[dataDate.key]) */
  dataDate?: DataDateInfo;
}

export interface DashboardRendererProps {
  spec: ReportSpec;
  data: DashboardData;
  /** yeniden yüklenirken önceki çizimi soluk tut */
  loading?: boolean;
  /** verilirse filtreler model üzerinden (backend) çalışır */
  model?: ModelFiltering;
}

export function DashboardRenderer({ spec, data, loading, model }: DashboardRendererProps) {
  const theme = useMemo(() => deriveTheme(normalizeTheme(spec?.theme)), [spec?.theme]);
  const [localState, setLocalState] = useState<FilterState>({});
  const filters = useMemo(() => (Array.isArray(spec?.filters) ? spec.filters : []), [spec?.filters]);
  const safeData = useMemo<DashboardData>(() => (data && data.datasets ? data : { datasets: {} }), [data]);

  // --- seçenekler: model modunda backend'den (ham değer), istemci modunda veriden
  const info = useMemo(() => new Map((model?.filters ?? []).map((f) => [f.id, f])), [model?.filters]);
  const options = useMemo(() => Object.fromEntries(filters.map((f) => [
    f.id, model ? (info.get(f.id)?.options ?? []).map((v) => String(v)) : filterOptions(f, safeData),
  ])), [filters, safeData, model, info]);
  const filterState: FilterState = useMemo(() => (model
    ? Object.fromEntries(Object.entries(model.selections).map(([k, v]) => [k, v.map((x) => String(x))]))
    : localState), [model, localState]);
  const setFilter = (id: string, vals: string[]) => {
    if (model) {
      const raw = new Map((info.get(id)?.options ?? []).map((v) => [String(v), v]));
      model.onSelections({ ...model.selections, [id]: vals.map((v) => (raw.has(v) ? raw.get(v)! : v)) });
    } else setLocalState((st) => ({ ...st, [id]: vals }));
  };
  const clearAll = () => {
    if (model) { model.onSelections({}); model.onCross(null); } else setLocalState({});
  };

  // istemci modu: artık olmayan değerleri temizle
  useEffect(() => {
    if (model) return;
    setLocalState((st) => {
      let changed = false;
      const next: FilterState = {};
      for (const [id, vals] of Object.entries(st)) {
        const opts = options[id];
        if (!opts) { changed = true; continue; }
        const keep = vals.filter((v) => opts.includes(v));
        if (keep.length !== vals.length) changed = true;
        next[id] = keep;
      }
      return changed ? next : st;
    });
  }, [options, model]);

  const tables = useMemo(() => buildTables(spec, safeData, model ? {} : localState), [spec, safeData, localState, model]);
  const activeFilters = filters.filter((f) => (filterState[f.id]?.length ?? 0) > 0);

  // --- sayfalar (Power BI gibi): birden çok sayfa varsa sekmeler; filtreler tüm sayfalarda geçerli
  const pages = useMemo(() => (Array.isArray(spec?.pages) ? spec.pages : []), [spec?.pages]);
  const [pageId, setPageId] = useState<string | undefined>(pages[0]?.id);
  const prevPages = useRef<string[]>(pages.map((p) => p.id));
  useEffect(() => {
    const ids = pages.map((p) => p.id);
    const added = ids.find((id) => !prevPages.current.includes(id));
    prevPages.current = ids;
    if (added && ids.length > 1) setPageId(added);                       // yeni eklenen sayfaya geç
    else setPageId((cur) => (cur && ids.includes(cur) ? cur : ids[0]));
  }, [pages]);
  const activePage = pages.length ? (pageId && pages.some((p) => p.id === pageId) ? pageId : pages[0].id) : undefined;

  const visuals = useMemo(() => {
    const vs = Array.isArray(spec?.visuals) ? spec.visuals : [];
    const ids = new Set(pages.map((p) => p.id));
    const onPage = activePage ? vs.filter((v) => (v.page && ids.has(v.page) ? v.page : pages[0].id) === activePage) : vs;
    return [...onPage].sort((a, b) => (a.position?.y ?? 0) - (b.position?.y ?? 0) || (a.position?.x ?? 0) - (b.position?.x ?? 0));
  }, [spec?.visuals, pages, activePage]);

  const rowHeight = Math.max(40, Number(spec?.layout?.rowHeight) || 90);
  const datasets = Array.isArray(spec?.datasets) ? spec.datasets : [];
  const banner = theme.headerStyle === "banner";
  const cross = model?.cross ?? null;

  /** hangi aktif filtreler bu görselin verisine uygulanamadı */
  const unaffectedFor = (visualId: string, datasetId: string): string[] => {
    const table = tables[datasetId];
    if (!table || table.error) return [];
    if (model) {
      const applied = new Set(model.applied[datasetId] ?? []);
      const miss = activeFilters.filter((f) => { const k = info.get(f.id)?.key; return k && !applied.has(k); }).map((f) => f.label);
      if (cross && cross.visualId !== visualId && !applied.has(cross.key)) miss.push(cross.label);
      return miss;
    }
    return activeFilters.filter((f) => !table.filterFields?.[filters.indexOf(f)]).map((f) => f.label);
  };

  const onSelectFor = (visualId: string, datasetId: string | undefined) => {
    if (!model || !datasetId) return undefined;
    const b = model.bindings[datasetId];
    if (!b || !Object.keys(b).length) return undefined;
    return (field: string, value: CellValue) => {
      const key = b[field];
      if (!key) return;
      if (cross && cross.visualId === visualId && cross.key === key && String(cross.value) === String(value)) {
        model.onCross(null);  // aynı öğeye ikinci tık: seçimi kaldır
        return;
      }
      const ds = datasets.find((d) => d.id === datasetId);
      model.onCross({ key, value, visualId, datasetId, label: `${fieldLabel(ds, field)}: ${formatCategory(value, false)}` });
    };
  };

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
        {filters.length || cross || model?.dataDate ? (
          <div className="db-filters">
            {model?.dataDate ? (
              <DataDateControl info={model.dataDate} value={String(model.selections[model.dataDate.key]?.[0] ?? "")}
                onChange={(v) => {
                  const next = { ...model.selections };
                  if (v) next[model.dataDate!.key] = [v]; else delete next[model.dataDate!.key];
                  model.onSelections(next);
                }} />
            ) : null}
            {filters.map((f) => (
              <FilterControl
                key={f.id}
                filter={f}
                options={options[f.id] ?? []}
                value={filterState[f.id] ?? []}
                onChange={(vals) => setFilter(f.id, vals)}
              />
            ))}
            {cross ? (
              <button type="button" className="db-cross-chip" onClick={() => model?.onCross(null)} title="Görselden yapılan seçimi kaldır">
                <span>Seçim: {cross.label}</span> <span aria-hidden="true">✕</span>
              </button>
            ) : null}
            {activeFilters.length || cross ? (
              <button type="button" className="db-filter-clear" onClick={clearAll}>
                Temizle
              </button>
            ) : null}
          </div>
        ) : null}
        {pages.length > 1 ? (
          <nav className="db-pages" role="tablist" aria-label="Rapor sayfaları">
            {pages.map((p) => (
              <button key={p.id} type="button" role="tab" aria-selected={p.id === activePage}
                className={`db-page-tab${p.id === activePage ? " is-on" : ""}`} onClick={() => setPageId(p.id)}>
                {p.title}
              </button>
            ))}
          </nav>
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
          const unaffected = v.type !== "text" && v.datasetId ? unaffectedFor(v.id, v.datasetId) : [];
          const note = unaffected.length && v.options?.ignoreFilters
            ? `Bu görsel tasarım gereği filtrelerden bağımsız (${unaffected.join(", ")} uygulanmadı).`
            : unaffected.length
            ? (model ? `Bu görselin verisi modelde ${unaffected.join(", ")} ile ilişkili değil; filtre uygulanmadı.`
                     : `Bu görselin verisinde ${unaffected.join(", ")} alanı yok; filtre uygulanmadı.`)
            : undefined;
          const isSource = cross?.visualId === v.id;
          const key = `${v.id ?? i}`;
          return (
            <div
              key={key}
              className={`db-cell db-cell--${v.type}`}
              data-visual-id={v.id}
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
                    onSelect={onSelectFor(v.id, v.datasetId)}
                    selectionNote={isSource ? cross!.label : undefined}
                    selected={isSource ? cross!.value : undefined}
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

const trDay = (iso: string | null | undefined) => (iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}` : "—");

/** Veri tarihi (itibarıyla): boşsa son gün; seçilen gün yoksa öncesindeki son günün verisi gösterilir. */
function DataDateControl({ info, value, onChange }: { info: DataDateInfo; value: string; onChange: (v: string) => void }) {
  return (
    <label className="db-filter db-filter--date" title={`Günlük anlık görüntü (${info.column}): ${info.tables.join(", ")}\n`
      + `Veri aralığı: ${trDay(info.min)} – ${trDay(info.max)}. Seçilen gün itibarıyla değerler gösterilir; o gün veri yoksa öncesindeki son gün kullanılır.`}>
      <span className="db-filter-label">Veri tarihi</span>
      <span className="db-date-row">
        <input type="date" className="db-date-input" value={value} min={info.min ?? undefined} max={info.max ?? undefined}
          onChange={(e) => onChange(e.target.value)} aria-label="Veri tarihi" />
        {value ? (
          <button type="button" className="db-date-reset" onClick={() => onChange("")} title="Son güne dön">Son gün</button>
        ) : <span className="db-date-hint">son gün: {trDay(info.max)}</span>}
      </span>
    </label>
  );
}
