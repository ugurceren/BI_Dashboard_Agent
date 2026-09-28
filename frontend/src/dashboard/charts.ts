/* eslint-disable @typescript-eslint/no-explicit-any */
// Visual → ECharts seçeneği. Tüm renkler/yazılar spec temasından türetilir.
import type { CellValue, Dataset, DatasetField, Visual } from "../types";
import {
  escapeHtml, formatAxis, formatCategory, formatValue, resolveFormat, toNumber, type FormatSpec,
} from "./format";
import { groupBy, keyOf, pivot, seriesOrder, type Row } from "./data";
import { alpha, inkOn, mix, sequentialRamp, type DerivedTheme } from "./theme";
import type { EOption } from "./EChart";

export class VisualError extends Error {}
/** Filtre sonrası boş veri: hata değil, kart içinde bilgi notu. */
export class EmptyDataError extends VisualError {}

export interface ChartCtx {
  visual: Visual;
  dataset?: Dataset;
  rows: Row[];      // filtrelenmiş
  all: Row[];       // filtresiz (renk/sıra kararlılığı için)
  columns: string[];
  theme: DerivedTheme;
  width: number;
  height: number;
}

// ---------- alan yardımcıları ----------

export function fieldDef(ds: Dataset | undefined, name: string | undefined): DatasetField | undefined {
  if (!name) return undefined;
  return ds?.fields?.find((f) => f.name === name);
}
export function fieldLabel(ds: Dataset | undefined, name: string | undefined): string {
  if (!name) return "";
  return fieldDef(ds, name)?.label || name;
}
export function fmtFor(ctx: Pick<ChartCtx, "dataset" | "visual">, name: string | undefined): FormatSpec {
  return resolveFormat(fieldDef(ctx.dataset, name), ctx.visual.options);
}

/** Esnek kodlama: LLM x/y yerine category/value (veya tersi) verse de çalışsın. */
export function enc(v: Visual) {
  const e = v.encoding ?? {};
  const y = e.y && e.y.length ? e.y : e.value ? [e.value] : [];
  return {
    x: e.x ?? e.category,
    y,
    series: e.series,
    category: e.category ?? e.x,
    value: e.value ?? e.y?.[0],
    columns: e.columns,
  };
}

/** Görselin ihtiyaç duyduğu alanları kontrol eder; eksikse okunur Türkçe hata fırlatır. */
export function validateFields(v: Visual, columns: string[]) {
  const e = enc(v);
  const need: [string, string | undefined][] = [];
  switch (v.type) {
    case "kpi":
    case "gauge":
      need.push(["value", e.value]);
      break;
    case "line": case "area": case "bar": case "combo": case "scatter":
      need.push(["x", e.x], ["y", e.y[0]]);
      e.y.slice(1).forEach((f) => need.push(["y", f]));
      if (e.series) need.push(["series", e.series]);
      break;
    case "pie": case "donut": case "funnel": case "treemap":
      need.push(["category", e.category], ["value", e.value]);
      break;
    case "heatmap":
      need.push(["x", v.encoding.x], ["category", v.encoding.category], ["value", e.value]);
      break;
    case "table":
      (e.columns ?? []).forEach((c) => need.push(["columns", c]));
      break;
    case "text":
      return;
    default:
      throw new VisualError(`Bilinmeyen görsel tipi: "${String(v.type)}"`);
  }
  for (const [role, f] of need) {
    if (!f) throw new VisualError(`"${v.type}" görseli için encoding.${role} alanı gerekli.`);
    if (!columns.includes(f))
      throw new VisualError(`Alan bulunamadı: "${f}" (encoding.${role}). Mevcut kolonlar: ${columns.join(", ") || "—"}`);
  }
  if (v.options?.deltaField && !columns.includes(v.options.deltaField))
    throw new VisualError(`Alan bulunamadı: "${v.options.deltaField}" (options.deltaField).`);
}

// ---------- ortak parçalar ----------

function palette(ctx: ChartCtx): string[] {
  const p = ctx.theme.palette?.length ? ctx.theme.palette : ["#2563eb"];
  return p;
}

function baseOption(ctx: ChartCtx): any {
  const t = ctx.theme;
  return {
    color: palette(ctx),
    backgroundColor: "transparent",
    textStyle: { fontFamily: t.fontFamily, color: t.mutedText, fontSize: 11 },
    animationDuration: 450,
    animationDurationUpdate: 300,
    animationEasing: "cubicOut",
    tooltip: tooltipBase(t),
  };
}

export function tooltipBase(t: DerivedTheme): any {
  return {
    confine: true,
    backgroundColor: t.surface,
    borderColor: t.border,
    borderWidth: 1,
    padding: [8, 11],
    textStyle: { color: t.text, fontFamily: t.fontFamily, fontSize: 12 },
    extraCssText: `border-radius:8px;box-shadow:${t.isDark ? "0 8px 24px rgba(0,0,0,.5)" : "0 8px 24px rgba(15,23,42,.12)"};`,
    transitionDuration: 0.15,
  };
}

const ttHead = (s: string) => `<div class="db-tt-h">${escapeHtml(s)}</div>`;
const ttRow = (color: string, value: string, name: string, line = false) =>
  `<div class="db-tt-r"><span class="db-tt-k${line ? " is-line" : ""}" style="background:${escapeHtml(color)}"></span>` +
  `<span class="db-tt-v">${escapeHtml(value)}</span><span class="db-tt-n">${escapeHtml(name)}</span></div>`;

