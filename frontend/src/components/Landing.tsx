// Giriş sayfası: koyu sahnede iki büyük kutu — Vitrin (yayınlanmış raporlar) ve Tasarım (agent ile rapor tasarlama).
// Kullanıcı yetkisi dahilinde seçer; seçim hatırlanır (sonraki açılış doğrudan o moda), logodan buraya dönülür.
import type { Me, SessionSummary, VitrinCard } from "../types";
import "./landing.css";

export type LandingChoice = "vitrin" | "design";

const fmtDate = (iso?: string) => {
  if (!iso) return "";
  try { return new Date(iso).toLocaleDateString("tr-TR", { day: "numeric", month: "long" }); } catch { return ""; }
};

export function Landing({ me, vitrin, sessions, onChoose, onAdmin }: {
  me: Me | null;
  vitrin: VitrinCard[] | null;
  sessions: SessionSummary[] | null;
  onChoose: (c: LandingChoice) => void;
  onAdmin?: () => void;
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
      <header className="ld-head">
        <span className="ld-mark" aria-hidden="true">
          <svg viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" strokeLinecap="round"><circle cx="7" cy="7" r="5" strokeWidth="1.7" /><path d="M5 9V7.5M7 9V5M9 9V6.5" strokeWidth="1.5" /><path d="M10.8 10.8 14 14" strokeWidth="2" /></svg>
        </span>
        <span className="ld-brand">BI Lens</span>
        {me ? <span className="ld-user">{me.display_name || me.username}</span> : null}
      </header>

      <section className="ld-hero">
        <h1>{firstName ? `Hoş geldiniz, ${firstName}.` : "Hoş geldiniz."}</h1>
        <p>Raporları keşfedin ya da yenisini tasarlayın.</p>
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
      </div>

      <footer className="ld-foot">
        {caps.admin && onAdmin ? <button type="button" className="ld-link" onClick={onAdmin}>Yönetim</button> : null}
        <span className="ld-muted">Seçiminiz hatırlanır; buraya sol üstteki BI Lens logosundan dönebilirsiniz.</span>
      </footer>
    </div>
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
