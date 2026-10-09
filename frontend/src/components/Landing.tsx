// Giriş sayfası: koyu sahnede iki büyük kutu — Vitrin (yayınlanmış raporlar) ve Tasarım (agent ile rapor tasarlama).
// Kullanıcı yetkisi dahilinde seçer; seçim hatırlanır (sonraki açılış doğrudan o moda), logodan buraya dönülür.
import type { Health, Me, SessionSummary, VitrinCard } from "../types";
import "./landing.css";

export type LandingChoice = "vitrin" | "design";

const fmtDate = (iso?: string) => {
  if (!iso) return "";
  try { return new Date(iso).toLocaleDateString("tr-TR", { day: "numeric", month: "long" }); } catch { return ""; }
};

export function Landing({ me, vitrin, sessions, health, onChoose, onSystem, onTour, onGuide }: {
  me: Me | null;
  vitrin: VitrinCard[] | null;
  sessions: SessionSummary[] | null;
  health: Health | null;
  onChoose: (c: LandingChoice) => void;
  /** Kontrol paneli: sistem sayfaları (seçim hatırlanmaz; açılış hep Vitrin ya da Tasarım) */
  onSystem: (page: "admin" | "settings") => void;
  onTour: () => void;
  onGuide: () => void;
}) {
  const caps = me?.capabilities ?? { design: true, admin: true, vitrin: true };
  const live = (vitrin ?? []).filter((r) => r.status === "active");
  const latest = [...live].sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 3);
  const drafts = sessions ?? [];
  const lastDraft = [...drafts].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))[0];
  // "Uğur Ceren" → "Uğur"; ad yoksa kullanıcı adı ("KURUM\\veli" → "veli")
  const firstName = me?.display_name?.trim().split(/\s+/)[0] || (me?.username ?? "").split("\\").pop() || "";

  return (
    <div className="landing" data-testid="landing">
      <section className="ld-hero">
        <h1>{firstName ? `Hoş geldiniz, ${firstName}.` : "Hoş geldiniz."}</h1>
        <p>Raporları keşfedin, yenisini tasarlayın ya da sistemi yönetin.</p>
        <div className="ld-intro">
          <button type="button" className="ld-intro-btn is-primary" onClick={onTour}><span aria-hidden="true">▶</span> Tanıtımı izle</button>
          <button type="button" className="ld-intro-btn" onClick={onGuide}>Kullanım kılavuzu</button>
        </div>
      </section>

      <div className="ld-cards">
        <button type="button" className="ld-card ld-vitrin" onClick={() => caps.vitrin && onChoose("vitrin")}
          aria-disabled={!caps.vitrin} aria-describedby="ld-vitrin-desc">
          <div className="ld-stage" aria-hidden="true"><VitrinArt count={live.length} /></div>
          <div className="ld-body">
            <h2>Vitrin</h2>
            <p id="ld-vitrin-desc">Yayınlanmış raporlar, yetkiniz dahilinde ve kendi veri rolünüzle.</p>
            <div className="ld-stats">
              {vitrin === null ? <span className="ld-muted">Yükleniyor…</span> : (
                <>
                  <span className="ld-stat"><b>{live.length}</b> rapor yayında</span>
                  {latest.length ? <ul className="ld-list">{latest.map((r) => <li key={r.id}>{r.title}</li>)}</ul> : null}
                </>
              )}
            </div>
            <span className="ld-cta">Vitrin'e gir <span aria-hidden="true">→</span></span>
          </div>
        </button>

        <button type="button" className={`ld-card ld-design${caps.design ? "" : " is-locked"}`}
          onClick={() => caps.design && onChoose("design")} aria-disabled={!caps.design} aria-describedby="ld-design-desc">
          <div className="ld-stage" aria-hidden="true"><DesignArt /></div>
          <div className="ld-body">
            <h2>Tasarım {caps.design ? null : <LockIcon />}</h2>
            <p id="ld-design-desc">
              {caps.design ? "Agent ile rapor tasarlayın: ihtiyaç → veri → dashboard."
                : "Tasarım yetkiniz yok — yöneticinizden isteyin."}
            </p>
            {caps.design ? (
              <div className="ld-stats">
                {sessions === null ? <span className="ld-muted">Yükleniyor…</span> : (
                  <>
                    <span className="ld-stat"><b>{drafts.length}</b> rapor</span>
                    {lastDraft ? <span className="ld-muted">Son düzenlenen: {lastDraft.title} · {fmtDate(lastDraft.updatedAt)}</span> : null}
                  </>
                )}
              </div>
            ) : null}
            {caps.design ? <span className="ld-cta">Tasarıma gir <span aria-hidden="true">→</span></span> : null}
          </div>
        </button>

        {/* Kontrol paneli: iki hedef (Yönetim, Bağlantı Ayarları) — iç içe buton olmasın diye kart bir grup, hedefler ayrı buton */}
        <div role="group" aria-label="Kontrol Paneli" aria-describedby="ld-control-desc"
          className={`ld-card ld-control${caps.admin ? "" : " is-locked"}`} onClick={() => caps.admin && onSystem("admin")}>
          <div className="ld-stage" aria-hidden="true"><ControlArt /></div>
          <div className="ld-body">
            <h2>Kontrol Paneli {caps.admin ? null : <LockIcon />}</h2>
            <p id="ld-control-desc">
              {caps.admin ? "Roller ve yayınlar, denetim kaydı; veri, sözlük ve dil modeli bağlantıları."
                : "Sistem yönetimi yalnız yöneticilere açık."}
            </p>
            {caps.admin ? (
              <>
                <div className="ld-stats" aria-label="Bağlantı durumu">
                  <Status ok={health?.llm.reachable} label="Dil modeli" detail={health?.llm.model} />
                  <Status ok={health?.data.ok} label="Veri" detail={health?.data.dialect === "tsql" ? "SQL Server" : health?.data.dialect} />
                  {health?.dictionary ? <Status ok={!health.dictionary.error} label="Sözlük" detail={`${health.dictionary.tables} tablo`} /> : null}
                </div>
                <div className="ld-actions" onClick={(e) => e.stopPropagation()}>
                  <button type="button" className="ld-sub" onClick={() => onSystem("admin")}>Yönetim <span aria-hidden="true">→</span></button>
                  <button type="button" className="ld-sub" onClick={() => onSystem("settings")}>Bağlantı Ayarları <span aria-hidden="true">→</span></button>
                </div>
              </>
            ) : null}
          </div>
        </div>
      </div>

      <footer className="ld-foot">
        <span className="ld-muted">Seçiminiz hatırlanır; buraya sol üstteki BI Lens logosundan dönebilirsiniz.</span>
      </footer>
    </div>
  );
}

