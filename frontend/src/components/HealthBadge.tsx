// LLM (Qwen) / görsel model / veri bağlantısı göstergesi — sol menüde, kullanıcı kartının üstünde.
import type { Health } from "../types";

export function HealthBadge({ health, healthError, compact, onClick }: { health: Health | null; healthError: string | null; compact?: boolean; onClick?: () => void }) {
  const llmOk = !!health?.llm?.reachable;
  const title = healthError
    ? `Backend'e ulaşılamadı: ${healthError}`
    : health
      ? [
          `LLM: ${health.llm.model} (${health.llm.reachable ? "erişilebilir" : "erişilemiyor"})`,
          health.llm.base_url ? `Adres: ${health.llm.base_url}` : "",
          health.llm.error ? `Hata: ${health.llm.error}` : "",
          `Görsel model: ${health.vision.configured ? health.vision.model : "yapılandırılmamış"}`,
          `Veri: ${health.data.ok ? "bağlı" : "hata"} (${health.data.dialect})`,
          health.data.error ? `Veri hatası: ${health.data.error}` : "",
          health.dictionary?.error ? `Sözlük hatası: ${health.dictionary.error}` : "",
          "Tıkla: Bağlantı Ayarları",
        ].filter(Boolean).join("\n")
      : "Kontrol ediliyor…";
  const dot = <span className={`dot ${healthError ? "bad" : health ? (llmOk ? "ok" : "bad") : "wait"}`} />;
  const dataBad = !!health && (!health.data.ok || !!health.dictionary?.error);
  if (compact) return <button type="button" className="sb-health is-compact" title={title} onClick={onClick}>{dataBad ? <span className="dot bad" /> : dot}</button>;
  return (
    <button type="button" className={`sb-health${dataBad ? " is-bad" : ""}`} title={title} onClick={onClick}>
      <div className="sb-health-row">
        {dot}
        <span className="sb-health-model">{healthError ? "Backend yok" : health ? health.llm.model : "…"}</span>
      </div>
      {health ? (
        <div className="sb-health-sub">
          <span className={health.vision.configured ? "is-ok" : "is-off"}>
            <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8Z" fill="none" stroke="currentColor" strokeWidth="1.3" /><circle cx="8" cy="8" r="2" fill="none" stroke="currentColor" strokeWidth="1.3" />{health.vision.configured ? null : <path d="M2.5 13.5l11-11" stroke="currentColor" strokeWidth="1.3" />}</svg>
            {health.vision.configured ? "Görsel" : "Görsel yok"}
          </span>
          <span className={health.data.ok && !health.dictionary?.error ? "is-ok" : "is-bad"}>
            <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.3"><ellipse cx="8" cy="4" rx="5" ry="2" /><path d="M3 4v8c0 1.1 2.2 2 5 2s5-.9 5-2V4M3 8c0 1.1 2.2 2 5 2s5-.9 5-2" /></svg>
            {!health.data.ok ? "Veri hatası" : health.dictionary?.error ? "Sözlük hatası" : "Veri"}
          </span>
        </div>
      ) : null}
    </button>
  );
}