function legend(ctx: ChartCtx, kind: "rect" | "line" | "mixed", extra: any = {}): any {
  const t = ctx.theme;
  return {
    type: "scroll",
    top: 0,
    left: 0,
    itemWidth: kind === "line" ? 14 : 10,
    itemHeight: kind === "line" ? 3 : 10,
    itemGap: 14,
    icon: "roundRect",
    textStyle: { color: t.mutedText, fontSize: 11.5, fontFamily: t.fontFamily },
    pageIconColor: t.mutedText,
    pageIconInactiveColor: t.border,
    pageTextStyle: { color: t.mutedText },
    ...extra,
  };
}

function showLegend(ctx: ChartCtx, seriesCount: number): boolean {
  if (ctx.visual.options?.showLegend === false) return false;
  return seriesCount >= 2;
}

function categoryAxis(ctx: ChartCtx, data: CellValue[], extra: any = {}): any {
  const t = ctx.theme;
  return {
    type: "category",
    data: data.map((d) => keyOf(d)),
    axisLine: { show: true, lineStyle: { color: t.axis, width: 1 } },
    axisTick: { show: false },
    axisLabel: {
      color: t.mutedText,
      fontSize: 11,
      hideOverlap: true,
      margin: 10,
      formatter: (v: string) => formatCategory(v === "∅" ? null : v),
    },
    splitLine: { show: false },
    ...extra,
  };
}

function valueAxis(ctx: ChartCtx, fmt: FormatSpec, extra: any = {}): any {
  const t = ctx.theme;
  return {
    type: "value",
    splitNumber: 4,
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: t.mutedText, fontSize: 11, margin: 10, formatter: (v: number) => formatAxis(v, fmt) },
    splitLine: { show: true, lineStyle: { color: t.grid, width: 1, type: "solid" } },
    ...extra,
  };
}

function grid(_ctx: ChartCtx, hasLegend: boolean, extra: any = {}): any {
  return { left: 4, right: 12, top: hasLegend ? 40 : 12, bottom: 4, ...extra };
}

function colorForIndex(ctx: ChartCtx, i: number): string {
  const p = palette(ctx);
  if (i === 0 && ctx.visual.options?.color) return ctx.visual.options.color;
  return p[i % p.length];
}

/** Sıralama + limit (değer alanına göre). */
function sortLimit<T>(items: T[], value: (t: T) => number, sort?: "asc" | "desc", limit?: number): T[] {
  let out = items;
  if (sort) out = [...out].sort((a, b) => (sort === "asc" ? value(a) - value(b) : value(b) - value(a)));
  if (limit && limit > 0) out = out.slice(0, limit);
  return out;
}

// ---------- line / area / bar ----------

