// Görsel bileşenleri: grafik, KPI, tablo, metin + kart kabuğu ve hata sınırı.
import { Component, useMemo, useState, type ReactNode } from "react";
import type { Dataset, Visual } from "../types";
import { EChart } from "./EChart";
import {
  buildChartOption, EmptyDataError, enc, fieldDef, fieldLabel, fmtFor, kpiValue, tooltipBase, usedFields, validateFields, VisualError,
  type ChartCtx,
} from "./charts";
import { groupBy, type Row } from "./data";
import { escapeHtml, formatCategory, formatValue, isPeriodLike, resolveFormat, toNumber } from "./format";
import { alpha, type DerivedTheme } from "./theme";

export interface TableData {
  all: Row[];
  rows: Row[];
  columns: string[];
  error?: string;
}

export interface VisualProps {
  visual: Visual;
  dataset?: Dataset;
  table?: TableData;
  tables: Record<string, TableData>;
  datasets: Dataset[];
  theme: DerivedTheme;
  filterNote?: string;
}

// ---------- hata sınırı ----------

export class VisualBoundary extends Component<{ title: string; resetKey: string; children: ReactNode }, { error: Error | null; key: string }> {
  state = { error: null as Error | null, key: this.props.resetKey };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  static getDerivedStateFromProps(props: { resetKey: string }, state: { key: string }) {
    if (props.resetKey !== state.key) return { error: null, key: props.resetKey };
    return null;
  }
  componentDidCatch(error: Error) {
    if (!(error instanceof VisualError)) console.error("[dashboard] görsel hatası:", error);
  }
  render() {
    if (this.state.error) return <ErrorCard title={this.props.title} message={this.state.error.message} />;
    return this.props.children;
  }
}

export function ErrorCard({ title, message }: { title?: string; message: string }) {
  return (
    <div className="db-card db-card--error">
      {title ? <div className="db-card-head"><div className="db-card-title">{title}</div></div> : null}
      <div className="db-error">
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
          <path d="M12 3 2.5 20h19L12 3Z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
          <path d="M12 10v4.5M12 17.2v.3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
        <div>
          <div className="db-error-title">Görsel çizilemedi</div>
          <div className="db-error-msg">{message}</div>
        </div>
      </div>
    </div>
  );
}

// ---------- kart kabuğu ----------

function CardHead({ visual, filterNote, tableToggle, onToggle, showTable }: {
  visual: Visual; filterNote?: string; tableToggle?: boolean; onToggle?: () => void; showTable?: boolean;
}) {
  if (!visual.title && !visual.subtitle && !tableToggle) return null;
  return (
    <div className="db-card-head">
      <div className="db-card-titles">
        {visual.title ? <div className="db-card-title">{visual.title}</div> : null}
        {visual.subtitle ? <div className="db-card-sub">{visual.subtitle}</div> : null}
      </div>
      <div className="db-card-actions">
        {filterNote ? <span className="db-badge" title={filterNote}>Filtre dışı</span> : null}
        {tableToggle ? (
          <button
            type="button"
            className={`db-icon-btn${showTable ? " is-on" : ""}`}
            onClick={onToggle}
            title={showTable ? "Grafiğe dön" : "Tablo görünümü"}
            aria-label={showTable ? "Grafiğe dön" : "Tablo görünümü"}
          >
            {showTable ? (
              <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M2 13.5h12M4 11V7M8 11V3.5M12 11V6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none" /></svg>
            ) : (
              <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="2" y="3" width="12" height="10" rx="2" stroke="currentColor" strokeWidth="1.3" fill="none" /><path d="M2 6.5h12M2 9.5h12M6.5 6.5V13" stroke="currentColor" strokeWidth="1.3" /></svg>
            )}
          </button>
        ) : null}
      </div>
    </div>
  );
}

// ---------- grafik ----------

