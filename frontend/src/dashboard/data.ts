// Veri dönüşümleri: satır nesneleri, istemci tarafı filtreler, gruplama, pivot.
import type { CellValue, DashboardData, DatasetData, Filter, ReportSpec } from "../types";
import { isPeriodLike, toNumber } from "./format";

export type Row = Record<string, CellValue>;
export type FilterState = Record<string, string[]>; // filter.id → seçili değerler (boş = tümü)
export type AggregateFn = "sum" | "avg" | "count" | "first" | "last" | "min" | "max";

export function toRows(d: DatasetData): Row[] {
  const cols = d.columns ?? [];
  return (d.rows ?? []).map((r) => {
    const o: Row = {};
    for (let i = 0; i < cols.length; i++) o[cols[i]] = r[i] ?? null;
    return o;
  });
}

export const keyOf = (v: CellValue | undefined): string => (v === null || v === undefined ? "∅" : String(v));

/** Filtrenin bu dataset'teki karşılığı: field ya da (export'ta) model kolonunun alan kökeni. */
export function fieldFor(f: Filter, datasetId: string, columns: string[], data: DashboardData): string | null {
  if (f.field && columns.includes(f.field)) return f.field;
  const key = data.filter_keys?.[f.id] ?? (f.table && f.column ? `${f.table}.${f.column}`.toLowerCase() : null);
  const b = key ? data.bindings?.[datasetId] : undefined;
  if (b) for (const [field, k] of Object.entries(b)) if (k === key && columns.includes(field)) return field;
  return null;
}

/** Bir filtre alanının tüm dataset'lerdeki ayrık değerleri. */
export function filterOptions(filter: Filter, data: DashboardData): string[] {
  const set = new Set<string>();
  for (const [id, ds] of Object.entries(data.datasets ?? {})) {
    if (!ds || ds.error) continue;
    const field = fieldFor(filter, id, ds.columns ?? [], data);
    const idx = field ? ds.columns.indexOf(field) : -1;
    if (idx < 0) continue;
    for (const r of ds.rows ?? []) {
      const v = r[idx];
      if (v !== null && v !== undefined && v !== "") set.add(String(v));
    }
  }
  const arr = [...set];
  if (arr.every(isPeriodLike)) return arr.sort();
  return arr.sort((a, b) => a.localeCompare(b, "tr"));
}

export function applyFilters(rows: Row[], fields: (string | null)[], filters: Filter[], state: FilterState): Row[] {
  const active = filters.map((f, i) => ({ f, field: fields[i] })).filter(({ f, field }) => field && (state[f.id]?.length ?? 0) > 0);
  if (!active.length) return rows;
  const sets = active.map(({ f, field }) => ({ field: field!, set: new Set(state[f.id]) }));
  return rows.filter((r) => sets.every(({ field, set }) => set.has(keyOf(r[field]))));
}

export function aggregate(values: (number | null)[], fn: AggregateFn = "sum"): number | null {
  const nums = values.filter((v): v is number => v !== null);
  if (!nums.length) return null;
  switch (fn) {
    case "first": return nums[0];
    case "last": return nums[nums.length - 1];
    case "min": return Math.min(...nums);
    case "max": return Math.max(...nums);
    case "avg": return nums.reduce((a, b) => a + b, 0) / nums.length;
    case "count": return nums.length;
    case "sum":
    default: return nums.reduce((a, b) => a + b, 0);
  }
}

export interface Group {
  key: string;
  raw: CellValue;
  values: Record<string, number | null>;
  count: number;
}

/** keyField'a göre gruplayıp valueFields toplamı (ilk görülme sırası korunur; dönem ise artan sıralı). */
export function groupBy(rows: Row[], keyField: string, valueFields: string[], fn: AggregateFn = "sum"): Group[] {
  const map = new Map<string, { raw: CellValue; buckets: Record<string, (number | null)[]>; count: number }>();
  for (const r of rows) {
    const k = keyOf(r[keyField]);
    let g = map.get(k);
    if (!g) {
      g = { raw: r[keyField] ?? null, buckets: Object.fromEntries(valueFields.map((f) => [f, []])), count: 0 };
      map.set(k, g);
    }
    g.count++;
    for (const f of valueFields) g.buckets[f].push(toNumber(r[f]));
  }
  let groups: Group[] = [...map.entries()].map(([key, g]) => ({
    key,
    raw: g.raw,
    count: g.count,
    values: Object.fromEntries(valueFields.map((f) => [f, aggregate(g.buckets[f], fn)])),
  }));
  if (groups.length > 1 && groups.every((g) => isPeriodLike(g.raw))) {
    groups = groups.sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
  }
  return groups;
}

/** Seri değerlerinin sabit sırası (renk varlığı izler, sırası değil): toplam büyükten küçüğe. */
export function seriesOrder(rows: Row[], seriesField: string, valueField: string): string[] {
  const totals = new Map<string, number>();
  for (const r of rows) {
    const k = keyOf(r[seriesField]);
    totals.set(k, (totals.get(k) ?? 0) + Math.abs(toNumber(r[valueField]) ?? 0));
  }
  return [...totals.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k);
}

export interface Pivot {
  categories: CellValue[];
  series: { name: string; field: string; data: (number | null)[] }[];
}

/** x ekseni + (y alanları | tek y + seri alanı) → kategoriler ve seri dizileri. */
export function pivot(rows: Row[], x: string, y: string[], seriesField?: string, order?: string[]): Pivot {
  if (seriesField && y.length) {
    const yf = y[0];
    const xs = groupBy(rows, x, [yf]);
    const names = order ?? seriesOrder(rows, seriesField, yf);
    const present = new Set(rows.map((r) => keyOf(r[seriesField])));
    const series = names
      .filter((n) => present.has(n))
      .map((name) => {
        const sub = rows.filter((r) => keyOf(r[seriesField]) === name);
        const g = new Map(groupBy(sub, x, [yf]).map((gg) => [gg.key, gg.values[yf]]));
        return { name, field: yf, data: xs.map((c) => g.get(c.key) ?? null) };
      });
    return { categories: xs.map((c) => c.raw), series };
  }
  const groups = groupBy(rows, x, y);
  return {
    categories: groups.map((g) => g.raw),
    series: y.map((f) => ({ name: f, field: f, data: groups.map((g) => g.values[f]) })),
  };
}

/** Spec'in tüm dataset'lerini filtrelenmiş satırlara çevirir. */
export function buildTables(spec: ReportSpec, data: DashboardData, state: FilterState) {
  const out: Record<string, { all: Row[]; rows: Row[]; columns: string[]; error?: string; denied?: boolean; filterFields?: (string | null)[] }> = {};
  for (const [id, ds] of Object.entries(data.datasets ?? {})) {
    if (!ds) continue;
    if (ds.error) {
      out[id] = { all: [], rows: [], columns: ds.columns ?? [], error: ds.error, denied: ds.denied };
      continue;
    }
    const all = toRows(ds);
    const filters = spec.filters ?? [];
    const fields = filters.map((f) => fieldFor(f, id, ds.columns ?? [], data));
    out[id] = { all, rows: applyFilters(all, fields, filters, state), columns: ds.columns ?? [], filterFields: fields };
  }
  return out;
}
