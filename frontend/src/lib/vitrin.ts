// Vitrin katalog filtresi (sol menü ↔ sayfa) ve "son açılanlar" (tarayıcıda, kullanıcıya özel kolaylık).
import type { VitrinCard } from "../types";

export type VitrinScope = "all" | "shared" | "mine" | "recent" | "retired";
export interface VitrinFilter { scope: VitrinScope; domain: string | null }

const LS_RECENT = "bi.vitrin.recent";

export function recentIds(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(LS_RECENT) ?? "[]");
    return Array.isArray(v) ? v.filter((x) => typeof x === "string") : [];
  } catch { return []; }
}

export function markOpened(id: string): void {
  try { localStorage.setItem(LS_RECENT, JSON.stringify([id, ...recentIds().filter((x) => x !== id)].slice(0, 12))); } catch { /* yok say */ }
}

/** kapsam + iş alanı filtresi; "son açılanlar" açılma sırasıyla */
export function applyFilter(items: VitrinCard[], f: VitrinFilter): VitrinCard[] {
  const active = items.filter((r) => (f.scope === "retired" ? r.status === "retired" : r.status === "active"));
  let out = active.filter((r) => (f.scope !== "shared" || !r.mine) && (f.scope !== "mine" || r.mine) && (!f.domain || r.domains.includes(f.domain)));
  if (f.scope === "recent") {
    const order = recentIds();
    out = out.filter((r) => order.includes(r.id)).sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
  }
  return out;
}

export function counts(items: VitrinCard[]) {
  const active = items.filter((r) => r.status === "active");
  const recent = new Set(recentIds());
  const domains: Record<string, number> = {};
  for (const r of active) for (const d of r.domains) domains[d] = (domains[d] ?? 0) + 1;
  return {
    all: active.length, shared: active.filter((r) => !r.mine).length, mine: active.filter((r) => r.mine).length,
    recent: active.filter((r) => recent.has(r.id)).length, retired: items.length - active.length, domains,
  };
}

export const SCOPE_LABEL: Record<VitrinScope, string> = {
  all: "Tümü", shared: "Bana paylaşılanlar", mine: "Yayınlarım", recent: "Son açılanlar", retired: "Yayından kalkanlar",
};