function Status({ ok, label, detail }: { ok?: boolean; label: string; detail?: string | null }) {
  const st = ok === undefined ? "wait" : ok ? "ok" : "err";
  return (
    <span className={`ld-health is-${st}`}>
      <i aria-hidden="true" />{label}: {st === "wait" ? "kontrol ediliyor…" : st === "ok" ? (detail || "bağlı") : "bağlantı yok"}
    </span>
  );
}

function LockIcon() {
  return (
    <svg className="ld-lock" viewBox="0 0 16 16" width="16" height="16" aria-label="kilitli" role="img" fill="none" stroke="currentColor" strokeWidth="1.5">
      <rect x="3" y="7" width="10" height="7" rx="1.5" /><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" />
    </svg>
  );
}

/** Vitrin: parlayan mini dashboard (KPI'lar, çubuklar, çizgi) — resimdeki ışıklı kalp gibi sıcak bir ışıltı. */
function VitrinArt({ count }: { count: number }) {
  const bars = [38, 62, 48, 80, 56, 92, 70];
  return (
    <svg viewBox="0 0 320 200" className="ld-art">
      <defs>
        <radialGradient id="ldWarm" cx="50%" cy="55%" r="60%">
          <stop offset="0%" stopColor="#ff5a4e" stopOpacity="0.55" />
          <stop offset="55%" stopColor="#b3122b" stopOpacity="0.18" />
          <stop offset="100%" stopColor="#000" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="ldBar" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ff8a7a" />
          <stop offset="100%" stopColor="#c81e3a" />
        </linearGradient>
        <filter id="ldGlow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="4" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
      </defs>
      <rect width="320" height="200" fill="url(#ldWarm)" />
      <g filter="url(#ldGlow)">
        {[0, 1, 2].map((i) => <rect key={i} x={46 + i * 80} y="26" width="68" height="34" rx="8" fill="#2a0b10" stroke="#ff6b5e" strokeOpacity="0.5" />)}
        <text x="80" y="49" textAnchor="middle" className="ld-art-num">{count}</text>
        <rect x="140" y="38" width="36" height="6" rx="3" fill="#ff8a7a" opacity="0.8" />
        <rect x="220" y="38" width="28" height="6" rx="3" fill="#ff8a7a" opacity="0.6" />
        {bars.map((h, i) => <rect key={i} className="ld-bar" style={{ animationDelay: `${i * 90}ms` }} x={52 + i * 32} y={176 - h} width="20" height={h} rx="4" fill="url(#ldBar)" />)}
        <polyline points="62,128 94,112 126,120 158,96 190,104 222,80 254,88" fill="none" stroke="#ffd2cc" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" opacity="0.9" />
      </g>
    </svg>
  );
}

