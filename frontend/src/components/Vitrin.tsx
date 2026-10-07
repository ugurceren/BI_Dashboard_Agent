// Vitrin: kullanıcının görebildiği yayınlanmış raporlar (kendisine / grubuna paylaşılanlar, kendi yayınları; admin hepsi).
import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import type { Api } from "../api/client";
import type { Grant, Me, VitrinCard } from "../types";
import { ConfirmDialog } from "./ConfirmDialog";
import { ShareEditor } from "./ShareEditor";
import { applyFilter, SCOPE_LABEL, type VitrinFilter } from "../lib/vitrin";
import "./home.css";
import "./vitrin.css";

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

function relTime(iso?: string): string {
  const t = Date.parse(iso ?? "");
  if (!Number.isFinite(t)) return "";
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 3600) return s < 60 ? "az önce" : `${Math.floor(s / 60)} dk önce`;
  if (s < 86400) return `${Math.floor(s / 3600)} sa önce`;
  return new Date(t).toLocaleDateString("tr-TR", { day: "numeric", month: "short", year: "numeric" });
}

export function Vitrin({ api, me, items, error, filter, reload, onOpen, onOpenDesign }: {
  api: Api;
  me: Me | null;
  items: VitrinCard[] | null;
  error: string | null;
  filter: VitrinFilter;
  reload: () => void;
  onOpen: (id: string) => void;
  onOpenDesign: (sessionId: string) => void;
}) {
  const [q, setQ] = useState("");
  const [sharing, setSharing] = useState<VitrinCard | null>(null);
  const [retiring, setRetiring] = useState<VitrinCard | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const active = useMemo(() => (items ?? []).filter((r) => r.status === "active"), [items]);
  const list = useMemo(() => {
    const ql = q.trim().toLocaleLowerCase("tr");
    return applyFilter(items ?? [], filter)
      .filter((r) => !ql || [r.title, r.description, r.owner_name, ...r.domains].join(" ").toLocaleLowerCase("tr").includes(ql));
  }, [items, q, filter]);

  const canDesign = me?.capabilities?.design ?? true;
  const heading = filter.domain ?? SCOPE_LABEL[filter.scope];
  return (
    <div className="home vitrin">
      <div className="vt-hero">
        <div>
          <h1 className="home-title">{heading}</h1>
          <p className="muted home-sub">
            {filter.scope === "recent" ? "Bu tarayıcıda en son açtığınız raporlar."
              : canDesign ? "Yayınlanmış raporlar: size paylaşılanlar ve kendi yayınlarınız." : "Size açılan raporlar. Veriler kendi veri yetkinizle gösterilir."}
          </p>
        </div>
        <label className="home-search">
          <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true"><path d="M7 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10ZM10.7 10.7 14 14" /></svg>
          <input type="search" placeholder="Rapor ara… (ad, açıklama, alan, sahip)" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Vitrin'de ara" />
        </label>
      </div>

      {error ? <div className="banner-error">Vitrin alınamadı: {error}</div> : null}
      {actionError ? <div className="banner-error">{actionError}</div> : null}
      {items === null ? <div className="panel-empty"><span className="spinner" /><p>Vitrin yükleniyor…</p></div> : null}
      {items && !active.length && filter.scope !== "retired" ? (
        <div className="panel-empty">
          <h3>Vitrin'de henüz rapor yok</h3>
          <p>{canDesign ? "Bir raporu tasarladıktan sonra üst çubuktaki “Yayınla” ile Vitrin'e ekleyip paylaşabilirsiniz."
            : "Size paylaşılan bir rapor olduğunda burada görünecek. Erişim için rapor sahibine ya da yöneticinize başvurun."}</p>
        </div>
      ) : null}

      <div className="home-grid">
        {list.map((r) => (
          <article key={r.id} className="rc vt-card" tabIndex={0} onClick={() => onOpen(r.id)} aria-label={`${r.title} raporunu aç`}
            onKeyDown={(e) => { if (e.key === "Enter" && e.target === e.currentTarget) onOpen(r.id); }}>
            <div className="rc-strip" aria-hidden="true" />
            <div className="rc-body">
              <div className="rc-top">
                <h2 className="rc-title" title={r.title}>{r.title}</h2>
                <span className="pill pill-muted" title="Yayın sürümü">v{r.version}</span>
              </div>
              <p className={`rc-desc${r.description ? "" : " muted"}`}>{r.description || "Açıklama eklenmemiş."}</p>
              {r.domains.length ? <span className="rc-domains">{r.domains.map((d) => <span key={d} className="rc-domain">{d}</span>)}</span> : null}
              <div className="rc-meta muted small">
                <span title={r.owner}>{r.mine ? "Sizin yayınınız" : r.owner_name}</span>
                <span>{relTime(r.updated_at)}</span>
                {r.shared_with !== null ? <span>{r.shared_with ? `${r.shared_with} paylaşım` : "paylaşılmadı"}</span> : null}
              </div>
            </div>
            <div className="rc-actions" onClick={(e) => e.stopPropagation()}>
              <button type="button" className="btn btn-primary btn-sm" onClick={() => onOpen(r.id)}>Aç</button>
              {r.can_export && r.status === "active" ? <a className="btn btn-secondary btn-sm" href={api.vitrinExportUrl(r.id)} target="_blank" rel="noopener">HTML</a> : null}
              {r.session_id ? <button type="button" className="btn btn-secondary btn-sm" onClick={() => onOpenDesign(r.session_id!)} title="Bu raporu Tasarım modunda aç">Tasarımda aç</button> : null}
              {r.can_manage ? <button type="button" className="btn btn-secondary btn-sm" onClick={() => setSharing(r)}>Paylaş</button> : null}
              {r.can_manage && r.status === "active" ? (
                <button type="button" className="btn btn-secondary btn-sm rc-del" onClick={() => setRetiring(r)}>Kaldır</button>
              ) : null}
            </div>
          </article>
        ))}
      </div>
      {items && items.length && !list.length ? <p className="muted">Bu filtreye uyan rapor yok.</p> : null}

      {sharing ? <ShareDialog api={api} report={sharing} onClose={() => { setSharing(null); reload(); }} /> : null}
      {retiring ? (
        <ConfirmDialog danger title="Yayından kaldırılsın mı?" confirmLabel="Evet, kaldır"
          message={`"${retiring.title}" Vitrin'den kalkar; paylaşılan kişiler artık göremez. Tasarım ve sürüm geçmişi korunur, yeniden yayınlayabilirsiniz.`}
          onCancel={() => setRetiring(null)}
          onConfirm={() => { const id = retiring.id; setRetiring(null); api.retireReport(id).then(reload).catch((e) => setActionError(errMsg(e))); }} />
      ) : null}
    </div>
  );
}