function cartesian(ctx: ChartCtx): EOption {
  const v = ctx.visual;
  const o = v.options ?? {};
  const e = enc(v);
  const t = ctx.theme;
  const x = e.x!;
  const order = e.series ? seriesOrder(ctx.all, e.series, e.y[0]) : undefined;
  const pv = pivot(ctx.rows, x, e.y, e.series, order);
  if (!pv.categories.length) throw new EmptyDataError("Seçili filtreler için veri yok.");

  // sıralama/limit kategoriler üzerinden (toplam değere göre)
  let idx = pv.categories.map((_, i) => i);
  if (o.sort || o.limit) {
    const tot = (i: number) => pv.series.reduce((s, se) => s + (se.data[i] ?? 0), 0);
    idx = sortLimit(idx, tot, o.sort, o.limit);
  }
  const categories = idx.map((i) => pv.categories[i]);
  const series = pv.series.map((s) => ({ ...s, data: idx.map((i) => s.data[i]) }));

  const isBar = v.type === "bar";
  const isArea = v.type === "area";
  const horizontal = isBar && !!o.horizontal;
  const stacked = !!o.stacked;
  const valueFmt = fmtFor(ctx, e.y[0]);
  const nameOf = (s: { name: string; field: string }) => (e.series ? formatCategory(s.name === "∅" ? null : s.name) : fieldLabel(ctx.dataset, s.field));
  const colorIdx = (s: { name: string }, i: number) => (order ? Math.max(0, order.indexOf(s.name)) : i);
  const legendOn = showLegend(ctx, series.length);
  const single = series.length === 1;

  const out: any = {
    ...baseOption(ctx),
    grid: grid(ctx, legendOn),
    legend: legendOn ? legend(ctx, isBar ? "rect" : isArea ? "rect" : "line") : { show: false },
    tooltip: {
      ...tooltipBase(t),
      trigger: "axis",
      axisPointer: isBar
        ? { type: "shadow", shadowStyle: { color: alpha(t.text, t.isDark ? 0.06 : 0.035) } }
        : { type: "line", lineStyle: { color: t.axis, width: 1 } },
      formatter: (ps: any[]) => {
        if (!ps?.length) return "";
        const head = formatCategory(categories[ps[0].dataIndex], false);
        const rows = [...ps]
          .filter((p) => p.value !== null && p.value !== undefined && p.value !== "-")
          .map((p) => ttRow(p.color?.colorStops ? p.color.colorStops[0].color : p.color, formatValue(p.value, fmtFor(ctx, series[p.seriesIndex]?.field)), p.seriesName, !isBar));
        if (stacked && ps.length > 1) {
          const total = ps.reduce((s, p) => s + (toNumber(p.value) ?? 0), 0);
          rows.push(`<div class="db-tt-total"><span>Toplam</span><b>${escapeHtml(formatValue(total, valueFmt))}</b></div>`);
        }
        return ttHead(head) + rows.join("");
      },
    },
  };

  // Dar yatay çubuk: kategori adı çubuğun üstünde (etiket sütunu çubukları ezmesin)
  const labelsAbove = horizontal && ctx.width < 560;
  const plotH = Math.max(80, ctx.height - (legendOn ? 40 : 12) - 8);
  const band = plotH / Math.max(1, categories.length);
  const aboveBarW = Math.round(Math.max(6, Math.min(14, band * 0.3)));
  const catAxis = categoryAxis(ctx, categories, horizontal
    ? {
        inverse: true,
        axisLine: { show: false },
        axisLabel: labelsAbove
          ? { ...categoryAxis(ctx, []).axisLabel, inside: true, align: "left", verticalAlign: "bottom", margin: 0, padding: [0, 0, aboveBarW / 2 + 4, 0], color: t.text, fontSize: 11.5, hideOverlap: false }
          : { ...categoryAxis(ctx, []).axisLabel, width: 120, overflow: "truncate", color: t.text, fontSize: 11.5 },
        z: 5,
      }
    : { boundaryGap: isBar });
  const valAxis = valueAxis(ctx, valueFmt, horizontal ? { splitNumber: 3 } : {});
  if (horizontal && o.showLabels) {
    // değerler çubuk ucunda: eksen yazıları ve ızgara gereksiz
    valAxis.axisLabel = { ...valAxis.axisLabel, show: false };
    valAxis.splitLine = { show: false };
    out.grid = grid(ctx, legendOn, { right: 72, left: labelsAbove ? 0 : 4, top: (legendOn ? 40 : 12) + (labelsAbove ? 8 : 0) });
  } else if (labelsAbove) {
    out.grid = grid(ctx, legendOn, { left: 0, top: (legendOn ? 40 : 12) + 8 });
  }
  out.xAxis = horizontal ? valAxis : catAxis;
  out.yAxis = horizontal ? catAxis : valAxis;

  // Dikey çubuk etiketleri: sığmıyorsa önce ₺ sembolünü at, yine sığmıyorsa gizle (tooltip + tablo görünümü değerleri taşır)
  let barLabelsOn = !!o.showLabels;
  let dropSymbol = false;
  if (isBar && !horizontal && barLabelsOn) {
    const bandW = Math.max(1, (ctx.width - 70) / Math.max(1, categories.length));
    const sample = categories.map((_, i) => {
      const v = stacked ? series.reduce((acc, se) => acc + (se.data[i] ?? 0), 0) : Math.max(...series.map((se) => se.data[i] ?? 0));
      return formatAxisValue(v, valueFmt);
    });
    const px = (x: string) => x.length * 6.1;
    const longest = Math.max(...sample.map(px));
    const longestBare = Math.max(...sample.map((x) => px(x.replace(/\s?₺$/, "").replace(/^₺/, ""))));
    const perBar = stacked || series.length === 1 ? bandW : bandW / series.length;
    if (longest > perBar - 2) {
      dropSymbol = true;
      if (longestBare > perBar - 2) barLabelsOn = false;
    }
  }
  const barWidthCap = 24;
  out.series = series.map((s, i) => {
    const color = colorForIndex(ctx, colorIdx(s, i));
    const name = nameOf(s);
    const last = i === series.length - 1;
    if (isBar) {
      const radius = stacked && !last ? 0 : 4;
      return {
        type: "bar",
        name,
        data: s.data,
        stack: stacked ? "total" : undefined,
        barMaxWidth: barWidthCap,
        barWidth: labelsAbove ? aboveBarW : undefined,
        barGap: "25%",
        barCategoryGap: single ? "38%" : "30%",
        itemStyle: {
          color,
          borderRadius: horizontal ? [0, radius, radius, 0] : [radius, radius, 0, 0],
          ...(stacked ? { borderColor: t.surface, borderWidth: 1 } : {}),
        },
        emphasis: { itemStyle: { color: mix(color, t.isDark ? "#ffffff" : "#000000", 0.12) } },
        label: barLabelsOn && (!stacked || last)
          ? {
              show: true,
              position: horizontal ? "right" : "top",
              distance: 6,
              color: t.text,
              fontSize: 11,
              fontWeight: 500,
              formatter: (p: any) => {
                const raw = stacked ? series.reduce((acc, se) => acc + (se.data[p.dataIndex] ?? 0), 0) : p.value;
                const txt = formatAxisValue(raw, stacked ? valueFmt : fmtFor(ctx, s.field));
                return dropSymbol ? txt.replace(/\s?₺$/, "").replace(/^₺/, "") : txt;
              },
            }
          : undefined,
        labelLayout: { hideOverlap: true },
      };
    }
    // line / area
    return {
      type: "line",
      name,
      data: s.data,
      stack: stacked ? "total" : undefined,
      smooth: o.smooth ? 0.35 : false,
      symbol: "circle",
      symbolSize: 8,
      showSymbol: categories.length <= 2,
      connectNulls: false,
      lineStyle: { width: 2, color, cap: "round", join: "round" },
      itemStyle: { color, borderColor: t.surface, borderWidth: 2 },
      emphasis: { focus: "none", scale: 1.2 },
      areaStyle: isArea
        ? {
            color: {
              type: "linear", x: 0, y: 0, x2: 0, y2: 1,
              colorStops: stacked
                ? [{ offset: 0, color: alpha(color, t.isDark ? 0.42 : 0.34) }, { offset: 1, color: alpha(color, t.isDark ? 0.22 : 0.16) }]
                : [{ offset: 0, color: alpha(color, 0.22) }, { offset: 1, color: alpha(color, 0.01) }],
            },
          }
        : undefined,
      label: o.showLabels
        ? { show: true, position: "top", color: t.mutedText, fontSize: 10.5, formatter: (p: any) => (p.dataIndex === categories.length - 1 ? formatAxisValue(p.value, fmtFor(ctx, s.field)) : "") }
        : undefined,
      endLabel: undefined,
    };
  });
  return out;
}

