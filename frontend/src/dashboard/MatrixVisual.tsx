// Matris (pivot tablo): uzun formatlı veri istemcide pivotlanır. Satır boyutları (1-2 seviye) aç/kapa gruplar,
// sütun boyutunun değerleri veriden sütun olur; ara toplam, genel toplam satırı ve sütunu; başlığa tıklayıp sıralama;
// yapışkan başlık ve ilk kolon; isteğe bağlı koşullu hücre rengi; satır etiketine tıklayınca çapraz filtre.
import { useMemo, useState, type CSSProperties, type ReactNode } from "react";
import type { CellValue } from "../types";
import { enc, fieldDef, fieldLabel } from "./charts";
import { keyOf, type AggregateFn, type Row } from "./data";
import { formatCategory, formatValue, isPeriodLike, resolveFormat, toNumber, type FormatSpec } from "./format";
import { inkOn, mix } from "./theme";
import { CardHead, type VisualProps } from "./visuals";

const TOTAL = "\u0000toplam";
const OTHER = "\u0000diger";

export interface MatrixColumn { key: string; raw: CellValue; label: string }

export interface MatrixNode {
  key: string;
  raw: CellValue;
  field: string | null;          // bu seviyenin satır alanı (kök: null)
  depth: number;                 // kök -1, ilk seviye 0
  path: string;
  children: MatrixNode[];
  /** sütun anahtarı (TOTAL dahil) → ölçü → değer */
  cells: Map<string, Record<string, number | null>>;
}

export interface MatrixModel {
  rows: string[];
  values: string[];
  columnDim?: string;
  columns: MatrixColumn[];        // sütun boyutu değerleri (gösterilenler; "Diğer" dahil)
  root: MatrixNode;               // root.cells = genel toplam
  trimmed?: string;               // sütun sınırı uygulandıysa açıklama
}

interface Bucket { nums: (number | null)[]; n: number }

function agg(b: Bucket | undefined, fn: AggregateFn): number | null {
  if (!b) return null;
  if (fn === "count") return b.n;
  const nums = b.nums.filter((v): v is number => v !== null);
  if (!nums.length) return null;
  switch (fn) {
    case "avg": return nums.reduce((a, c) => a + c, 0) / nums.length;
    case "min": return Math.min(...nums);
    case "max": return Math.max(...nums);
    case "first": return nums[0];
    case "last": return nums[nums.length - 1];
    default: return nums.reduce((a, c) => a + c, 0);
  }
}

/** Etiket sırası: dönem → artan, sayı → artan, metin → Türkçe alfabetik; boş en sonda. */
function compareRaw(a: CellValue, b: CellValue): number {
  if (a === null || a === undefined) return b === null || b === undefined ? 0 : 1;
  if (b === null || b === undefined) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  if (isPeriodLike(a) && isPeriodLike(b)) return String(a) < String(b) ? -1 : String(a) > String(b) ? 1 : 0;
  return String(a).localeCompare(String(b), "tr", { numeric: true });
}

