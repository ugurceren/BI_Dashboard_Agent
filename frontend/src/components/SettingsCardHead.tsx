// Bağlantı Ayarları kart başlığı: simge, başlık, kaynak rozeti; kartı küçült / aç ve tam genişlik / yarım genişlik düğmeleri.
// Küçültülmüş kartta açıklama yerine tek satırlık özet görünür. Durum tarayıcıda saklanır (bi.settings.layout).
import { useState } from "react";
import type { ReactNode } from "react";

export type CardId = "data" | "dictionary" | "llm";
export interface CardLayout { collapsed: boolean; wide: boolean }
type Layout = Record<CardId, CardLayout>;

const KEY = "bi.settings.layout";
const DEFAULT: Layout = { data: { collapsed: false, wide: false }, dictionary: { collapsed: false, wide: false }, llm: { collapsed: false, wide: true } };

function read(): Layout {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "null");
    if (v && typeof v === "object") return { data: { ...DEFAULT.data, ...v.data }, dictionary: { ...DEFAULT.dictionary, ...v.dictionary }, llm: { ...DEFAULT.llm, ...v.llm } };
  } catch { /* depolama kapalı olabilir */ }
  return DEFAULT;
}

export function useCardLayout() {
  const [layout, setLayout] = useState<Layout>(read);
  const write = (next: Layout) => { setLayout(next); try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* yok say */ } };
  const set = (id: CardId, patch: Partial<CardLayout>) => write({ ...layout, [id]: { ...layout[id], ...patch } });
  const all = (collapsed: boolean) => write({ data: { ...layout.data, collapsed }, dictionary: { ...layout.dictionary, collapsed }, llm: { ...layout.llm, collapsed } });
  return { layout, set, all };
}

const Ico = ({ d, size = 14 }: { d: string; size?: number }) => (
  <svg viewBox="0 0 16 16" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
);
const CHEVRON = "M4 6l4 4 4-4";
const WIDEN = "M2.5 8h11M5 5.5 2.5 8 5 10.5M11 5.5l2.5 2.5-2.5 2.5";
const NARROW = "M1.5 8h4.5M13.5 8H10M4 5.5 6.5 8 4 10.5M12 5.5 9.5 8l2.5 2.5";

export function SettingsCardHead({ icon, iconClass, title, desc, summary, source, layout, onChange }: {
  icon: ReactNode; iconClass?: string; title: string; desc: ReactNode; summary: ReactNode; source?: "ui" | "env";
  layout: CardLayout; onChange: (patch: Partial<CardLayout>) => void;
}) {
  const { collapsed, wide } = layout;
  return (
    <header className={`st-card-head${collapsed ? " is-collapsed" : ""}`}>
      <span className={`st-card-icon${iconClass ? ` ${iconClass}` : ""}`}>{icon}</span>
      <button type="button" className="st-card-title" onClick={() => onChange({ collapsed: !collapsed })} aria-expanded={!collapsed}>
        <h2>{title}{source ? <span className={`st-src-badge is-${source}`} title={source === "ui" ? "Arayüzden kaydedilmiş ayar" : "backend/.env ayarı kullanılıyor"}>{source === "ui" ? "arayüz" : ".env"}</span> : null}</h2>
        {collapsed ? <p className="muted st-card-summary">{summary}</p> : <p className="muted">{desc}</p>}
      </button>
      <div className="st-card-tools">
        <button type="button" className="st-icon-btn st-wide-btn" onClick={() => onChange({ wide: !wide })}
          title={wide ? "Yarım genişlik" : "Tam genişlik"} aria-label={wide ? "Yarım genişlik" : "Tam genişlik"}>
          <Ico d={wide ? NARROW : WIDEN} />
        </button>
        <button type="button" className={`st-icon-btn st-chevron${collapsed ? " is-collapsed" : ""}`} onClick={() => onChange({ collapsed: !collapsed })}
          title={collapsed ? "Genişlet" : "Daralt"} aria-label={collapsed ? "Genişlet" : "Daralt"}>
          <Ico d={CHEVRON} />
        </button>
      </div>
    </header>
  );
}
