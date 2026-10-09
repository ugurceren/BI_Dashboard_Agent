// Rapor envanteri: kart ya da liste görünümü (amaç, domain, kapsam, içerik); aç / yeniden adlandır / indir / sil.
import { useMemo, useState, type ReactNode } from "react";
import type { SessionSummary } from "../types";
import { STATUSES, statusInfo, type ReportStatus } from "../lib/status";
import { ConfirmDialog } from "./ConfirmDialog";
import { StatusPicker } from "./StatusPicker";
import { ReportTable, type ColId } from "./ReportTable";
import "./home.css";

function relTime(iso?: string): string {
  const t = Date.parse(iso ?? "");
  if (!Number.isFinite(t)) return "";
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 60) return "az önce";
  if (s < 3600) return `${Math.floor(s / 60)} dk önce`;
  if (s < 86400) return `${Math.floor(s / 3600)} sa önce`;
  return new Date(t).toLocaleDateString("tr-TR", { day: "numeric", month: "short", year: "numeric" });
}

const PHASE_LABEL: Record<string, string> = { requirements: "İhtiyaç", data: "Veri", design: "Tasarım" };

const VISUAL_LABEL: Record<string, string> = {
  kpi: "KPI", line: "Çizgi", area: "Alan", bar: "Çubuk", pie: "Pasta", donut: "Halka", table: "Tablo", scatter: "Dağılım",
  heatmap: "Isı haritası", funnel: "Huni", gauge: "Gösterge", treemap: "Ağaç harita", combo: "Kombine", text: "Metin",
};

const PATHS = {
  open: "M6.5 3.5h-3v9h9v-3M9 2.5h4.5V7M13.5 2.5 7.5 8.5",
  rename: "M3 11.5V13h1.5l7-7L10 4.5l-7 7ZM9.5 5 11 6.5",
  download: "M8 2.5v8M4.5 7 8 10.5 11.5 7M3 13.5h10",
  del: "M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5",
  search: "M7 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10ZM10.7 10.7 14 14",
  plus: "M8 3v10M3 8h10",
  cards: "M2.5 2.5h4.5v4.5H2.5zM9 2.5h4.5v4.5H9zM2.5 9h4.5v4.5H2.5zM9 9h4.5v4.5H9z",
  list: "M5.5 4h8M5.5 8h8M5.5 12h8M2.5 4h.01M2.5 8h.01M2.5 12h.01",
};
const Ico = ({ d }: { d: string }) => (
  <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
);

type Sort = "updated" | "title";
type ViewMode = "cards" | "list";
const LS_VIEW = "bi.inventoryView";
const readView = (): ViewMode => {
  try { return localStorage.getItem(LS_VIEW) === "list" ? "list" : "cards"; } catch { return "cards"; }
};

type Actions = {
  onOpen: (id: string) => void;
  onRename: (id: string, title: string) => Promise<boolean>;
  onDelete: (id: string) => void;
  onStatus: (id: string, status: ReportStatus) => void;
  exportUrl: (id: string) => string;
};