/** Etiket için kısa ama bilgi kaybetmeyen biçim. */
function formatAxisValue(v: unknown, fmt: FormatSpec): string {
  const n = toNumber(v);
  if (n === null) return "";
  if (fmt.format === "currency" && Math.abs(n) >= 1e5) return formatValue(n, { ...fmt, format: "compact" });
  if (fmt.format === "number" && Math.abs(n) >= 1e6) return formatValue(n, { ...fmt, format: "compact" });
  return formatValue(n, fmt);
}

// ---------- combo ----------

function combo(ctx: ChartCtx): EOption {
  const v = ctx.visual;
  const o = v.options ?? {};
  const e = enc(v);
  const t = ctx.theme;
  const pv = pivot(ctx.rows, e.x!, e.y);
  if (!pv.categories.length) throw new EmptyDataError("Seçili filtreler için veri yok.");
  const categories = pv.categories;
  const [barS, ...lineS] = pv.series;
  const barFmt = fmtFor(ctx, barS.field);
  const lineFmt = lineS.length ? fmtFor(ctx, lineS[0].field) : barFmt;
  const legendOn = showLegend(ctx, pv.series.length);
  const barColor = colorForIndex(ctx, 0);

  const yAxis: any[] = [valueAxis(ctx, barFmt)];
  if (lineS.length)
    yAxis.push(valueAxis(ctx, lineFmt, { splitLine: { show: false }, position: "right", alignTicks: true }));

  return {
    ...baseOption(ctx),
    grid: grid(ctx, legendOn),
    legend: legendOn ? legend(ctx, "rect") : { show: false },
    tooltip: {
      ...tooltipBase(t),
      trigger: "axis",
      axisPointer: { type: "shadow", shadowStyle: { color: alpha(t.text, t.isDark ? 0.06 : 0.035) } },
      formatter: (ps: any[]) => {
        if (!ps?.length) return "";
        return (
          ttHead(formatCategory(categories[ps[0].dataIndex], false)) +
          ps.map((p) => ttRow(p.color, formatValue(p.value, fmtFor(ctx, pv.series[p.seriesIndex]?.field)), p.seriesName, p.seriesIndex > 0)).join("")
        );
      },
    },
    xAxis: categoryAxis(ctx, categories, { boundaryGap: true }),
    yAxis,
    series: [
      {
        type: "bar",
        name: fieldLabel(ctx.dataset, barS.field),
        data: barS.data,
        barMaxWidth: 22,
        barCategoryGap: "38%",
        itemStyle: { color: barColor, borderRadius: [4, 4, 0, 0] },
        emphasis: { itemStyle: { color: mix(barColor, t.isDark ? "#ffffff" : "#000000", 0.12) } },
        label: o.showLabels ? { show: true, position: "top", color: t.text, fontSize: 10.5, formatter: (p: any) => formatAxisValue(p.value, barFmt) } : undefined,
      },
      ...lineS.map((s, i) => {
        // çizgi, çubuktan belirgin ayrışsın: ilk çizgi paletin 4. rengini (varsa) alır
        const color = colorForIndex(ctx, i === 0 && palette(ctx).length > 3 ? 3 : i + 1);
        return {
          type: "line",
          name: fieldLabel(ctx.dataset, s.field),
          data: s.data,
          yAxisIndex: 1,
          smooth: o.smooth ? 0.35 : false,
          symbol: "circle",
          symbolSize: 7,
          showSymbol: true,
          lineStyle: { width: 2, color, cap: "round" },
          itemStyle: { color, borderColor: t.surface, borderWidth: 2 },
          z: 3,
        };
      }),
    ],
  };
}

// ---------- pie / donut ----------

const OTHER = "Diğer";

function categoryTotals(ctx: ChartCtx, sortDesc = true) {
  const e = enc(ctx.visual);
  const o = ctx.visual.options ?? {};
  let groups = groupBy(ctx.rows, e.category!, [e.value!]).map((g) => ({
    key: g.key,
    raw: g.raw,
    value: g.values[e.value!] ?? 0,
  }));
  if (sortDesc || o.sort) groups = sortLimit(groups, (g) => g.value, o.sort ?? "desc");
  if (o.limit) groups = groups.slice(0, o.limit);
  return groups;
}

