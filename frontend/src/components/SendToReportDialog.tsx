// Sorgu Çalıştır → "Dashboard hazırla": sorgu yeni bir rapora ya da mevcut bir rapora sorgu modu taslağı olarak gönderilir.
// İhtiyaç / Veri fazındaki rapor sorgu modunda açılır (kullanıcı önizleyip "Dashboard'a geç" der); tasarım fazındaki
// rapora ise doğrudan veri kümesi olarak eklenir.
import { useEffect, useMemo, useState } from "react";
import type { Api } from "../api/client";
import type { QueryDraft, SessionSummary } from "../types";
import { ApiError } from "../api/client";
import { slugifyId } from "../lib/slug";
import "./querymode.css";

const PHASE_TR: Record<string, string> = { requirements: "İhtiyaç", data: "Veri", design: "Tasarım" };

export function SendToReportDialog({ api, sql, onClose, onDone }: {
  api: Api;
  sql: string;
  onClose: () => void;
  /** rapor hazır: tasarım sayfasında açılır */
  onDone: (sessionId: string) => void;
}) {
  const [target, setTarget] = useState<"new" | "existing">("new");
  const [reports, setReports] = useState<SessionSummary[] | null>(null);
  const [reportId, setReportId] = useState("");
  const [title, setTitle] = useState("");
  const [name, setName] = useState("sorgu_1");
  const [nameTouched, setNameTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);

  useEffect(() => {
    let alive = true;
    api.listSessions().then((r) => { if (alive) { setReports(r); if (r[0]) setReportId(r[0].id); } }).catch(() => alive && setReports([]));
    return () => { alive = false; };
  }, [api]);
  useEffect(() => { if (!nameTouched && title.trim()) setName(slugifyId(title)); }, [title, nameTouched]);

  const chosen = useMemo(() => reports?.find((r) => r.id === reportId) ?? null, [reports, reportId]);
  const id = slugifyId(name);
  const canSend = !!id && !busy && (target === "new" || !!chosen);

  const send = async () => {
    setBusy(true);
    setErrors([]);
    const draft: QueryDraft = { id, title: title.trim(), sql };
    try {
      if (target === "new") {
        const s = await api.createSession();
        await api.saveQueryDrafts(s.id, { mode: "query", drafts: [draft] });
        onDone(s.id);
        return;
      }
      const s = await api.getSession(reportId);
      if (s.phase === "design") {
        await api.datasetsFromQuery(s.id, [draft]);
      } else {
        const drafts = [...(s.query_drafts ?? []).filter((d) => d.id !== id), draft];
        await api.saveQueryDrafts(s.id, { mode: "query", drafts });
      }
      onDone(s.id);
    } catch (e) {
      const detail = e instanceof ApiError && Array.isArray(e.detail) ? (e.detail as string[]) : null;
      setErrors(detail ?? [e instanceof Error ? e.message : String(e)]);
      setBusy(false);
    }
  };

  return (
    <div className="cd-backdrop" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div className="sd-box" role="dialog" aria-modal="true" aria-labelledby="send-title">
        <h3 id="send-title">Bu sorguyla dashboard hazırla</h3>
        <p className="muted small">Sorgu sonucu raporun veri kümesi olur; tasarımı sohbetle yaparsınız.</p>
        <label className="sd-field">
          <span>Açıklama</span>
          <input className="vt-input" type="text" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="ör. Şube bazlı teminat" autoFocus />
        </label>
        <label className="sd-field">
          <span>Veri kümesi adı</span>
          <input className="vt-input" type="text" value={name} onChange={(e) => { setName(e.target.value); setNameTouched(true); }} aria-describedby="send-id-hint" />
          <small id="send-id-hint" className="muted">{id && id !== name ? <>Kaydedilecek ad: <code>{id}</code></> : "Küçük harf, rakam ve alt çizgi."}</small>
        </label>
        <fieldset className="sd-target">
          <legend>Nereye</legend>
          <label><input type="radio" name="send-target" checked={target === "new"} onChange={() => setTarget("new")} /> Yeni rapor</label>
          <label>
            <input type="radio" name="send-target" checked={target === "existing"} onChange={() => setTarget("existing")} disabled={!reports?.length} /> Mevcut rapor
          </label>
          {target === "existing" ? (
            <select className="vt-input" value={reportId} onChange={(e) => setReportId(e.target.value)} aria-label="Rapor">
              {(reports ?? []).map((r) => <option key={r.id} value={r.id}>{r.title} · {PHASE_TR[r.phase] ?? r.phase}</option>)}
            </select>
          ) : null}
          {target === "existing" && chosen ? (
            <small className="muted">
              {chosen.phase === "design"
                ? "Rapor tasarım fazında: sorgu doğrudan veri kümesi olarak eklenir (aynı adlı varsa güncellenir)."
                : "Rapor sorgu modunda açılır; önizleyip \"Dashboard'a geç\" ile tasarıma geçersiniz."}
            </small>
          ) : null}
        </fieldset>
        {errors.length ? <div className="qp-msg is-bad" role="alert"><b>Gönderilemedi</b><ul>{errors.map((e, i) => <li key={i}>{e}</li>)}</ul></div> : null}
        <div className="sd-actions">
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={busy}>Vazgeç</button>
          <button type="button" className="btn btn-primary" onClick={() => void send()} disabled={!canSend}>
            {busy ? <span className="spinner" /> : null}Gönder ve aç
          </button>
        </div>
      </div>
    </div>
  );
}