export function Home({ reports, loading, onNew, ...actions }: Actions & {
  reports: SessionSummary[];
  loading: boolean;
  onNew: () => void;
}) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("updated");
  const [status, setStatus] = useState<ReportStatus | "all">("all");
  const [view, setViewState] = useState<ViewMode>(readView);
  const setView = (v: ViewMode) => { setViewState(v); try { localStorage.setItem(LS_VIEW, v); } catch { /* yoksay */ } };
  const counts = useMemo(() => Object.fromEntries(STATUSES.map((s) => [s.id, reports.filter((r) => (r.status ?? "idea") === s.id).length])), [reports]);
  const list = useMemo(() => {
    const ql = q.trim().toLocaleLowerCase("tr");
    const f = reports.filter((r) => (status === "all" || (r.status ?? "idea") === status) && (!ql ||
      JSON.stringify([r.title, r.subtitle, r.business_goal, r.kpis, r.dimensions, r.audience, r.domains]).toLocaleLowerCase("tr").includes(ql)));
    return [...f].sort((a, b) => (sort === "title" ? (a.title || "").localeCompare(b.title || "", "tr")
      : (b.updatedAt ?? "").localeCompare(a.updatedAt ?? "")));
  }, [reports, q, sort, status]);

  return (
    <div className="home">
      <div className="home-head">
        <div className="st-filter" role="group" aria-label="Statüye göre filtrele">
          <button type="button" className={`st-chip${status === "all" ? " is-on" : ""}`} onClick={() => setStatus("all")}>
            Tümü <span className="st-count">{reports.length}</span>
          </button>
          {STATUSES.map((s) => (
            <button key={s.id} type="button" title={s.hint} className={`st-chip${status === s.id ? " is-on" : ""}`}
              style={{ ["--st" as string]: s.color }} onClick={() => setStatus(s.id)}>
              <i className="st-dot" /> {s.label} <span className="st-count">{counts[s.id]}</span>
            </button>
          ))}
        </div>
        <div className="home-tools">
          <label className="home-search">
            <Ico d={PATHS.search} />
            <input type="search" placeholder="Rapor ara… (ad, amaç, domain, KPI)" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Rapor ara" />
            {q ? <button type="button" className="home-search-x" onClick={() => setQ("")} aria-label="Aramayı temizle">×</button> : null}
          </label>
          {view === "cards" ? (   // liste görünümünde sıralama tablo başlıklarından
            <select className="home-sort" value={sort} onChange={(e) => setSort(e.target.value as Sort)} aria-label="Sırala">
              <option value="updated">Son güncellenen</option>
              <option value="title">Ada göre</option>
            </select>
          ) : null}
          <div className="view-seg" role="group" aria-label="Görünüm">
            <button type="button" className={view === "cards" ? "is-on" : undefined} onClick={() => setView("cards")} title="Kart görünümü" aria-pressed={view === "cards"}><Ico d={PATHS.cards} /></button>
            <button type="button" className={view === "list" ? "is-on" : undefined} onClick={() => setView("list")} title="Liste görünümü" aria-pressed={view === "list"}><Ico d={PATHS.list} /></button>
          </div>
          <button type="button" className="btn btn-primary home-new" onClick={onNew} title="Yeni rapor tasarla">
            <Ico d={PATHS.plus} /><span>Yeni rapor</span>
          </button>
        </div>
      </div>

      {loading && !reports.length ? <div className="panel-empty"><span className="spinner" /><p>Raporlar yükleniyor…</p></div> : null}
      {!loading && !reports.length ? (
        <div className="panel-empty">
          <h3>Henüz rapor yok</h3>
          <p>İlk raporunuzu oluşturmak için “Yeni rapor”a tıklayın; agent ihtiyacınızı sorup veriyi hazırlayacak.</p>
          <button type="button" className="btn btn-primary" onClick={onNew}>Yeni rapor</button>
        </div>
      ) : null}

      {view === "cards" ? (
        <div className="home-grid">
          {list.map((r) => <ReportCard key={r.id} r={r} {...actions} />)}
        </div>
      ) : list.length ? (
        <ReportTable reports={list} renderRow={(r, cols) => <ReportRow r={r} cols={cols} {...actions} />} />
      ) : null}
      {reports.length && !list.length ? <p className="muted">Aramaya uyan rapor yok.</p> : null}
    </div>
  );
}

