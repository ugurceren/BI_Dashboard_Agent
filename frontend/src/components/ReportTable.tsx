// Rapor envanteri akıllı tablo: başlıktan sıralama, kolon filtreleri (değer listesi / metin), kolon göster-gizle,
// yapışkan başlık, sayaç, CSV dışa aktarma. Sıralama ve kolon tercihleri tarayıcıda hatırlanır.
import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { SessionSummary } from "../types";
import { STATUSES, statusInfo } from "../lib/status";

export type ColId = "status" | "title" | "phase" | "domains" | "audience" | "owner" | "vitrin" | "visuals" | "datasets"
  | "kpis" | "created" | "updated";

const PHASE_LABEL: Record<string, string> = { requirements: "İhtiyaç", data: "Veri", design: "Tasarım" };
const STATUS_ORDER = Object.fromEntries(STATUSES.map((s, i) => [s.id, i]));

type Col = {
  id: ColId;
  label: string;
  /** sıralama anahtarı */
  sort: (r: SessionSummary) => string | number;
  /** filtre: "values" → değer listesi (çoklu seçim), "text" → içerir */
  filter?: "values" | "text";
  /** filtre / CSV için hücre değer(ler)i */
  values: (r: SessionSummary) => string[];
  numeric?: boolean;
  defaultHidden?: boolean;
};

const dash = (v?: string | null) => (v && v.trim() ? v.trim() : "—");
const day = (iso?: string) => (iso ? new Date(iso).toLocaleDateString("tr-TR") : "—");

export const COLUMNS: Col[] = [
  { id: "status", label: "Statü", sort: (r) => STATUS_ORDER[r.status ?? "idea"] ?? 0, filter: "values", values: (r) => [statusInfo(r.status).label] },
  { id: "title", label: "Rapor", sort: (r) => (r.title || "").toLocaleLowerCase("tr"), filter: "text",
    values: (r) => [r.title || "Başlıksız", r.business_goal || r.subtitle || ""] },
  { id: "phase", label: "Faz", sort: (r) => ["requirements", "data", "design"].indexOf(r.phase), filter: "values",
    values: (r) => [PHASE_LABEL[r.phase] ?? r.phase], defaultHidden: true },
  { id: "domains", label: "Domain", sort: (r) => (r.domains ?? []).join(", ").toLocaleLowerCase("tr") || "￿", filter: "values",
    values: (r) => (r.domains?.length ? r.domains : ["—"]) },
  { id: "audience", label: "Hedef kitle", sort: (r) => (r.audience || "￿").toLocaleLowerCase("tr"), filter: "values", values: (r) => [dash(r.audience)] },
  { id: "owner", label: "Sahip", sort: (r) => (r.owner_name || r.owner || "￿").toLocaleLowerCase("tr"), filter: "values",
    values: (r) => [dash(r.owner_name || r.owner)], defaultHidden: true },
  { id: "vitrin", label: "Vitrin", sort: (r) => (r.published?.status === "active" ? r.published.version : -1), filter: "values",
    values: (r) => [r.published?.status === "active" ? "Yayında" : "Yayında değil"] },
  { id: "visuals", label: "Görsel", sort: (r) => r.visual_count ?? 0, numeric: true, values: (r) => [String(r.visual_count ?? 0)] },
  { id: "datasets", label: "Veri kümesi", sort: (r) => r.dataset_count ?? 0, numeric: true, values: (r) => [String(r.dataset_count ?? 0)] },
  { id: "kpis", label: "KPI'lar", sort: (r) => (r.kpi_titles ?? r.kpis ?? []).length, filter: "text",
    values: (r) => [(r.kpi_titles?.length ? r.kpi_titles : r.kpis ?? []).join(", ") || "—"], defaultHidden: true },
  { id: "created", label: "Oluşturuldu", sort: (r) => r.createdAt ?? "", values: (r) => [day(r.createdAt)], defaultHidden: true },
  { id: "updated", label: "Güncellendi", sort: (r) => r.updatedAt ?? "", values: (r) => [day(r.updatedAt)] },
];
const BY_ID = Object.fromEntries(COLUMNS.map((c) => [c.id, c])) as Record<ColId, Col>;

type SortState = { col: ColId; dir: "asc" | "desc" };
type Filters = Partial<Record<ColId, string[] | string>>;   // değer listesi ya da metin
type Prefs = { sort: SortState; hidden: ColId[] };

const LS_PREFS = "bi.inventoryTable";
const DEFAULT_PREFS: Prefs = { sort: { col: "updated", dir: "desc" }, hidden: COLUMNS.filter((c) => c.defaultHidden).map((c) => c.id) };
const readPrefs = (): Prefs => {
  try {
    const p = JSON.parse(localStorage.getItem(LS_PREFS) || "null") as Prefs | null;
    if (p && BY_ID[p.sort?.col] && Array.isArray(p.hidden)) return { sort: p.sort, hidden: p.hidden.filter((h) => BY_ID[h]) };
  } catch { /* yoksay */ }
  return DEFAULT_PREFS;
};

