// Sunum: tam ekran slaytlar (yöneticilere). ← → / Boşluk / PageDown ile ilerler, Esc çıkar, F tam ekran, N konuşmacı notu.
import { useCallback, useEffect, useState } from "react";
import { ROLE_TABLE, SLIDES, type Art, type Slide } from "./content";
import { Kicker, Rich, Shot } from "./ui";
import "./tour.css";

export function Presentation({ index, onGo, onExit, onGuide }: {
  index: number;
  onGo: (i: number) => void;
  onExit: () => void;
  onGuide: () => void;
}) {
  const i = Math.min(Math.max(index, 0), SLIDES.length - 1);
  const slide = SLIDES[i];
  const [notes, setNotes] = useState(false);
  const next = useCallback(() => { if (i < SLIDES.length - 1) onGo(i + 1); }, [i, onGo]);
  const prev = useCallback(() => { if (i > 0) onGo(i - 1); }, [i, onGo]);

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
      if (document.querySelector(".tr-lightbox")) return;              // büyütülmüş görsel açıkken slayt değişmesin
      if (["ArrowRight", "PageDown", " "].includes(e.key)) { e.preventDefault(); next(); }
      else if (["ArrowLeft", "PageUp"].includes(e.key)) { e.preventDefault(); prev(); }
      else if (e.key === "Home") onGo(0);
      else if (e.key === "End") onGo(SLIDES.length - 1);
      else if (e.key === "Escape") onExit();
      else if (e.key.toLowerCase() === "n") setNotes((n) => !n);
      else if (e.key.toLowerCase() === "f") {
        if (document.fullscreenElement) void document.exitFullscreen();
        else void document.documentElement.requestFullscreen?.().catch(() => undefined);
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [next, prev, onGo, onExit]);

  return (
    <div className="tr-present" data-testid="presentation" aria-roledescription="sunum">
      <header className="tr-bar">
        <button type="button" className="tr-ghost" onClick={onExit} title="Sunumdan çık (Esc)">← Çık</button>
        <span className="tr-bar-title">BI Lens · Tanıtım</span>
        <span className="tr-spacer" />
        <button type="button" className="tr-ghost" onClick={() => setNotes((n) => !n)} aria-pressed={notes} title="Konuşmacı notu (N)">Not</button>
        <button type="button" className="tr-ghost" onClick={onGuide} title="Kullanım kılavuzu">Kılavuz</button>
      </header>

      <main key={slide.id} className={`tr-slide${slide.shot ? " has-shot" : ""}${slide.art ? ` art-${slide.art}` : ""}`}
        aria-roledescription="slayt" aria-label={`${i + 1} / ${SLIDES.length}: ${slide.title}`}>
        <SlideBody slide={slide} />
      </main>

      {notes ? <aside className="tr-notes" aria-label="Konuşmacı notu"><Rich text={slide.notes} /></aside> : null}

      <footer className="tr-foot">
        <button type="button" className="tr-nav" onClick={prev} disabled={i === 0} aria-label="Önceki slayt">‹</button>
        <div className="tr-dots" role="tablist" aria-label="Slaytlar">
          {SLIDES.map((s, k) => (
            <button key={s.id} type="button" role="tab" aria-selected={k === i} aria-label={`${k + 1}. ${s.title}`}
              className={`tr-dot${k === i ? " is-on" : ""}`} onClick={() => onGo(k)} />
          ))}
        </div>
        <span className="tr-count" data-testid="slide-count">{i + 1} / {SLIDES.length}</span>
        <button type="button" className="tr-nav" onClick={next} disabled={i === SLIDES.length - 1} aria-label="Sonraki slayt">›</button>
        <div className="tr-progress" aria-hidden="true"><i style={{ width: `${((i + 1) / SLIDES.length) * 100}%` }} /></div>
      </footer>
    </div>
  );
}

function SlideBody({ slide }: { slide: Slide }) {
  const text = (
    <div className="tr-text">
      {slide.kicker ? <Kicker>{slide.kicker}</Kicker> : null}
      <h1>{slide.title}</h1>
      {slide.lead ? <p className="tr-lead"><Rich text={slide.lead} /></p> : null}
      {slide.bullets?.length ? <ul className="tr-bullets">{slide.bullets.map((b) => <li key={b}><Rich text={b} /></li>)}</ul> : null}
    </div>
  );
  return (
    <>
      {text}
      {slide.shot ? <Shot src={slide.shot} className="tr-slide-shot" /> : slide.art ? <ArtView art={slide.art} /> : null}
    </>
  );
}