function stableColorMap(ctx: ChartCtx, catField: string, valField: string) {
  const order = seriesOrder(ctx.all, catField, valField).filter((k) => k !== OTHER);
  const map = new Map<string, string>();
  order.forEach((k, i) => map.set(k, colorForIndex(ctx, i)));
  return (k: string) => (k === OTHER ? ctx.theme.deEmphasis : map.get(k) ?? ctx.theme.deEmphasis);
}

function pie(ctx: ChartCtx): EOption {
  const v = ctx.visual;
  const o = v.options ?? {};
  const e = enc(v);
  const t = ctx.theme;
  const fmt = fmtFor(ctx, e.value);
  let items = categoryTotals(ctx);
  if (!items.length) throw new EmptyDataError("Seçili filtreler için veri yok.");
  // Kuyruğu "Diğer"e topla (en fazla 6 adlandırılmış dilim + Diğer)
  const MAX_NAMED = Math.min(6, palette(ctx).length);
  const named = items.filter((i) => i.key !== OTHER);
  const existingOther = items.find((i) => i.key === OTHER)?.value ?? 0;
  if (named.length > MAX_NAMED + 1 || (existingOther && named.length > MAX_NAMED)) {
    const keep = named.slice(0, MAX_NAMED);
    const rest = named.slice(MAX_NAMED).reduce((s, i) => s + i.value, 0) + existingOther;
    items = [...keep, { key: OTHER, raw: OTHER, value: rest }];
  } else if (existingOther) {
    items = [...named, { key: OTHER, raw: OTHER, value: existingOther }];
  }
  const total = items.reduce((s, i) => s + i.value, 0);
  const colorOf = stableColorMap(ctx, e.category!, e.value!);
  const isDonut = v.type === "donut";
  const legendOn = o.showLegend !== false && items.length >= 2;
  const pctFmt = (x: number) => formatValue(total ? x / total : 0, { format: "percent", decimals: 1 });
  const W = Math.max(160, ctx.width);
  const H = Math.max(120, ctx.height);
  const side = legendOn && W >= 440;
  const names = items.map((i) => formatCategory(i.raw));
  const pctOf = new Map(items.map((i) => [formatCategory(i.raw), pctFmt(i.value)]));
  let cx: number, cy: number, r: number;
  const legends: any[] = [];
  const legendItem = (colW: number, extra: any) => {
    const nameW = Math.max(48, colW - 10 - 8 - 50);
    return legend(ctx, "rect", {
      type: "plain",
      orient: "vertical",
      itemGap: 7,
      formatter: (name: string) => `{n|${name}}{p|${pctOf.get(name) ?? ""}}`,
      textStyle: {
        color: t.mutedText, fontFamily: t.fontFamily,
        rich: {
          n: { width: nameW, color: t.mutedText, fontSize: 11.5, fontFamily: t.fontFamily, overflow: "truncate", ellipsis: "…" },
          p: { width: 46, align: "right", color: t.text, fontSize: 11.5, fontWeight: 600, fontFamily: t.fontFamily },
        },
      },
      ...extra,
    });
  };
  if (!legendOn) {
    cx = W / 2; cy = H / 2; r = Math.min(W, H) / 2 - 6;
  } else if (side) {
    const legendW = Math.min(230, W * 0.44);
    const pieW = W - legendW - 16;
    cx = pieW / 2; cy = H / 2; r = Math.min(pieW, H) / 2 - 6;
    legends.push(legendItem(legendW, { left: pieW + 16, top: "middle", data: names }));
  } else {
    const cols = names.length > 4 && W >= 260 ? 2 : 1;
    const perCol = Math.ceil(names.length / cols);
    const legendH = perCol * 21;
    const pieH = H - legendH - 14;
    cx = W / 2; cy = pieH / 2; r = Math.max(30, Math.min(W, pieH) / 2 - 4);
    const colW = W / cols;
    for (let c = 0; c < cols; c++) {
      legends.push(legendItem(colW - 8, { left: c * colW + (cols === 1 ? Math.max(0, (W - 240) / 2) : 4), top: H - legendH, data: names.slice(c * perCol, (c + 1) * perCol) }));
    }
  }

  const out: any = {
    ...baseOption(ctx),
    tooltip: {
      ...tooltipBase(t),
      trigger: "item",
      formatter: (p: any) =>
        ttHead(p.name) + ttRow(p.color, formatValue(p.value, fmt), `Pay ${pctFmt(p.value)}`),
    },
    legend: legends.length ? legends : { show: false },
    series: [
      {
        type: "pie",
        radius: isDonut ? [Math.round(r * 0.64), Math.round(r)] : [0, Math.round(r)],
        center: [cx, cy],
        avoidLabelOverlap: true,
        startAngle: 90,
        padAngle: isDonut ? 1.2 : 0,
        itemStyle: { borderColor: t.surface, borderWidth: isDonut ? 1 : 2, borderRadius: isDonut ? 5 : 3 },
        label: o.showLabels
          ? { show: true, color: t.text, fontSize: 11, formatter: (p: any) => `${p.name}\n${pctFmt(p.value)}` }
          : { show: false },
        labelLine: { show: !!o.showLabels, lineStyle: { color: t.axis } },
        emphasis: { scale: true, scaleSize: 4, label: { show: !!o.showLabels } },
        data: items.map((i) => ({
          name: formatCategory(i.raw),
          value: i.value,
          itemStyle: { color: colorOf(i.key) },
        })),
      },
    ],
  };
  if (isDonut) {
    out.title = {
      text: formatAxisValue(total, fmt.format === "percent" ? fmt : fmt.format === "currency" ? fmt : { ...fmt }),
      subtext: "Toplam",
      left: cx,
      top: cy,
      textAlign: "center",
      textVerticalAlign: "middle",
      itemGap: 2,
      textStyle: { color: t.text, fontSize: r < 80 ? 14 : r < 110 ? 17 : 20, fontWeight: 650, fontFamily: t.fontFamily },
      subtextStyle: { color: t.mutedText, fontSize: 11, fontFamily: t.fontFamily },
    };
  }
  return out;
}

