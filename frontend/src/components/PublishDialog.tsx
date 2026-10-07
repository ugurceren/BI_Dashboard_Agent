// Vitrin'e yayınla: tasarımın o anki hali yeni sürüm olur (değişmez kopya); açıklama, sürüm notu ve paylaşım.
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import type { Api } from "../api/client";
import type { Grant, Publication, SessionState } from "../types";
import { ShareEditor } from "./ShareEditor";
import "./vitrin.css";

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));
const fmt = (iso?: string) => (iso ? new Date(iso).toLocaleString("tr-TR", { dateStyle: "medium", timeStyle: "short" }) : "");

export function PublishDialog({ api, sessionId, onClose, onPublished }: {
  api: Api;
  sessionId: string;
  onClose: () => void;
  onPublished: (s: SessionState, reportId: string) => void;
}) {
  const [pub, setPub] = useState<Publication | null>(null);
  const [description, setDescription] = useState("");
  const [notes, setNotes] = useState("");
  const [grants, setGrants] = useState<Grant[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.publication(sessionId).then((p) => {
      setPub(p);
      setDescription(p.report?.description ?? "");
      setGrants(p.grants ?? []);
    }).catch((e) => setError(errMsg(e)));
  }, [api, sessionId]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && !busy && onClose();
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  }, [busy, onClose]);

  const publish = async () => {
    setBusy(true); setError(null);
    try {
      const r = await api.publish(sessionId, { description: description.trim() || null, notes: notes.trim() || null, grants });
      onPublished(r.session, r.report.id);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };
  const retire = async () => {
    if (!pub?.report) return;
    setBusy(true); setError(null);
    try {
      await api.retireReport(pub.report.id);
      setPub(await api.publication(sessionId));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const active = pub?.published && pub.report?.status === "active";
  const next = (pub?.report?.version ?? 0) + 1;
  return createPortal(
    <div className="cd-backdrop" onClick={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div className="pd-box" role="dialog" aria-modal="true" aria-labelledby="pd-title">
        <div className="pd-head">
          <h2 id="pd-title">Vitrin'e yayınla</h2>
          {pub?.published ? (
            <span className={`pill ${active ? "pill-ok" : "pill-muted"}`}>
              {active ? `Yayında · sürüm ${pub.report?.version}` : `Yayından kaldırıldı · sürüm ${pub.report?.version}`}
            </span>
          ) : <span className="pill pill-muted">Henüz yayınlanmadı</span>}
        </div>
        {!pub && !error ? <div className="panel-empty"><span className="spinner" /></div> : null}
        {pub ? (
          <>
            <p className="muted small pd-lead">
              Tasarımın şu anki hali <b>sürüm {next}</b> olarak yayınlanır. İzleyiciler bu sürümü görür; tasarımda
              sonradan yaptığınız değişiklikler yeniden yayınlayana kadar Vitrin'e yansımaz. Veri, her izleyicinin kendi
              veri yetkisiyle getirilir.
            </p>
            {pub.unpublished_changes ? <div className="pd-note">Yayında olmayan değişiklikler var.</div> : null}
            <label className="pd-field">
              <span>Açıklama (Vitrin kartında görünür)</span>
              <textarea className="vt-input" rows={2} maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)}
                placeholder="Rapor neyi gösteriyor, kimin için?" />
            </label>
            <label className="pd-field">
              <span>Sürüm notu</span>
              <input className="vt-input" maxLength={2000} value={notes} onChange={(e) => setNotes(e.target.value)}
                placeholder={pub.published ? "Bu sürümde ne değişti?" : "İlk sürüm"} />
            </label>
            <div className="pd-field">
              <span>Kimler görebilir?</span>
              <ShareEditor grants={grants} onChange={setGrants} disabled={busy} />
            </div>
            {pub.versions?.length ? (
              <details className="pd-versions">
                <summary>Sürüm geçmişi ({pub.versions.length})</summary>
                <ul>
                  {pub.versions.map((v) => (
                    <li key={v.version}><b>v{v.version}</b> · {fmt(v.published_at)} · {v.published_by}{v.notes ? ` — ${v.notes}` : ""}</li>
                  ))}
                </ul>
              </details>
            ) : null}
          </>
        ) : null}
        {error ? <div className="banner-error">{error}</div> : null}
        <div className="pd-actions">
          {active ? <button type="button" className="btn btn-ghost rc-del" disabled={busy} onClick={() => void retire()}>Yayından kaldır</button> : <span />}
          <span className="pd-spacer" />
          <button type="button" className="btn btn-secondary" disabled={busy} onClick={onClose}>Vazgeç</button>
          <button type="button" className="btn btn-primary" disabled={busy || !pub} onClick={() => void publish()}>
            {busy ? "Yayınlanıyor…" : pub?.published ? `Sürüm ${next} olarak yayınla` : "Yayınla"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
