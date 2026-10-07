// Vitrin'de yayınlanmış rapor: salt-okunur dashboard (SQL ve sohbet yok). Veri, açan kullanıcının veri yetkisiyle gelir;
// filtreler ve görselden çapraz filtre tasarım ekranındakiyle aynı çalışır.
import { useCallback, useEffect, useMemo, useState } from "react";
import type { Api } from "../api/client";
import type { CellValue, CrossSelection, DashboardData, Selection, VitrinReport } from "../types";
import { DashboardRenderer } from "../dashboard/DashboardRenderer";
import "./vitrin.css";

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function ReportView({ api, id, onBack }: { api: Api; id: string; onBack: () => void }) {
  const [rep, setRep] = useState<VitrinReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(false);
  const [dataError, setDataError] = useState<string | null>(null);
  const [selections, setSelections] = useState<Record<string, CellValue[]>>({});
  const [cross, setCross] = useState<CrossSelection | null>(null);

  useEffect(() => {
    let alive = true;
    setRep(null); setError(null); setData(null); setSelections({}); setCross(null);
    api.vitrinReport(id).then((r) => alive && setRep(r)).catch((e) => alive && setError(errMsg(e)));
    return () => { alive = false; };
  }, [api, id]);

  const fi = rep?.filters ?? null;
  const selectionList = useMemo<Selection[]>(() => {
    const out: Selection[] = [];
    for (const f of fi?.filters ?? []) {
      const vals = selections[f.id];
      if (f.key && vals?.length) out.push({ key: f.key, values: vals });
    }
    const dd = fi?.data_date;
    if (dd && selections[dd.key]?.length) out.push({ key: dd.key, values: selections[dd.key] });
    if (cross) out.push({ key: cross.key, values: [cross.value], exclude: [cross.datasetId] });
    return out;
  }, [fi, selections, cross]);
  const selectionKey = JSON.stringify(selectionList);

  const load = useCallback(async (sel: Selection[]) => {
    setLoading(true);
    try {
      const d = await api.vitrinData(id, sel);
      setData(d && d.datasets ? d : { datasets: {} });
      setDataError(null);
    } catch (e) {
      setDataError(errMsg(e));
      setData((cur) => cur ?? { datasets: {} });
    } finally {
      setLoading(false);
    }
  }, [api, id]);
  useEffect(() => {
    if (rep) void load(selectionList);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rep, selectionKey]);

  if (error) {
    return (
      <div className="panel-empty">
        <h3>Rapor açılamadı</h3>
        <p>{error}</p>
        <button type="button" className="btn btn-primary" onClick={onBack}>Vitrin'e dön</button>
      </div>
    );
  }
  if (!rep) return <div className="panel-empty"><span className="spinner" /><p>Rapor yükleniyor…</p></div>;
  const r = rep.report;
  return (
    <div className="viewer-page rv">
      <div className="rv-bar">
        <button type="button" className="btn btn-ghost btn-sm" onClick={onBack}>← Vitrin</button>
        <div className="rv-meta muted small">
          <span title={r.owner}>{r.owner_name}</span>
          <span title={rep.version.notes ?? undefined}>sürüm {rep.version.version} · {new Date(rep.version.published_at).toLocaleDateString("tr-TR", { day: "numeric", month: "short", year: "numeric" })}</span>
          {r.status === "retired" ? <span className="pill pill-warn">Yayından kaldırıldı</span> : null}
        </div>
        <span className="pd-spacer" />
        {r.can_export ? <a className="btn btn-secondary btn-sm" href={api.vitrinExportUrl(r.id)} target="_blank" rel="noopener">HTML indir</a> : null}
      </div>
      {dataError ? <div className="banner-error">Veri alınamadı: {dataError}</div> : null}
      {data ? (
        <DashboardRenderer spec={rep.spec} data={data} loading={loading} model={fi ? {
          filters: fi.filters, bindings: fi.bindings, applied: data.applied ?? {},
          selections, onSelections: setSelections, cross, onCross: setCross, dataDate: fi.data_date,
        } : undefined} />
      ) : <div className="panel-empty"><span className="spinner" /><p>Veri yükleniyor…</p></div>}
    </div>
  );
}