// ---------- funnel ----------

function funnel(ctx: ChartCtx): EOption {
  const e = enc(ctx.visual);
  const t = ctx.theme;
  const fmt = fmtFor(ctx, e.value);
  const items = categoryTotals(ctx);
  if (!items.length) throw new EmptyDataError("Seçili filtreler için veri yok.");
  const base = colorForIndex(ctx, 0);
  const n = items.length;
  const first = items[0]?.value || 1;
  const colors = items.map((_, i) => mix(base, t.surface, n <= 1 ? 0 : (i / (n - 1)) * 0.55));
  return {
    ...baseOption(ctx),
    tooltip: {
      ...tooltipBase(t),
      trigger: "item",
      formatter: (p: any) => ttHead(p.name) + ttRow(p.color, formatValue(p.value, fmt), `İlk adıma oran ${formatValue(p.value / first, { format: "percent" })}`),
    },
    series: [
      {
        type: "funnel",
        left: "8%", right: "8%", top: 8, bottom: 8,
        sort: "descending",
        gap: 2,
        minSize: "18%",
        itemStyle: { borderColor: t.surface, borderWidth: 0, borderRadius: 4 },
        label: {
          show: true,
          position: "inside",
          fontFamily: t.fontFamily,
          fontSize: 11.5,
          formatter: (p: any) => `${p.name}  ·  ${formatAxisValue(p.value, fmt)}`,
        },
        emphasis: { label: { fontWeight: 600 } },
        data: items.map((i, k) => ({
          name: formatCategory(i.raw),
          value: i.value,
          itemStyle: { color: colors[k] },
          label: { color: inkOn(colors[k], t.text) },
        })),
      },
    ],
  };
}

// ---------- treemap ----------

function treemap(ctx: ChartCtx): EOption {
  const e = enc(ctx.visual);
  const t = ctx.theme;
  const fmt = fmtFor(ctx, e.value);
  const items = categoryTotals(ctx);
  if (!items.length) throw new EmptyDataError("Seçili filtreler için veri yok.");
  const colorOf = stableColorMap(ctx, e.category!, e.value!);
  const total = items.reduce((s, i) => s + i.value, 0);
  return {
    ...baseOption(ctx),
    tooltip: {
      ...tooltipBase(t),
      trigger: "item",
      formatter: (p: any) => ttHead(p.name) + ttRow(p.color, formatValue(p.value, fmt), `Pay ${formatValue(total ? p.value / total : 0, { format: "percent" })}`),
    },
    series: [
      {
        type: "treemap",
        left: 0, right: 0, top: 0, bottom: 0,
        roam: false,
        nodeClick: false,
        breadcrumb: { show: false },
        itemStyle: { borderColor: t.surface, borderWidth: 2, gapWidth: 2, borderRadius: 4 },
        label: {
          show: true,
          fontFamily: t.fontFamily,
          fontSize: 12,
          lineHeight: 16,
          formatter: (p: any) => `{n|${p.name}}\n{v|${formatAxisValue(p.value, fmt)}}`,
          rich: { n: { fontSize: 12, fontWeight: 600 }, v: { fontSize: 11 } },
        },
        emphasis: { itemStyle: { borderColor: t.surface } },
        data: items
          .filter((i) => i.value > 0)
          .map((i) => {
            const c = colorOf(i.key);
            return { name: formatCategory(i.raw), value: i.value, itemStyle: { color: c }, label: { color: inkOn(c, t.text) } };
          }),
      },
    ],
  };
}

// ---------- heatmap ----------