function ArtView({ art }: { art: Art }) {
  if (art === "flow" || art === "value") {
    const steps = art === "flow"
      ? [["1", "İhtiyaç", "KPI, kırılım, dönem"], ["2", "Veri", "Sözlük, ilişki onayı, SQL"], ["3", "Tasarım", "Dashboard, sohbetle düzenleme"], ["4", "Vitrin", "Yayın, paylaşım, yetki"]]
      : [["⏱", "Hız", "Talepten yayına tek oturum"], ["≡", "Tutarlılık", "Aynı sözlük, aynı kurallar"], ["◆", "Güvenlik", "Yetki ve KVKK sistemde"], ["↗", "Paylaşım", "Vitrin, AD grupları"]];
    return (
      <div className="tr-art tr-flow" aria-hidden="true">
        {steps.map(([n, t, d]) => (
          <div key={t} className="tr-flow-step"><b className="tr-flow-n">{n}</b><strong>{t}</strong><span>{d}</span></div>
        ))}
      </div>
    );
  }
  if (art === "security") {
    const items = [["SQL", "Yalnız okuma, her sorgu doğrulanır"], ["PII", "Kişisel veri rolüne göre gizli"], ["T-1", "Veri tarihi kuralları"], ["LOG", "Denetim kaydı"], ["LLM", "Kurum içinde"]];
    return (
      <div className="tr-art tr-shield" aria-hidden="true">
        {items.map(([k, d]) => <div key={k} className="tr-shield-item"><b>{k}</b><span>{d}</span></div>)}
      </div>
    );
  }
  if (art === "roles") {
    return (
      <div className="tr-art tr-roles">
        <table>
          <thead><tr>{ROLE_TABLE.head.map((h) => <th key={h}>{h}</th>)}</tr></thead>
          <tbody>{ROLE_TABLE.rows.map((r) => <tr key={r[0]}>{r.map((c, k) => <td key={k} className={c === "✓" ? "is-yes" : undefined}>{c}</td>)}</tr>)}</tbody>
        </table>
      </div>
    );
  }
  if (art === "arch") {
    return (
      <div className="tr-art tr-arch" aria-label="Mimari şema">
        <div className="tr-arch-row"><div className="tr-box">Tarayıcı<small>Vitrin · Tasarım · Kontrol Paneli</small></div></div>
        <div className="tr-arch-arrow">↓ <small>IIS + Windows kimliği (sunucu modu)</small></div>
        <div className="tr-arch-row"><div className="tr-box is-core">BI Lens<small>FastAPI + React · agent, SQL doğrulayıcı, yetki</small></div></div>
        <div className="tr-arch-arrow">↓</div>
        <div className="tr-arch-row tr-arch-3">
          <div className="tr-box">SQL Server<small>rapor verisi (salt-okunur)</small></div>
          <div className="tr-box">Veri sözlüğü<small>tablo, kolon, ilişki</small></div>
          <div className="tr-box">Dil modeli<small>kurum içi sunucu</small></div>
        </div>
        <div className="tr-arch-row"><div className="tr-box is-muted">Meta veritabanı<small>roller, yayınlar, denetim</small></div></div>
      </div>
    );
  }
  if (art === "roadmap") {
    const phases = [["Bugün", "Masaüstü kurulum, demo ve pilot raporlar"], ["Pilot", "Ortak sunucu, seçili birim, AD grupları"], ["Yaygınlaştırma", "Eğitim, sözlük genişletme, envanter taşıma"]];
    return (
      <div className="tr-art tr-road" aria-hidden="true">
        {phases.map(([t, d], k) => <div key={t} className="tr-road-step"><b>{k + 1}</b><strong>{t}</strong><span>{d}</span></div>)}
      </div>
    );
  }
  return (
    <div className="tr-art tr-end" aria-hidden="true">
      <svg viewBox="0 0 16 16" width="96" height="96" fill="none" stroke="currentColor" strokeLinecap="round"><circle cx="7" cy="7" r="5" strokeWidth="1.2" /><path d="M5 9V7.5M7 9V5M9 9V6.5" strokeWidth="1.1" /><path d="M10.8 10.8 14 14" strokeWidth="1.5" /></svg>
    </div>
  );
}