/** Tasarım: yeşil ışıklı sensör / lens halkası (resimdeki saatin arka yüzü gibi). */
function DesignArt() {
  const spokes = Array.from({ length: 36 }, (_, i) => (i * 360) / 36);
  return (
    <svg viewBox="0 0 320 200" className="ld-art">
      <defs>
        <radialGradient id="ldGreen" cx="50%" cy="50%" r="55%">
          <stop offset="0%" stopColor="#3dff8f" stopOpacity="0.7" />
          <stop offset="35%" stopColor="#0fae5a" stopOpacity="0.25" />
          <stop offset="100%" stopColor="#000" stopOpacity="0" />
        </radialGradient>
        <filter id="ldGlowG" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
      </defs>
      <rect width="320" height="200" fill="url(#ldGreen)" />
      <g transform="translate(160 100)">
        <circle r="86" fill="none" stroke="#6b7280" strokeOpacity="0.35" />
        <circle r="70" fill="#07110b" stroke="#9ca3af" strokeOpacity="0.25" />
        <g className="ld-spin">
          {spokes.map((a) => <line key={a} x1="0" y1="-68" x2="0" y2="-40" stroke="#a7f3d0" strokeOpacity="0.28" transform={`rotate(${a})`} />)}
        </g>
        <g filter="url(#ldGlowG)">
          <circle r="22" fill="none" stroke="#4ade80" strokeWidth="2.5" className="ld-pulse" />
          <circle r="9" fill="#4ade80" />
          {[0, 90, 180, 270].map((a) => <circle key={a} r="4.5" cx="0" cy="-34" fill="#86efac" transform={`rotate(${a})`} />)}
        </g>
      </g>
    </svg>
  );
}

/** Kontrol paneli: mor-mavi ışıklı kaydırıcılar ve gösterge (kontrol masası). */
function ControlArt() {
  const sliders = [0.35, 0.7, 0.5, 0.85, 0.25];
  return (
    <svg viewBox="0 0 320 200" className="ld-art">
      <defs>
        <radialGradient id="ldViolet" cx="50%" cy="50%" r="58%">
          <stop offset="0%" stopColor="#8b5cf6" stopOpacity="0.55" />
          <stop offset="45%" stopColor="#4f46e5" stopOpacity="0.2" />
          <stop offset="100%" stopColor="#000" stopOpacity="0" />
        </radialGradient>
        <filter id="ldGlowV" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
      </defs>
      <rect width="320" height="200" fill="url(#ldViolet)" />
      <g filter="url(#ldGlowV)">
        {sliders.map((v, i) => {
          const x = 70 + i * 32, y = 160 - v * 110;
          return (
            <g key={i}>
              <line x1={x} y1="50" x2={x} y2="160" stroke="#a5b4fc" strokeOpacity="0.25" strokeWidth="4" strokeLinecap="round" />
              <line x1={x} y1={y} x2={x} y2="160" stroke="#a78bfa" strokeWidth="4" strokeLinecap="round" />
              <rect className="ld-knob" style={{ animationDelay: `${i * 160}ms` }} x={x - 9} y={y - 5} width="18" height="10" rx="4" fill="#ede9fe" />
            </g>
          );
        })}
        <circle cx="255" cy="70" r="20" fill="none" stroke="#a5b4fc" strokeOpacity="0.35" strokeWidth="3" />
        <path d="M255 70 L268 58" stroke="#c4b5fd" strokeWidth="3" strokeLinecap="round" className="ld-needle" />
        <circle cx="255" cy="130" r="5" fill="#4ade80" /><circle cx="255" cy="148" r="5" fill="#4ade80" /><circle cx="255" cy="166" r="5" fill="#fbbf24" />
      </g>
    </svg>
  );
}