/** Uzun formatlı satırlardan matris modeli. Toplamlar hücrelerden değil alttaki satırlardan hesaplanır (ort. / adet doğru). */
export function buildMatrix(data: Row[], rows: string[], columnDim: string | undefined, values: string[],
  fn: AggregateFn = "sum", maxColumns?: number): MatrixModel {
  // sütunlar
  let columns: MatrixColumn[] = [];
  let colOf: (r: Row) => string | null = () => null;
  let trimmed: string | undefined;
  if (columnDim) {
    const seen = new Map<string, CellValue>();
    for (const r of data) {
      const k = keyOf(r[columnDim]);
      if (!seen.has(k)) seen.set(k, r[columnDim] ?? null);
    }
    let cols = [...seen.entries()].map(([key, raw]) => ({ key, raw, label: formatCategory(raw) }))
      .sort((a, b) => compareRaw(a.raw, b.raw));
    const map = new Map(cols.map((c) => [c.key, c.key]));
    if (maxColumns && cols.length > maxColumns) {
      const n = cols.length;
      if (cols.every((c) => isPeriodLike(c.raw))) {
        // dönem: son N dönem; eskiler dışarıda kalır (toplamlar gösterilen dönemleri kapsar)
        cols = cols.slice(-maxColumns);
        for (const k of [...map.keys()]) if (!cols.some((c) => c.key === k)) map.delete(k);
        trimmed = `Son ${maxColumns} dönem gösteriliyor (toplam ${n}).`;
      } else {
        // kategori: ilk ölçüye göre en büyük N; kalanı "Diğer" sütununda (toplamlar tüm veriyi kapsar)
        const size = new Map<string, number>();
        for (const r of data) {
          const k = keyOf(r[columnDim]);
          size.set(k, (size.get(k) ?? 0) + Math.abs(toNumber(r[values[0]]) ?? 0));
        }
        const keep = new Set([...cols].sort((a, b) => (size.get(b.key) ?? 0) - (size.get(a.key) ?? 0)).slice(0, maxColumns).map((c) => c.key));
        cols = cols.filter((c) => keep.has(c.key));
        for (const k of map.keys()) if (!keep.has(k)) map.set(k, OTHER);
        cols.push({ key: OTHER, raw: "Diğer", label: "Diğer" });
        trimmed = `En büyük ${maxColumns} sütun gösteriliyor; kalan ${n - maxColumns} değer "Diğer" sütununda.`;
      }
    }
    columns = cols;
    colOf = (r) => map.get(keyOf(r[columnDim])) ?? null;
  }

  type Acc = { node: MatrixNode; buckets: Map<string, Record<string, Bucket>>; kids: Map<string, Acc> };
  const mk = (key: string, raw: CellValue, field: string | null, depth: number, path: string): Acc => ({
    node: { key, raw, field, depth, path, children: [], cells: new Map() }, buckets: new Map(), kids: new Map(),
  });
  const root = mk("", null, null, -1, "");
  const add = (a: Acc, ck: string, r: Row) => {
    let b = a.buckets.get(ck);
    if (!b) a.buckets.set(ck, (b = Object.fromEntries(values.map((m) => [m, { nums: [], n: 0 }]))));
    for (const m of values) {
      const v = r[m];
      b[m].nums.push(toNumber(v));
      if (v !== null && v !== undefined && v !== "") b[m].n++;
    }
  };
  for (const r of data) {
    const ck = columnDim ? colOf(r) : null;
    if (columnDim && ck === null) continue;            // sınır dışında kalan dönem
    let a = root;
    const touch = (x: Acc) => { add(x, TOTAL, r); if (ck) add(x, ck, r); };
    touch(a);
    rows.forEach((f, i) => {
      const k = keyOf(r[f]);
      let child = a.kids.get(k);
      if (!child) a.kids.set(k, (child = mk(k, r[f] ?? null, f, i, a.node.path ? `${a.node.path}\u0001${k}` : k)));
      a = child;
      touch(a);
    });
  }
  const finish = (a: Acc): MatrixNode => {
    for (const [ck, b] of a.buckets) a.node.cells.set(ck, Object.fromEntries(values.map((m) => [m, agg(b[m], fn)])));
    a.node.children = [...a.kids.values()].map(finish).sort((x, y) => compareRaw(x.raw, y.raw));
    return a.node;
  };
  return { rows, values, columnDim, columns, root: finish(root), trimmed };
}

/** col: sütun anahtarı (TOTAL dahil) ya da "label" (satır etiketine göre) */
type SortState = { col: string; measure?: string; dir: "asc" | "desc" };

function sortTree(node: MatrixNode, s: SortState | null): MatrixNode {
  if (!s || !node.children.length) return node;
  const kids = node.children.map((c) => sortTree(c, s));
  const sign = s.dir === "asc" ? 1 : -1;
  kids.sort((a, b) => {
    if (s.col === "label") return sign * compareRaw(a.raw, b.raw);
    const av = a.cells.get(s.col)?.[s.measure ?? ""] ?? null;
    const bv = b.cells.get(s.col)?.[s.measure ?? ""] ?? null;
    if (av === null) return bv === null ? 0 : 1;
    if (bv === null) return -1;
    return sign * (av - bv);
  });
  return { ...node, children: kids };
}