function heatmap(ctx: ChartCtx): EOption {
  const v = ctx.visual;
  const e = enc(v);
  const t = ctx.theme;
  const xField = v.encoding.x!;
  const yField = v.encoding.category!;
  const vf = e.value!;
  const fmt = fmtFor(ctx, vf);
  const xs = groupBy(ctx.rows, xField, []).map((g) => g.raw);
  const ys = groupBy(ctx.rows, yField, []).map((g) => g.raw);
  if (!xs.length || !ys.length) throw new EmptyDataError("Seçili filtreler için veri yok.");
  const xi = new Map(xs.map((x, i) => [keyOf(x), i]));
  const yi = new Map(ys.map((y, i) => [keyOf(y), i]));
  const cells = new Map<string, number>();
  for (const r of ctx.rows) {
    const k = `${xi.get(keyOf(r[xField]))}|${yi.get(keyOf(r[yField]))}`;
    cells.set(k, (cells.get(k) ?? 0) + (toNumber(r[vf]) ?? 0));
  }
  const data = [...cells.entries()].map(([k, val]) => {
    const [a, b] = k.split("|").map(Number);
    return [a, b, val];
  });
  const vals = data.map((d) => d[2]);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const showLabels = v.options?.showLabels ?? xs.length * ys.length <= 48;
  const ramp = sequentialRamp(colorForIndex(ctx, 0), t.surface, 6);
  return {
    ...baseOption(ctx),
    grid: { left: 4, right: 8, top: 8, bottom: 62 },
    tooltip: {
      ...tooltipBase(t),
      trigger: "item",
      formatter: (p: any) =>
        ttHead(`${formatCategory(ys[p.value[1]], false)} · ${formatCategory(xs[p.value[0]], false)}`) +
        ttRow(p.color, formatValue(p.value[2], fmt), fieldLabel(ctx.dataset, vf)),
    },
    xAxis: categoryAxis(ctx, xs, {
      axisLine: { show: false },
      axisLabel: {
        ...categoryAxis(ctx, []).axisLabel,
        interval: 0,
        hideOverlap: false,
        width: Math.max(36, (ctx.width - 130) / Math.max(1, xs.length) - 6),
        overflow: "break",
        lineHeight: 13,
      },
    }),
    yAxis: categoryAxis(ctx, ys, { axisLine: { show: false }, axisLabel: { ...categoryAxis(ctx, []).axisLabel, color: t.text, width: 110, overflow: "truncate" } }),
    visualMap: {
      min, max: max === min ? min + 1 : max,
      calculable: false,
      orient: "horizontal",
      left: "center",
      bottom: 0,
      itemWidth: 10,
      itemHeight: 140,
      inRange: { color: ramp },
      textStyle: { color: t.mutedText, fontSize: 10.5, fontFamily: t.fontFamily },
      formatter: (x: number) => formatAxis(x, fmt),
    },
    series: [
      {
        type: "heatmap",
        data,
        itemStyle: { borderColor: t.surface, borderWidth: 2, borderRadius: 3 },
        label: showLabels
          ? {
              show: true,
              fontSize: 10.5,
              fontFamily: t.fontFamily,
              formatter: (p: any) => formatAxisValue(p.value[2], { ...fmt, format: fmt.format === "percent" ? "percent" : "compact" }),
              color: t.text,
            }
          : { show: false },
        emphasis: { itemStyle: { borderColor: t.text, borderWidth: 1 } },
      },
    ],
  };
}

// ---------- scatter ----------

function scatter(ctx: ChartCtx): EOption {
  const v = ctx.visual;
  const e = enc(v);
  const t = ctx.theme;
  const xF = e.x!;
  const yF = e.y[0];
  const labelF = v.encoding.category;
  const xNumeric = ctx.rows.every((r) => r[xF] === null || toNumber(r[xF]) !== null);
  const xFmt = fmtFor(ctx, xF);
  const yFmt = fmtFor(ctx, yF);
  const order = e.series ? seriesOrder(ctx.all, e.series, yF) : ["__all"];
  const groups = order
    .map((name) => ({ name, rows: e.series ? ctx.rows.filter((r) => keyOf(r[e.series!]) === name) : ctx.rows }))
    .filter((g) => g.rows.length);
  if (!groups.length) throw new EmptyDataError("Seçili filtreler için veri yok.");
  const legendOn = showLegend(ctx, groups.length);
  const xCats = xNumeric ? [] : groupBy(ctx.rows, xF, []).map((g) => g.raw);
  return {
    ...baseOption(ctx),
    grid: grid(ctx, legendOn, { right: 16 }),
    legend: legendOn ? legend(ctx, "rect", { icon: "circle", itemWidth: 9, itemHeight: 9 }) : { show: false },
    tooltip: {
      ...tooltipBase(t),
      trigger: "item",
      formatter: (p: any) => {
        const r: Row = p.data.__row;
        const head = labelF ? formatCategory(r[labelF]) : e.series ? p.seriesName : fieldLabel(ctx.dataset, yF);
        return (
          ttHead(head) +
          ttRow(p.color, formatValue(r[yF], yFmt), fieldLabel(ctx.dataset, yF)) +
          ttRow(p.color, xNumeric ? formatValue(r[xF], xFmt) : formatCategory(r[xF]), fieldLabel(ctx.dataset, xF))
        );
      },
    },
    xAxis: xNumeric
      ? valueAxis(ctx, xFmt, { scale: true, splitLine: { show: false }, axisLine: { show: true, lineStyle: { color: t.axis } } })
      : categoryAxis(ctx, xCats),
    yAxis: valueAxis(ctx, yFmt, { scale: true }),
    series: groups.map((g, i) => {
      const color = colorForIndex(ctx, i);
      return {
        type: "scatter",
        name: e.series ? formatCategory(g.name) : fieldLabel(ctx.dataset, yF),
        symbolSize: 10,
        itemStyle: { color: alpha(color, 0.85), borderColor: t.surface, borderWidth: 1.5 },
        emphasis: { scale: 1.4, itemStyle: { color } },
        data: g.rows.map((r) => ({ value: [xNumeric ? toNumber(r[xF]) : keyOf(r[xF]), toNumber(r[yF])], __row: r })),
      };
    }),
  };
}