/** Kart ve satırın ortak durumu: yeniden adlandırma ve silme onayı. */
function useReportItem(r: SessionSummary, a: Actions) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(r.title);
  const [saving, setSaving] = useState(false);
  const [askDelete, setAskDelete] = useState(false);
  const cancel = () => { setEditing(false); setDraft(r.title); };
  const save = async () => {
    const t = draft.trim();
    if (!t || t === r.title) { cancel(); return; }
    setSaving(true);
    const ok = await a.onRename(r.id, t);
    setSaving(false);
    if (ok) setEditing(false);
  };
  const renameForm = editing ? (
    <form className="rc-rename" onClick={(e) => e.stopPropagation()} onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <input autoFocus className="dict-input" value={draft} maxLength={120} onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Escape") cancel(); }} aria-label="Yeni rapor adı" />
      <button type="submit" className="btn btn-primary btn-sm" disabled={saving}>Kaydet</button>
      <button type="button" className="btn btn-ghost btn-sm" onClick={cancel}>Vazgeç</button>
    </form>
  ) : null;
  const statusSelect = (align: "left" | "right") => <StatusPicker value={r.status} onChange={(st) => a.onStatus(r.id, st)} align={align} size="sm" />;
  const buttons = (compact: boolean): ReactNode => (
    <>
      <button type="button" className="btn btn-secondary btn-sm" onClick={() => a.onOpen(r.id)} title="Raporu aç"><Ico d={PATHS.open} />{compact ? null : "Aç"}</button>
      <button type="button" className="btn btn-secondary btn-sm" onClick={() => { setDraft(r.title); setEditing(true); }} title="Yeniden adlandır">
        <Ico d={PATHS.rename} />{compact ? null : "Adlandır"}</button>
      {r.has_spec ? <a className="btn btn-secondary btn-sm" href={a.exportUrl(r.id)} target="_blank" rel="noopener" title="HTML olarak indir">
        <Ico d={PATHS.download} />{compact ? null : "HTML"}</a> : null}
      <button type="button" className="btn btn-secondary btn-sm rc-del" onClick={() => setAskDelete(true)} title="Raporu sil"><Ico d={PATHS.del} />{compact ? null : "Sil"}</button>
    </>
  );
  const dialog = askDelete ? (
    <ConfirmDialog danger title="Rapor silinsin mi?" confirmLabel="Evet, sil"
      message={`"${r.title || "Başlıksız"}" raporu ve tüm konuşma geçmişi kalıcı olarak silinecek. Bu işlem geri alınamaz. Emin misiniz?`}
      onCancel={() => setAskDelete(false)} onConfirm={() => { setAskDelete(false); a.onDelete(r.id); }} />
  ) : null;
  return { editing, renameForm, statusSelect, buttons, dialog };
}

const Domains = ({ r }: { r: SessionSummary }) =>
  r.domains?.length ? <span className="rc-domains">{r.domains.map((d) => <span key={d} className="rc-domain">{d}</span>)}</span>
    : <span className="muted">—</span>;