function matches(r: SessionSummary, filters: Filters): boolean {
  return Object.entries(filters).every(([id, f]) => {
    const vals = BY_ID[id as ColId].values(r);
    if (Array.isArray(f)) return !f.length || vals.some((v) => f.includes(v));
    const q = (f ?? "").trim().toLocaleLowerCase("tr");
    return !q || vals.some((v) => v.toLocaleLowerCase("tr").includes(q));
  });
}

/** CSV (Excel Türkçe: ; ayraç, UTF-8 BOM) */
function toCsv(rows: SessionSummary[], cols: Col[]): string {
  const esc = (s: string) => (/[;"\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
  const head = cols.flatMap((c) => (c.id === "title" ? ["Rapor", "Amaç"] : [c.label]));
  const body = rows.map((r) => cols.flatMap((c) => (c.id === "title" ? c.values(r) : [c.values(r).join(", ")])).map(esc).join(";"));
  return "﻿" + [head.map(esc).join(";"), ...body].join("\r\n");
}

export function ReportTable({ reports, renderRow }: {
  reports: SessionSummary[];
  /** satır (<tr>): hücre içerikleri ve satır durumu (statü seçici, yeniden adlandırma, işlemler) Home'dadır */
  renderRow: (r: SessionSummary, cols: ColId[]) => ReactNode;
}) {
  const [prefs, setPrefsState] = useState<Prefs>(readPrefs);
  const setPrefs = (p: Prefs) => { setPrefsState(p); try { localStorage.setItem(LS_PREFS, JSON.stringify(p)); } catch { /* yoksay */ } };
  const [filters, setFilters] = useState<Filters>({});
  const [openFilter, setOpenFilter] = useState<ColId | null>(null);
  const [colsOpen, setColsOpen] = useState(false);

  const visible = COLUMNS.filter((c) => !prefs.hidden.includes(c.id));
  const active = Object.entries(filters).filter(([, f]) => (Array.isArray(f) ? f.length : (f ?? "").trim())).length;

  const rows = useMemo(() => {
    const c = BY_ID[prefs.sort.col];
    const f = reports.filter((r) => matches(r, filters));
    const sign = prefs.sort.dir === "asc" ? 1 : -1;
    return [...f].sort((a, b) => {
      const x = c.sort(a), y = c.sort(b);
      const d = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "tr");
      return d * sign || (b.updatedAt ?? "").localeCompare(a.updatedAt ?? "");
    });
  }, [reports, filters, prefs.sort]);

  const toggleSort = (col: ColId) => {
    const same = prefs.sort.col === col;
    const dir = same ? (prefs.sort.dir === "asc" ? "desc" : "asc") : BY_ID[col].numeric || col === "updated" || col === "created" ? "desc" : "asc";
    setPrefs({ ...prefs, sort: { col, dir } });
  };
  const downloadCsv = () => {
    const blob = new Blob([toCsv(rows, visible)], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `rapor-envanteri-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };

  return (
    <div className="sm">
      <div className="sm-bar">
        <span className="sm-total" aria-live="polite">
          {rows.length === reports.length ? `${reports.length} rapor` : `${rows.length} / ${reports.length} rapor`}
        </span>
        {active ? <button type="button" className="sm-btn" onClick={() => setFilters({})}>Filtreleri temizle ({active})</button> : null}
        <span className="sm-spacer" />
        <Popover open={colsOpen} onClose={() => setColsOpen(false)} label="Kolonlar"
          trigger={<button type="button" className="sm-btn" onClick={() => setColsOpen((o) => !o)} aria-expanded={colsOpen} aria-haspopup="dialog">Kolonlar</button>}>
          <div className="sm-pop-list" role="group" aria-label="Görünen kolonlar">
            {COLUMNS.map((c) => (
              <label key={c.id} className="sm-check">
                <input type="checkbox" checked={!prefs.hidden.includes(c.id)} disabled={c.id === "title"}
                  onChange={(e) => setPrefs({ ...prefs, hidden: e.target.checked ? prefs.hidden.filter((h) => h !== c.id) : [...prefs.hidden, c.id] })} />
                {c.label}
              </label>
            ))}
          </div>
          <button type="button" className="sm-link" onClick={() => setPrefs(DEFAULT_PREFS)}>Varsayılana dön</button>
        </Popover>
        <button type="button" className="sm-btn" onClick={downloadCsv} disabled={!rows.length} title="Görünen satır ve kolonları CSV (Excel) olarak indir">CSV indir</button>
      </div>

      <div className="rl-wrap">
        <table className="rl rl-smart">
          <thead>
            <tr>
              {visible.map((c) => {
                const sorted = prefs.sort.col === c.id;
                const f = filters[c.id];
                const on = Array.isArray(f) ? f.length > 0 : !!(f ?? "").trim();
                return (
                  <th key={c.id} className={`${c.numeric ? "num" : ""}${on ? " is-filtered" : ""}`}
                    aria-sort={sorted ? (prefs.sort.dir === "asc" ? "ascending" : "descending") : "none"}>
                    <div className="sm-th">
                      <button type="button" className="sm-sort" onClick={() => toggleSort(c.id)} aria-label={`${c.label} kolonuna göre sırala`}>
                        {c.label}
                        <span className="sm-arrow" aria-hidden="true">{sorted ? (prefs.sort.dir === "asc" ? "▲" : "▼") : "↕"}</span>
                      </button>
                      {c.filter ? (
                        <Popover open={openFilter === c.id} onClose={() => setOpenFilter(null)} label={`${c.label} filtresi`}
                          trigger={
                            <button type="button" className={`sm-funnel${on ? " is-on" : ""}`} aria-label={`${c.label} filtresi`}
                              aria-expanded={openFilter === c.id} onClick={() => setOpenFilter((o) => (o === c.id ? null : c.id))}>
                              <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M2 3h12l-4.5 5.5V13l-3-1.5v-3z" fill={on ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /></svg>
                            </button>
                          }>
                          <ColumnFilter col={c} reports={reports} value={f}
                            onChange={(v) => setFilters((cur) => ({ ...cur, [c.id]: v }))} />
                        </Popover>
                      ) : null}
                    </div>
                  </th>
                );
              })}
              <th aria-label="İşlemler" />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => <Fragment key={r.id}>{renderRow(r, visible.map((c) => c.id))}</Fragment>)}
          </tbody>
        </table>
        {!rows.length ? <p className="muted sm-empty">Filtrelere uyan rapor yok.</p> : null}
      </div>
    </div>
  );
}

function ColumnFilter({ col, reports, value, onChange }: { col: Col; reports: SessionSummary[]; value: string[] | string | undefined; onChange: (v: string[] | string) => void }) {
  const [q, setQ] = useState("");
  const counts = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of reports) for (const v of new Set(col.values(r))) m.set(v, (m.get(v) ?? 0) + 1);
    return [...m.entries()].sort((a, b) => (a[0] === "—" ? 1 : b[0] === "—" ? -1 : a[0].localeCompare(b[0], "tr")));
  }, [col, reports]);
  if (col.filter === "text") {
    return (
      <input className="dict-input sm-text" autoFocus placeholder={`${col.label} içinde ara…`} value={typeof value === "string" ? value : ""}
        onChange={(e) => onChange(e.target.value)} aria-label={`${col.label} içinde ara`} />
    );
  }
  const sel = Array.isArray(value) ? value : [];
  const shown = counts.filter(([v]) => !q || v.toLocaleLowerCase("tr").includes(q.toLocaleLowerCase("tr")));
  return (
    <>
      {counts.length > 8 ? <input className="dict-input sm-text" autoFocus placeholder="Değer ara…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Değer ara" /> : null}
      <div className="sm-pop-list" role="group" aria-label={`${col.label} değerleri`}>
        {shown.map(([v, n]) => (
          <label key={v} className="sm-check">
            <input type="checkbox" checked={sel.includes(v)} onChange={(e) => onChange(e.target.checked ? [...sel, v] : sel.filter((x) => x !== v))} />
            <span className="sm-val">{v}</span><span className="sm-n">{n}</span>
          </label>
        ))}
      </div>
      {sel.length ? <button type="button" className="sm-link" onClick={() => onChange([])}>Seçimi temizle</button> : null}
    </>
  );
}

/** Tıklayınca açılan küçük panel; dışarı tıklayınca ya da Esc ile kapanır. */
function Popover({ open, onClose, trigger, label, children }: { open: boolean; onClose: () => void; trigger: ReactNode; label: string; children: ReactNode }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  useEffect(() => {
    if (!open || !ref.current) return;
    // tablo kaydırma kutusunun içinde kesilmesin: ekrana göre (fixed) konumlanır
    const r = ref.current.getBoundingClientRect();
    setPos({ top: r.bottom + 6, left: Math.max(8, Math.min(r.left, window.innerWidth - 316)) });
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const down = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) onClose(); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    const scroll = (e: Event) => { if (!(e.target instanceof Node && ref.current?.contains(e.target))) onClose(); };   // sabit konum kaymasın
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key);
    window.addEventListener("scroll", scroll, true);
    return () => {
      document.removeEventListener("mousedown", down);
      document.removeEventListener("keydown", key);
      window.removeEventListener("scroll", scroll, true);
    };
  }, [open, onClose]);
  return (
    <span className="sm-pop-host" ref={ref}>
      {trigger}
      {open && pos ? <div className="sm-pop" role="dialog" aria-label={label} style={{ top: pos.top, left: pos.left }}>{children}</div> : null}
    </span>
  );
}