// ---------- gauge ----------

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const m = v / p;
  const nice = m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10;
  return nice * p;
}

export function kpiValue(ctx: Pick<ChartCtx, "rows" | "visual">, field: string, fallbackAgg?: "avg" | "first"): number | null {
  const o = ctx.visual.options ?? {};
  const vals = ctx.rows.map((r) => toNumber(r[field]));
  const fn = fallbackAgg ? (vals.length > 1 ? "avg" : "first") : o.aggregate ?? "sum";
  const nums = vals.filter((x): x is number => x !== null);
  if (!nums.length) return null;
  switch (fn) {
    case "avg": return nums.reduce((a, b) => a + b, 0) / nums.length;
    case "first": return nums[0];
    case "last": return nums[nums.length - 1];
    case "min": return Math.min(...nums);
    case "max": return Math.max(...nums);
    default: return nums.reduce((a, b) => a + b, 0);
  }
}

function gauge(ctx: ChartCtx): EOption {
  const v = ctx.visual;
  const e = enc(v);
  const t = ctx.theme;
  const fmt = fmtFor(ctx, e.value);
  const value = kpiValue(ctx, e.value!);
  if (value === null) throw new EmptyDataError("Seçili filtreler için veri yok.");
  const target = v.options?.target;
  const max = target ? Math.max(target, value) * (value > target ? 1.05 : 1) : fmt.format === "percent" && value <= 1 ? 1 : niceMax(value * 1.25);
  const color = colorForIndex(ctx, 0);
  const small = Math.min(ctx.width, ctx.height) < 220;
  const ratio = target ? value / target : null;
  const series: any[] = [
    {
      type: "gauge",
      startAngle: 205,
      endAngle: -25,
      min: 0,
      max,
      radius: "92%",
      center: ["50%", "58%"],
      progress: { show: true, width: small ? 10 : 14, roundCap: true, itemStyle: { color } },
      axisLine: { roundCap: true, lineStyle: { width: small ? 10 : 14, color: [[1, t.grid]] } },
      pointer: { show: false },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { show: false },
      anchor: { show: false },
      title: {
        show: true,
        offsetCenter: [0, small ? "34%" : "30%"],
        color: t.mutedText,
        fontSize: 11.5,
        fontFamily: t.fontFamily,
      },
      detail: {
        valueAnimation: true,
        offsetCenter: [0, "-4%"],
        color: t.text,
        fontSize: small ? 20 : 28,
        fontWeight: 650,
        fontFamily: t.fontFamily,
        formatter: (x: number) => formatAxisValue(x, fmt),
      },
      data: [
        {
          value,
          name: target
            ? `Hedef ${formatAxisValue(target, fmt)} · ${formatValue(ratio, { format: "percent", decimals: 0 })}`
            : fieldLabel(ctx.dataset, e.value),
        },
      ],
    },
  ];
  if (target) {
    // hedef işareti: aynı ölçekte ince bir ibre
    series.push({
      type: "gauge",
      startAngle: 205,
      endAngle: -25,
      min: 0,
      max,
      radius: "92%",
      center: ["50%", "58%"],
      axisLine: { show: false },
      progress: { show: false },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { show: false },
      anchor: { show: false },
      title: { show: false },
      detail: { show: false },
      pointer: { show: true, icon: "rect", length: small ? "16%" : "18%", width: 3, offsetCenter: [0, "-84%"], itemStyle: { color: t.text } },
      data: [{ value: target }],
      tooltip: { show: false },
      silent: true,
    });
  }
  return {
    ...baseOption(ctx),
    tooltip: {
      ...tooltipBase(t),
      trigger: "item",
      formatter: () => ttHead(v.title) + ttRow(color, formatValue(value, fmt), "Gerçekleşen") + (target ? ttRow(t.text, formatValue(target, fmt), "Hedef") : ""),
    },
    series,
  };
}

// ---------- giriş ----------

export function buildChartOption(ctx: ChartCtx): EOption {
  switch (ctx.visual.type) {
    case "line":
    case "area":
    case "bar":
      return cartesian(ctx);
    case "combo":
      return combo(ctx);
    case "pie":
    case "donut":
      return pie(ctx);
    case "funnel":
      return funnel(ctx);
    case "treemap":
      return treemap(ctx);
    case "heatmap":
      return heatmap(ctx);
    case "scatter":
      return scatter(ctx);
    case "gauge":
      return gauge(ctx);
    default:
      throw new VisualError(`"${ctx.visual.type}" bir grafik tipi değil.`);
  }
}

/** Görselin kullandığı alanlar (tablo görünümü için). */
export function usedFields(v: Visual, columns: string[]): string[] {
  const e = enc(v);
  const list = [e.x, e.category, e.series, ...(e.y ?? []), e.value, ...(e.columns ?? []), v.options?.deltaField, v.options?.compareField]
    .filter((f): f is string => !!f && columns.includes(f));
  const uniq = [...new Set(list)];
  return uniq.length ? uniq : columns;
}