const BLANK = "";

export function MatrixVisual(p: VisualProps) {
  const { visual, dataset, theme } = p;
  const o = visual.options ?? {};
  const e = enc(visual);
  const fn: AggregateFn = o.aggregate ?? "sum";
  const rows = p.table!.rows;
  const model = useMemo(() => buildMatrix(rows, e.rows, e.columnDim, e.values, fn, o.maxColumns),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, e.rows.join("\u0001"), e.columnDim, e.values.join("\u0001"), fn, o.maxColumns]);
  const leveled = model.rows.length > 1;

  // varsayılan: çok satırlı iki seviyeli matris ilk seviyede kapalı açılır
  const [collapsed, setCollapsed] = useState<Set<string>>(() => {
    const leaves = model.root.children.reduce((n, c) => n + c.children.length, 0);
    return leveled && leaves > 40 ? new Set(model.root.children.map((c) => c.path)) : new Set();
  });
  const defaultSort: SortState | null = o.sort ? { col: TOTAL, measure: model.values[0], dir: o.sort } : null;
  const [sort, setSort] = useState<SortState | null>(null);
  const active = sort ?? defaultSort;
  const tree = useMemo(() => sortTree(model.root, active), [model, active]);

  const showSub = o.subtotals !== false;
  const showRowTotal = o.rowTotals !== false;
  const showColTotal = !!model.columnDim && o.columnTotals !== false;
  // gösterilen sütunlar: (sütun değeri | TOTAL) × ölçü
  const colKeys = model.columnDim ? [...model.columns.map((c) => c.key), ...(showColTotal ? [TOTAL] : [])] : [TOTAL];

  // tek ölçü: options.format önce (grafiklerdeki gibi); birden çok ölçü: her ölçü kendi biçiminde, yalnız "compact" hepsine
  // uygulanır ve para birimi yalnız tutar alanlarına eklenir (adet ₺ almasın)
  const fmts = useMemo(() => Object.fromEntries(model.values.map((m): [string, FormatSpec] => {
    if (fn === "count") return [m, { format: "number", decimals: 0 }];
    const def = fieldDef(dataset, m);
    if (model.values.length === 1) return [m, resolveFormat(def, o)];
    return [m, resolveFormat(def, { ...o, format: o.format === "compact" ? "compact" : def?.format,
      currency: def?.format === "currency" ? o.currency : undefined })];
  })),
  // eslint-disable-next-line react-hooks/exhaustive-deps
  [model.values, dataset, fn, o.format, o.currency, o.decimals]);

  // koşullu renk: ölçü başına en alt seviye hücrelerinin aralığı (toplamlar renklenmez)
  const ranges = useMemo(() => {
    if (!o.conditionalColor) return null;
    const out: Record<string, [number, number]> = {};
    const cellKeys = model.columnDim ? model.columns.map((c) => c.key) : [TOTAL];
    const walk = (n: MatrixNode) => {
      if (n.children.length) return n.children.forEach(walk);
      for (const ck of cellKeys) for (const m of model.values) {
        const v = n.cells.get(ck)?.[m];
        if (v === null || v === undefined) continue;
        const r = out[m];
        out[m] = r ? [Math.min(r[0], v), Math.max(r[1], v)] : [v, v];
      }
    };
    model.root.children.forEach(walk);
    return out;
  }, [model, o.conditionalColor]);
  const colorFor = (m: string, v: number | null): CSSProperties | undefined => {
    const r = ranges?.[m];
    if (!r || v === null) return undefined;
    const t = r[1] > r[0] ? (v - r[0]) / (r[1] - r[0]) : 1;
    const fill = mix(theme.accent, theme.surface, 0.9 - 0.62 * t);
    return { background: fill, color: inkOn(fill, theme.isDark ? theme.surface : theme.text) };
  };

  const toggle = (path: string) => setCollapsed((s) => {
    const n = new Set(s);
    if (n.has(path)) n.delete(path); else n.add(path);
    return n;
  });
  const allCollapsed = leveled && tree.children.every((c) => !c.children.length || collapsed.has(c.path));
  const toggleAll = () => setCollapsed(allCollapsed ? new Set() : new Set(tree.children.map((c) => c.path)));

  const sortBy = (col: string, measure?: string) => {
    const on = active && active.col === col && (col === "label" || active.measure === measure);
    const dir: "asc" | "desc" = on ? (active!.dir === "desc" ? "asc" : "desc") : col === "label" ? "asc" : "desc";
    setSort({ col, measure, dir });
  };
  const sortMark = (col: string, measure?: string) => {
    const on = active && active.col === col && (col === "label" || active.measure === measure);
    return <span className={`db-sort${on ? " is-on" : ""}`}>{on ? (active!.dir === "asc" ? "↑" : "↓") : "↕"}</span>;
  };
  const ariaSort = (col: string, measure?: string) => {
    const on = active && active.col === col && (col === "label" || active.measure === measure);
    return on ? (active!.dir === "asc" ? "ascending" : "descending") : "none";
  };

  const multi = model.values.length > 1;
  const measureLabel = (m: string) => fieldLabel(dataset, m);
  const colLabel = (ck: string) => ck === TOTAL ? "Toplam" : model.columns.find((c) => c.key === ck)?.label ?? ck;
  const colTitle = (ck: string) => {
    const c = model.columns.find((x) => x.key === ck);
    return c && c.key !== OTHER ? formatCategory(c.raw, false) : colLabel(ck);
  };
  const selectedKey = p.selected !== undefined ? keyOf(p.selected) : null;

  const cellsOf = (n: MatrixNode, show: boolean, colored: boolean) => colKeys.flatMap((ck) => model.values.map((m) => {
    const v = show ? n.cells.get(ck)?.[m] ?? null : null;
    return (
      <td key={`${ck}|${m}`} className={`is-num${ck === TOTAL && model.columnDim ? " db-mx-coltotal" : ""}`}
        style={colored && ck !== TOTAL ? colorFor(m, v) : undefined}>
        {v === null ? BLANK : formatValue(v, fmts[m])}
      </td>
    );
  }));

  const body: ReactNode[] = [];
  const emit = (n: MatrixNode) => {
    const group = n.children.length > 0;
    const open = group && !collapsed.has(n.path);
    const sel = selectedKey !== null && keyOf(n.raw) === selectedKey;
    const label = n.raw === null || n.raw === undefined || n.raw === "" ? "(boş)" : formatCategory(n.raw, false);
    const select = p.onSelect && n.field ? () => p.onSelect!(n.field!, n.raw) : group ? () => toggle(n.path) : undefined;
    body.push(
      <tr key={n.path} className={`${group ? "db-mx-group" : "db-mx-leaf"}${sel ? " is-selected" : ""}`}
        data-level={n.depth} data-key={n.key}>
        <th scope="row" className="db-mx-label" style={{ ["--lvl" as string]: n.depth }}>
          <span className="db-mx-head">
            {group ? (
              <button type="button" className="db-mx-toggle" aria-expanded={open} aria-label={open ? `${label} daralt` : `${label} genişlet`}
                onClick={(ev) => { ev.stopPropagation(); toggle(n.path); }}>
                <svg viewBox="0 0 10 10" width="10" height="10" aria-hidden="true"><path d={open ? "M1.5 3.5 5 7l3.5-3.5" : "M3.5 1.5 7 5 3.5 8.5"} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
              </button>
            ) : leveled ? <span className="db-mx-spacer" /> : null}
            <span className={`db-mx-text${select ? " is-click" : ""}`} title={label} onClick={select}>{label}</span>
          </span>
        </th>
        {cellsOf(n, !group || showSub || !open, !group)}
      </tr>,
    );
    if (open) n.children.forEach(emit);
  };
  tree.children.forEach(emit);

  if (!rows.length) {
    return (
      <div className="db-card db-card--table">
        <CardHead visual={visual} filterNote={p.filterNote} selectionNote={p.selectionNote} />
        <div className="db-empty-note">Seçili filtreler için veri yok.</div>
      </div>
    );
  }

  const rowHeader = model.rows.map((f) => fieldLabel(dataset, f)).join(" / ");
  return (
    <div className="db-card db-card--table db-card--matrix">
      <CardHead visual={visual} filterNote={p.filterNote} selectionNote={p.selectionNote} />
      <div className="db-card-body db-card-body--flush">
        <div className="db-table-wrap db-mx-wrap" style={{ ["--db-zebra" as string]: theme.surface2 }}>
          <table className="db-matrix">
            <thead>
              <tr>
                <th className="db-mx-corner" rowSpan={model.columnDim && multi ? 2 : 1} scope="col"
                  aria-sort={ariaSort("label")} onClick={() => sortBy("label")}>
                  <span className="db-mx-head">
                    {leveled ? (
                      <button type="button" className="db-mx-toggle" onClick={(ev) => { ev.stopPropagation(); toggleAll(); }}
                        aria-label={allCollapsed ? "Tümünü genişlet" : "Tümünü daralt"} title={allCollapsed ? "Tümünü genişlet" : "Tümünü daralt"}>
                        <svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true"><rect x="1" y="1" width="10" height="10" rx="2" fill="none" stroke="currentColor" strokeWidth="1.2" /><path d={allCollapsed ? "M3.5 6h5M6 3.5v5" : "M3.5 6h5"} stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" /></svg>
                      </button>
                    ) : null}
                    <span className="db-th">{rowHeader}{sortMark("label")}</span>
                  </span>
                </th>
                {model.columnDim
                  ? colKeys.map((ck) => multi ? (
                    <th key={ck} colSpan={model.values.length} scope="colgroup" className={`is-num db-mx-colhead${ck === TOTAL ? " db-mx-coltotal" : ""}`} title={colTitle(ck)}>
                      {colLabel(ck)}
                    </th>
                  ) : (
                    <th key={ck} scope="col" className={`is-num${ck === TOTAL ? " db-mx-coltotal" : ""}`} title={`${colTitle(ck)} · ${measureLabel(model.values[0])}`}
                      aria-sort={ariaSort(ck, model.values[0])} onClick={() => sortBy(ck, model.values[0])}>
                      <span className="db-th">{colLabel(ck)}{sortMark(ck, model.values[0])}</span>
                    </th>
                  ))
                  : model.values.map((m) => (
                    <th key={m} scope="col" className="is-num" aria-sort={ariaSort(TOTAL, m)} onClick={() => sortBy(TOTAL, m)}>
                      <span className="db-th">{measureLabel(m)}{sortMark(TOTAL, m)}</span>
                    </th>
                  ))}
              </tr>
              {model.columnDim && multi ? (
                <tr>
                  {colKeys.flatMap((ck) => model.values.map((m) => (
                    <th key={`${ck}|${m}`} scope="col" className={`is-num db-mx-measure${ck === TOTAL ? " db-mx-coltotal" : ""}`}
                      aria-sort={ariaSort(ck, m)} onClick={() => sortBy(ck, m)} title={`${colTitle(ck)} · ${measureLabel(m)}`}>
                      <span className="db-th">{measureLabel(m)}{sortMark(ck, m)}</span>
                    </th>
                  )))}
                </tr>
              ) : null}
            </thead>
            <tbody>{body}</tbody>
            {showRowTotal ? (
              <tfoot>
                <tr className="db-mx-total">
                  <th scope="row" className="db-mx-label">Genel toplam</th>
                  {cellsOf(tree, true, false)}
                </tr>
              </tfoot>
            ) : null}
          </table>
        </div>
        {model.trimmed ? <div className="db-mx-note">{model.trimmed}</div> : null}
      </div>
    </div>
  );
}
