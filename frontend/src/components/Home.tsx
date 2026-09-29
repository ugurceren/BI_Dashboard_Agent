// Rapor envanteri: her rapor bir kart (amaç, kapsam, içerik); yeniden adlandır / aç / indir / sil.
import { useMemo, useState } from "react";
import type { SessionSummary } from "../types";
import { PHASES } from "./Chat";
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

const VISUAL_LABEL: Record<string, string> = {
  kpi: "KPI", line: "Çizgi", area: "Alan", bar: "Çubuk", pie: "Pasta", donut: "Halka", table: "Tablo", scatter: "Dağılım",
  heatmap: "Isı haritası", funnel: "Huni", gauge: "Gösterge", treemap: "Ağaç harita", combo: "Kombine", text: "Metin",
};

type Sort = "updated" | "title";

export function Home({ reports, loading, onOpen, onNew, onRename, onDelete, exportUrl }: {
  reports: SessionSummary[];
  loading: boolean;
  onOpen: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => Promise<boolean>;
  onDelete: (id: string) => void;
  exportUrl: (id: string) => string;
}) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("updated");
  const list = useMemo(() => {
    const ql = q.trim().toLocaleLowerCase("tr");
    const f = reports.filter((r) => !ql || JSON.stringify([r.title, r.subtitle, r.business_goal, r.kpis, r.dimensions, r.audience])
      .toLocaleLowerCase("tr").includes(ql));
    return [...f].sort((a, b) => (sort === "title" ? (a.title || "").localeCompare(b.title || "", "tr")
      : (b.updatedAt ?? "").localeCompare(a.updatedAt ?? "")));
  }, [reports, q, sort]);

  return (
    <div className="home">
      <div className="home-head">
        <div>
          <h1 className="home-title">Rapor envanteri</h1>
          <p className="home-sub muted">{reports.length} rapor · Açmak için bir karta tıklayın, yeni rapor için sağ üstteki butonu kullanın.</p>
        </div>
        <div className="home-tools">
          <input className="dict-input home-search" placeholder="Rapor ara… (ad, amaç, KPI)" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Rapor ara" />
          <select className="home-sort" value={sort} onChange={(e) => setSort(e.target.value as Sort)} aria-label="Sırala">
            <option value="updated">Son güncellenen</option>
            <option value="title">Ada göre</option>
          </select>
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

      <div className="home-grid">
        {list.map((r) => <ReportCard key={r.id} r={r} onOpen={onOpen} onRename={onRename} onDelete={onDelete} exportUrl={exportUrl} />)}
      </div>
      {reports.length && !list.length ? <p className="muted">Aramaya uyan rapor yok.</p> : null}
    </div>
  );
}

function ReportCard({ r, onOpen, onRename, onDelete, exportUrl }: {
  r: SessionSummary;
  onOpen: (id: string) => void;
  onRename: (id: string, title: string) => Promise<boolean>;
  onDelete: (id: string) => void;
  exportUrl: (id: string) => string;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(r.title);
  const [saving, setSaving] = useState(false);
  const palette = r.theme?.palette?.length ? r.theme.palette : ["#94a3b8", "#cbd5e1", "#e2e8f0"];
  const phase = PHASES.find((p) => p.id === r.phase)?.label ?? r.phase;
  const kpis = r.kpi_titles?.length ? r.kpi_titles : r.kpis ?? [];

  const save = async () => {
    const t = draft.trim();
    if (!t || t === r.title) { setEditing(false); setDraft(r.title); return; }
    setSaving(true);
    const ok = await onRename(r.id, t);
    setSaving(false);
    if (ok) setEditing(false);
  };

  return (
    <article className="rc" onClick={() => !editing && onOpen(r.id)} tabIndex={0}
      onKeyDown={(e) => { if (e.key === "Enter" && !editing) onOpen(r.id); }} aria-label={`${r.title} raporunu aç`}>
      <div className="rc-strip" aria-hidden="true">
        {palette.map((c, i) => <span key={i} style={{ background: c }} />)}
      </div>
      <div className="rc-body">
        <div className="rc-top">
          {editing ? (
            <form className="rc-rename" onClick={(e) => e.stopPropagation()} onSubmit={(e) => { e.preventDefault(); void save(); }}>
              <input autoFocus className="dict-input" value={draft} maxLength={120} onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Escape") { setEditing(false); setDraft(r.title); } }} aria-label="Yeni rapor adı" />
              <button type="submit" className="btn btn-primary btn-sm" disabled={saving}>Kaydet</button>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setEditing(false); setDraft(r.title); }}>Vazgeç</button>
            </form>
          ) : (
            <h2 className="rc-title" title={r.title}>{r.title || "Başlıksız"}</h2>
          )}
          <span className={`pill rc-phase ph-${r.phase}`}>{r.has_spec ? "Dashboard hazır" : phase}</span>
        </div>
        {r.subtitle || r.business_goal ? <p className="rc-desc">{r.business_goal || r.subtitle}</p> : <p className="rc-desc muted">Henüz ihtiyaç tanımlanmadı.</p>}

        <dl className="rc-facts">
          {r.audience ? <><dt>Hedef kitle</dt><dd>{r.audience}</dd></> : null}
          {r.time_range ? <><dt>Dönem</dt><dd>{r.time_range}</dd></> : null}
          {r.dimensions?.length ? <><dt>Kırılımlar</dt><dd>{r.dimensions.join(", ")}</dd></> : null}
          {r.filters?.length ? <><dt>Filtreler</dt><dd>{r.filters.join(", ")}</dd></> : null}
        </dl>

        {kpis.length ? (
          <div className="rc-chips" aria-label="KPI'lar">
            {kpis.slice(0, 5).map((k) => <span key={k} className="rc-chip">{k}</span>)}
            {kpis.length > 5 ? <span className="rc-chip muted">+{kpis.length - 5}</span> : null}
          </div>
        ) : null}

        <div className="rc-meta muted small">
          {r.visual_count ? <span>{r.visual_count} görsel{r.visual_types?.length ? ` (${r.visual_types.map((t) => VISUAL_LABEL[t] ?? t).join(", ")})` : ""}</span> : null}
          {r.dataset_count ? <span>{r.dataset_count} veri kümesi</span> : null}
          {r.views?.length ? <span title={r.views.join(", ")}>{r.views.length} onaylı view</span> : null}
          <span>Güncellendi: {relTime(r.updatedAt)}</span>
        </div>
      </div>
      <div className="rc-actions" onClick={(e) => e.stopPropagation()}>
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => onOpen(r.id)}>Aç</button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setDraft(r.title); setEditing(true); }}>Yeniden adlandır</button>
        {r.has_spec ? <a className="btn btn-ghost btn-sm" href={exportUrl(r.id)} target="_blank" rel="noopener">HTML indir</a> : null}
        <button type="button" className="btn btn-ghost btn-sm rc-del"
          onClick={() => { if (window.confirm(`"${r.title || "Başlıksız"}" raporu silinsin mi? Bu işlem geri alınamaz.`)) onDelete(r.id); }}>Sil</button>
      </div>
    </article>
  );
}