function ReportCard({ r, ...a }: Actions & { r: SessionSummary }) {
  const it = useReportItem(r, a);
  const st = statusInfo(r.status);
  const kpis = r.kpi_titles?.length ? r.kpi_titles : r.kpis ?? [];
  return (
    <article className="rc" onClick={() => !it.editing && a.onOpen(r.id)} tabIndex={0}
      onKeyDown={(e) => { if (e.key === "Enter" && !it.editing && e.target === e.currentTarget) a.onOpen(r.id); }} aria-label={`${r.title} raporunu aç`}>
      <div className="rc-strip" aria-hidden="true" style={{ background: st.color }} />
      <div className="rc-body">
        <div className="rc-top">
          {it.renameForm ?? <h2 className="rc-title" title={r.title}>{r.title || "Başlıksız"}</h2>}
          {it.statusSelect("right")}
        </div>
        {r.subtitle || r.business_goal ? <p className="rc-desc">{r.business_goal || r.subtitle}</p> : <p className="rc-desc muted">Henüz ihtiyaç tanımlanmadı.</p>}

        <dl className="rc-facts">
          <dt>Domain</dt><dd><Domains r={r} /></dd>
          {r.audience ? <><dt>Hedef kitle</dt><dd>{r.audience}</dd></> : null}
          {r.time_range ? <><dt>Dönem</dt><dd>{r.time_range}</dd></> : null}
          {r.dimensions?.length ? <><dt>Kırılımlar</dt><dd>{r.dimensions.join(", ")}</dd></> : null}
          {r.filters?.length ? <><dt>Filtreler</dt><dd>{r.filters.join(", ")}</dd></> : null}
        </dl>

        {kpis.length ? (
          <div className="rc-chips" aria-label="KPI'lar">
            {kpis.slice(0, 4).map((k) => <span key={k} className="rc-chip">{k}</span>)}
            {kpis.length > 4 ? <span className="rc-chip muted">+{kpis.length - 4}</span> : null}
          </div>
        ) : null}

        <div className="rc-meta muted small">
          {r.published?.status === "active" ? <span className="pill pill-ok" title="Vitrin'de yayında olan sürüm">Vitrin'de · v{r.published.version}</span> : null}
          {r.visual_count ? <span title={r.visual_types?.map((t) => VISUAL_LABEL[t] ?? t).join(", ")}>{r.visual_count} görsel</span> : null}
          {r.dataset_count ? <span>{r.dataset_count} veri kümesi</span> : null}
          {r.views?.length ? <span title={r.views.join(", ")}>{r.views.length} onaylı view</span> : null}
          <span>{relTime(r.updatedAt)}</span>
        </div>
      </div>
      <div className="rc-actions" onClick={(e) => e.stopPropagation()}>{it.buttons(false)}</div>
      {it.dialog}
    </article>
  );
}

function ReportRow({ r, cols, ...a }: Actions & { r: SessionSummary; cols: ColId[] }) {
  const it = useReportItem(r, a);
  const cell = (c: ColId): ReactNode => {
    switch (c) {
      case "status": return it.statusSelect("left");
      case "title": return it.renameForm ?? (
        <>
          <div className="rl-name" title={r.title}>{r.title || "Başlıksız"}</div>
          <div className="rl-desc muted">{r.business_goal || r.subtitle || "Henüz ihtiyaç tanımlanmadı."}</div>
        </>
      );
      case "phase": return PHASE_LABEL[r.phase] ?? r.phase;
      case "domains": return <Domains r={r} />;
      case "audience": return r.audience || <span className="muted">—</span>;
      case "owner": return r.owner_name || r.owner || <span className="muted">—</span>;
      case "vitrin": return r.published?.status === "active"
        ? <span className="pill pill-ok" title="Vitrin'de yayında olan sürüm">v{r.published.version}</span> : <span className="muted">—</span>;
      case "visuals": return <span title={r.visual_types?.map((t) => VISUAL_LABEL[t] ?? t).join(", ")}>{r.visual_count ?? 0}</span>;
      case "datasets": return r.dataset_count ?? 0;
      case "kpis": return (r.kpi_titles?.length ? r.kpi_titles : r.kpis ?? []).join(", ") || <span className="muted">—</span>;
      case "created": return <span className="muted">{relTime(r.createdAt)}</span>;
      case "updated": return <span className="muted">{relTime(r.updatedAt)}</span>;
    }
  };
  const CLS: Partial<Record<ColId, string>> = { status: "rl-status", title: "rl-title", audience: "rl-aud", kpis: "rl-kpis",
    visuals: "num", datasets: "num", created: "rl-time", updated: "rl-time" };
  return (
    <tr className="rl-row" onClick={() => !it.editing && a.onOpen(r.id)} style={{ ["--st" as string]: statusInfo(r.status).color }}>
      {cols.map((c) => <td key={c} className={CLS[c]}>{cell(c)}</td>)}
      <td className="rl-actions" onClick={(e) => e.stopPropagation()}>
        <div>{it.buttons(true)}</div>
        {it.dialog}
      </td>
    </tr>
  );
}