export function ChartVisual(p: VisualProps) {
  const { visual, table } = p;
  const [showTable, setShowTable] = useState(false);
  const [size, setSize] = useState({ w: 600, h: 300 });
  const t = table!;
  // genişliğe duyarlı seçenekler (ör. donut lejantı yanda/altta) için boyut kovası;
  // pie/donut/bar/heatmap yerleşimi piksel hesabıyla yapılır → tam boyut
  const fine = ["pie", "donut", "gauge", "bar", "heatmap"].includes(visual.type);
  const wBucket = fine ? Math.round(size.w) : size.w >= 400 ? 1 : 0;
  const hBucket = fine ? Math.round(size.h) : size.h >= 220 ? 1 : 0;
  const built = useMemo(() => {
    const ctx: ChartCtx = {
      visual, dataset: p.dataset, rows: t.rows, all: t.all, columns: t.columns, theme: p.theme,
      width: size.w, height: size.h,
    };
    try {
      return { option: buildChartOption(ctx), empty: null as string | null };
    } catch (e) {
      if (e instanceof EmptyDataError) return { option: null, empty: e.message };
      throw e;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visual, p.dataset, t, p.theme, wBucket, hBucket]);

  return (
    <div className="db-card">
      <CardHead visual={visual} filterNote={p.filterNote} tableToggle showTable={showTable} onToggle={() => setShowTable((s) => !s)} />
      <div className="db-card-body">
        {showTable ? (
          <DataTable rows={t.rows} columns={usedFields(visual, t.columns)} dataset={p.dataset} visual={visual} theme={p.theme} />
        ) : built.option ? (
          <EChart option={built.option} onSize={(w, h) => setSize((s) => (Math.abs(s.w - w) > 1 || Math.abs(s.h - h) > 1 ? { w, h } : s))} />
        ) : (
          <div className="db-empty-note">{built.empty}</div>
        )}
      </div>
    </div>
  );
}

// ---------- KPI ----------

function Arrow({ up }: { up: boolean }) {
  return (
    <svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true">
      {up ? <path d="M6 2.2 10 7H7.2v3H4.8V7H2L6 2.2Z" fill="currentColor" /> : <path d="M6 9.8 2 5h2.8V2h2.4v3H10L6 9.8Z" fill="currentColor" />}
    </svg>
  );
}

export function KpiVisual(p: VisualProps) {
  const { visual, theme } = p;
  const table = p.table!;
  const o = visual.options ?? {};
  const e = enc(visual);
  const ctx = { visual, dataset: p.dataset, rows: table.rows };
  const value = kpiValue(ctx, e.value!);
  const fmt = fmtFor(ctx, e.value);
  const delta = o.deltaField ? kpiValue(ctx, o.deltaField, "first") : null;
  const deltaDef = fieldDef(p.dataset, o.deltaField);
  const deltaFmt = resolveFormat(deltaDef, { decimals: 1 }, true);
  const deltaIsPct = !deltaDef?.format || deltaDef.format === "percent";
  const deltaText =
    delta === null ? null : `${delta > 0 ? "+" : delta < 0 ? "−" : ""}${formatValue(Math.abs(delta), deltaIsPct ? { format: "percent", decimals: 1 } : deltaFmt)}`;
  const target = o.target;
  const ratio = target && value !== null ? value / target : null;

  // sparkline
  const spark = useMemo(() => {
    if (!o.sparklineDatasetId || !o.sparklineField) return null;
    const st = p.tables[o.sparklineDatasetId];
    if (!st || st.error || !st.columns.includes(o.sparklineField)) return null;
    const sds = p.datasets.find((d) => d.id === o.sparklineDatasetId);
    const xField =
      sds?.fields?.find((f) => f.type === "date" && st.columns.includes(f.name))?.name ??
      st.columns.find((c) => st.rows.some((r) => isPeriodLike(r[c]))) ??
      sds?.fields?.find((f) => f.type === "string" && st.columns.includes(f.name))?.name ??
      st.columns.find((c) => c !== o.sparklineField && st.rows.some((r) => typeof r[c] === "string"));
    const pts = xField
      ? groupBy(st.rows, xField, [o.sparklineField]).map((g) => ({ x: g.raw, y: g.values[o.sparklineField!] }))
      : st.rows.map((r, i) => ({ x: i, y: toNumber(r[o.sparklineField!]) }));
    if (pts.length < 2) return null;
    const sfmt = resolveFormat(fieldDef(sds, o.sparklineField), { format: fmt.format === "compact" ? "compact" : undefined, currency: o.currency });
    return { pts, sfmt, label: fieldLabel(sds, o.sparklineField) };
  }, [o.sparklineDatasetId, o.sparklineField, p.tables, p.datasets, fmt.format, o.currency]);

  const sparkOption = useMemo(() => {
    if (!spark) return null;
    const lineColor = o.color ?? theme.accent;
    const n = spark.pts.length;
    return {
      animationDuration: 400,
      grid: { left: 2, right: 6, top: 6, bottom: 2 },
      xAxis: { type: "category", show: false, boundaryGap: false, data: spark.pts.map((q) => String(q.x)) },
      yAxis: { type: "value", show: false, scale: true },
      tooltip: {
        ...tooltipBase(theme),
        trigger: "axis",
        axisPointer: { type: "line", lineStyle: { color: theme.axis } },
        formatter: (ps: { dataIndex: number; value: number }[]) => {
          const q = ps[0];
          if (!q) return "";
          return `<div class="db-tt-h">${escapeHtml(formatCategory(spark.pts[q.dataIndex].x, false))}</div>` +
            `<div class="db-tt-r"><span class="db-tt-k is-line" style="background:${escapeHtml(lineColor)}"></span><span class="db-tt-v">${escapeHtml(formatValue(q.value, spark.sfmt))}</span><span class="db-tt-n">${escapeHtml(spark.label)}</span></div>`;
        },
      },
      series: [
        {
          type: "line",
          data: spark.pts.map((q, i) =>
            i === n - 1
              ? { value: q.y, symbol: "circle", symbolSize: 7, itemStyle: { color: lineColor, borderColor: theme.surface, borderWidth: 2 } }
              : q.y,
          ),
          smooth: 0.3,
          showSymbol: true,
          symbol: "none",
          lineStyle: { width: 1.75, color: alpha(lineColor, 0.85) },
          areaStyle: {
            color: {
              type: "linear", x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [{ offset: 0, color: alpha(lineColor, 0.16) }, { offset: 1, color: alpha(lineColor, 0) }],
            },
          },
          emphasis: { disabled: true },
        },
      ],
    };
  }, [spark, theme, o.color]);

  const up = (delta ?? 0) >= 0;
  return (
    <div className="db-card db-kpi">
      <div className="db-kpi-top">
        <div className="db-kpi-label">{visual.title}</div>
        {p.filterNote ? <span className="db-badge" title={p.filterNote}>Filtre dışı</span> : null}
      </div>
      <div className="db-kpi-value" title={value === null ? undefined : formatValue(value, { ...fmt, format: fmt.format === "compact" ? (fmt.currency ? "currency" : "number") : fmt.format })}>
        {value === null ? "–" : formatValue(value, fmt)}
      </div>
      {deltaText || visual.subtitle ? (
        <div className="db-kpi-delta-row">
          {deltaText ? (
            <span className={`db-delta ${delta === 0 ? "is-flat" : up ? "is-up" : "is-down"}`}>
              {delta !== 0 ? <Arrow up={up} /> : null}
              {deltaText}
            </span>
          ) : null}
          <span className="db-kpi-delta-label">{o.deltaLabel ?? visual.subtitle ?? ""}</span>
        </div>
      ) : null}
      {ratio !== null && target ? (
        <div className="db-kpi-target">
          <div className="db-meter"><div className="db-meter-fill" style={{ width: `${Math.max(0, Math.min(1, ratio)) * 100}%` }} /></div>
          <div className="db-kpi-target-text">
            Hedef {formatValue(target, fmt)} · <b>{formatValue(ratio, { format: "percent", decimals: 0 })}</b>
          </div>
        </div>
      ) : null}
      {sparkOption ? (
        <div className="db-kpi-spark">
          <EChart option={sparkOption} />
        </div>
      ) : (
        <div className="db-kpi-fill" />
      )}
    </div>
  );
}

// ---------- tablo ----------

function DataTable({ rows, columns, dataset, visual, theme }: {
  rows: Row[]; columns: string[]; dataset?: Dataset; visual: Visual; theme: DerivedTheme;
}) {
  const o = visual.options ?? {};
  const isTableVisual = visual.type === "table";
  const numeric = useMemo(() => {
    const m: Record<string, boolean> = {};
    for (const c of columns) {
      const def = fieldDef(dataset, c);
      m[c] = def ? def.type === "number" : rows.length > 0 && rows.every((r) => r[c] === null || typeof r[c] === "number");
    }
    return m;
  }, [columns, dataset, rows]);
  const defaultSortField = isTableVisual && o.sort ? (visual.encoding.value ?? columns.find((c) => numeric[c])) : undefined;
  const [sort, setSort] = useState<{ field: string; dir: "asc" | "desc" } | null>(null);
  const active = sort ?? (defaultSortField ? { field: defaultSortField, dir: o.sort! } : null);

  const shown = useMemo(() => {
    let r = rows;
    if (active) {
      const { field, dir } = active;
      const num = numeric[field];
      r = [...r].sort((a, b) => {
        const av = a[field], bv = b[field];
        if (av === null || av === undefined) return 1;
        if (bv === null || bv === undefined) return -1;
        const c = num ? (toNumber(av) ?? 0) - (toNumber(bv) ?? 0) : String(av).localeCompare(String(bv), "tr");
        return dir === "asc" ? c : -c;
      });
    }
    if (isTableVisual && o.limit) r = r.slice(0, o.limit);
    return r;
  }, [rows, active, numeric, isTableVisual, o.limit]);

  const fmtOf = (c: string) => {
    const def = fieldDef(dataset, c);
    if (isTableVisual) return resolveFormat(def, o, true);
    return resolveFormat(def, { currency: o.currency, decimals: o.decimals }, true);
  };

  if (!rows.length) return <div className="db-empty-note">Seçili filtreler için veri yok.</div>;

  return (
    <div className="db-table-wrap" style={{ ["--db-zebra" as string]: theme.surface2 }}>
      <table className="db-table">
        <thead>
          <tr>
            {columns.map((c) => {
              const on = active?.field === c;
              return (
                <th
                  key={c}
                  className={numeric[c] ? "is-num" : undefined}
                  onClick={() => setSort({ field: c, dir: on && active!.dir === "desc" ? "asc" : "desc" })}
                  aria-sort={on ? (active!.dir === "asc" ? "ascending" : "descending") : "none"}
                >
                  <span className="db-th">
                    {fieldLabel(dataset, c)}
                    <span className={`db-sort${on ? " is-on" : ""}`}>{on ? (active!.dir === "asc" ? "↑" : "↓") : "↕"}</span>
                  </span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {shown.map((r, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c} className={numeric[c] ? "is-num" : undefined}>
                  {numeric[c] ? formatValue(r[c], fmtOf(c)) : r[c] === null || r[c] === undefined ? "–" : formatCategory(r[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TableVisual(p: VisualProps) {
  const { visual } = p;
  const table = p.table!;
  const columns = visual.encoding?.columns?.length ? visual.encoding.columns : table.columns;
  return (
    <div className="db-card db-card--table">
      <CardHead visual={visual} filterNote={p.filterNote} />
      <div className="db-card-body db-card-body--flush">
        <DataTable rows={table.rows} columns={columns} dataset={p.dataset} visual={visual} theme={p.theme} />
      </div>
    </div>
  );
}

// ---------- metin ----------

export function TextVisual({ visual }: VisualProps) {
  const text = visual.options?.text ?? "";
  return (
    <div className="db-card db-text">
      {visual.title ? <div className="db-card-title db-text-title">{visual.title}</div> : null}
      {visual.subtitle ? <div className="db-card-sub">{visual.subtitle}</div> : null}
      {text ? <div className="db-text-body">{text}</div> : null}
    </div>
  );
}

/** Veri kümesi / alan kontrolü — hata varsa okunur mesaj (fırlatmadan). */
function precheck(p: VisualProps): string | null {
  const { visual, table } = p;
  if (visual.type === "text") return null;
  if (!table) return `Veri kümesi bulunamadı: "${visual.datasetId ?? "—"}"`;
  if (table.error) return `Veri kümesi hatası (${visual.datasetId}): ${table.error}`;
  try {
    validateFields(visual, table.columns);
  } catch (e) {
    if (e instanceof VisualError) return e.message;
    throw e;
  }
  return null;
}

export function VisualSwitch(p: VisualProps) {
  const problem = precheck(p);
  if (problem) return <ErrorCard title={p.visual.title} message={problem} />;
  switch (p.visual.type) {
    case "kpi":
      return <KpiVisual {...p} />;
    case "table":
      return <TableVisual {...p} />;
    case "text":
      return <TextVisual {...p} />;
    default:
      return <ChartVisual {...p} />;
  }
}