function ShareDialog({ api, report, onClose }: { api: Api; report: VitrinCard; onClose: () => void }) {
  const [grants, setGrants] = useState<Grant[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.getGrants(report.id).then(setGrants).catch((e) => setError(errMsg(e)));
  }, [api, report.id]);
  const save = async () => {
    if (!grants) return;
    setBusy(true);
    try {
      await api.setGrants(report.id, grants);
      onClose();
    } catch (e) {
      setError(errMsg(e));
      setBusy(false);
    }
  };
  return createPortal(
    <div className="cd-backdrop" onClick={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div className="pd-box" role="dialog" aria-modal="true" aria-labelledby="sd-title">
        <div className="pd-head"><h2 id="sd-title">Paylaş: {report.title}</h2></div>
        {grants ? <ShareEditor grants={grants} onChange={setGrants} disabled={busy} /> : !error ? <div className="panel-empty"><span className="spinner" /></div> : null}
        {error ? <div className="banner-error">{error}</div> : null}
        <div className="pd-actions">
          <span className="pd-spacer" />
          <button type="button" className="btn btn-secondary" disabled={busy} onClick={onClose}>Vazgeç</button>
          <button type="button" className="btn btn-primary" disabled={busy || !grants} onClick={() => void save()}>Kaydet</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
