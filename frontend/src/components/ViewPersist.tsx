// Dataset → onaylı view: script üret (incelemek/çalıştırmak için), view oluşturulunca dataset'i ona bağla.
import { useState } from "react";
import type { Dataset, SessionState } from "../types";
import { ApiError, type Api } from "../api/client";

function defaultName(id: string) {
  const tr: Record<string, string> = { ç: "c", ğ: "g", ı: "i", ö: "o", ş: "s", ü: "u", Ç: "C", Ğ: "G", İ: "I", Ö: "O", Ş: "S", Ü: "U" };
  return `v_${id.replace(/[çğıöşüÇĞİÖŞÜ]/g, (c) => tr[c] ?? c).replace(/[^A-Za-z0-9_]/g, "_")}`;
}

export function ViewPersist({ api, state, ds, onSession }: {
  api: Api; state: SessionState; ds: Dataset; onSession: (s: SessionState) => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(defaultName(ds.id));
  const [script, setScript] = useState<{ view: string; script: string; file: string; exists: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [copied, setCopied] = useState(false);

  if (ds.view) {
    return (
      <div className="vp vp-done">
        <span className="pill pill-ok">onaylı view</span> <code>{ds.view}</code>
        <span className="muted small"> — veri bu view'dan okunuyor</span>
      </div>
    );
  }

  const err = (e: unknown) => (e instanceof ApiError ? (e.detail.length ? e.detail.join(" · ") : e.message) : String(e));

  const generate = async () => {
    setBusy(true); setMsg(null);
    try {
      setScript(await api.viewScript(state.id, ds.id, name));
    } catch (e) {
      setMsg({ ok: false, text: err(e) });
    } finally {
      setBusy(false);
    }
  };

  const useView = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await api.useView(state.id, ds.id, name);
      onSession(r.session);
      setMsg({ ok: true, text: `Dataset artık ${r.view} view'ından okunuyor.` + (r.lost_filters.length ? ` Bu veriye artık uygulanamayan filtreler: ${r.lost_filters.join(", ")}.` : " Filtreler çalışmaya devam ediyor.") });
    } catch (e) {
      setMsg({ ok: false, text: err(e) });
    } finally {
      setBusy(false);
    }
  };

  const download = () => {
    if (!script) return;
    const url = URL.createObjectURL(new Blob([script.script], { type: "text/plain;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url; a.download = `${script.view}.sql`; a.click();
    URL.revokeObjectURL(url);
  };

  if (!open) {
    return (
      <button type="button" className="link-btn vp-open" onClick={() => setOpen(true)} title="Bu dataset'i veritabanında onaylı bir view olarak kalıcılaştır">
        View olarak kalıcılaştır
      </button>
    );
  }

  return (
    <div className="vp">
      <div className="vp-row">
        <label className="small muted" htmlFor={`vp-${ds.id}`}>View adı</label>
        <span className="vp-schema"><code>rpt.</code></span>
        <input id={`vp-${ds.id}`} className="dict-input vp-name" value={name} onChange={(e) => { setName(e.target.value); setScript(null); }} />
        <button type="button" className="btn btn-secondary btn-sm" onClick={generate} disabled={busy || !name}>Script oluştur</button>
        <button type="button" className="link-btn" onClick={() => setOpen(false)}>Kapat</button>
      </div>
      {script ? (
        <>
          <ol className="vp-steps small">
            <li>Scripti inceleyin (agent çalıştırmadı; veritabanına yazma yetkisi yok).</li>
            <li>SSMS'te ya da DBA onayıyla çalıştırın. Dosya: <code>{script.file}</code></li>
            <li>Sonra <b>"View'ı kullan"</b> deyin: view doğrulanır, sözlüğe eklenir ve dataset ona bağlanır.</li>
          </ol>
          <pre className="code-block sql vp-script">{script.script}</pre>
          <div className="vp-row">
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => { void navigator.clipboard?.writeText(script.script); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
              {copied ? "Kopyalandı" : "Kopyala"}
            </button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={download}>.sql indir</button>
            <button type="button" className="btn btn-primary btn-sm" onClick={useView} disabled={busy}>
              {script.exists ? "View mevcut — kullan" : "View'ı oluşturdum, kullan"}
            </button>
          </div>
        </>
      ) : null}
      {msg ? <div className={msg.ok ? "vp-ok small" : "banner-error"}>{msg.text}</div> : null}
    </div>
  );
}
