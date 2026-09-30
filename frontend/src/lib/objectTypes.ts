// Veri nesnesi tipleri (Tablo / View / SP / Dataset) ve iki düzeyli gruplama: domain → tip ya da tip → domain.
// Sorgu Çalıştır'daki "Yetkili nesneler" paneli ve Veri Erişimim sayfası ortak kullanır.

export type GroupBy = "domain" | "type";
export type ObjType = "table" | "view" | "procedure" | "dataset";

export const TYPE_ORDER: ObjType[] = ["table", "view", "procedure", "dataset"];
export const TYPE_TITLE: Record<ObjType, string> = { table: "Tablolar", view: "View'lar", procedure: "Stored procedure'ler", dataset: "Rapor dataset'leri" };
export const TYPE_SHORT: Record<ObjType, string> = { table: "Tablo", view: "View", procedure: "SP", dataset: "Dataset" };
export const TYPE_BADGE: Record<ObjType, string> = { table: "T", view: "V", procedure: "SP", dataset: "D" };

/** sözlükteki tür (fact / dimension / bridge / view) → gösterilen tip */
export const typeOfKind = (kind?: string | null): ObjType => (kind === "view" ? "view" : "table");

export interface TwoLevel<T> {
  key: string;
  title: string;
  type?: ObjType;          // tip → domain modunda grubun tipi
  count: number;
  subs: { key: string; title: string; type?: ObjType; items: T[] }[];
}

const byDomain = (a: string, b: string) => (a === "Diğer" ? 1 : b === "Diğer" ? -1 : a.localeCompare(b, "tr"));
const byType = (a: string, b: string) => TYPE_ORDER.indexOf(a as ObjType) - TYPE_ORDER.indexOf(b as ObjType);

/** Öğeleri domain → tip ya da tip → domain olarak iki düzeyde gruplar (domain'ler alfabetik, "Diğer" sonda). */
export function groupTwoLevel<T>(items: T[], groupBy: GroupBy, typeOf: (x: T) => ObjType, domainOf: (x: T) => string): TwoLevel<T>[] {
  const outer = new Map<string, Map<string, T[]>>();
  for (const x of items) {
    const d = domainOf(x) || "Diğer";
    const t = typeOf(x);
    const [g, s] = groupBy === "domain" ? [d, t] : [t, d];
    const m = outer.get(g) ?? new Map<string, T[]>();
    m.set(s, [...(m.get(s) ?? []), x]);
    outer.set(g, m);
  }
  return [...outer.keys()].sort(groupBy === "domain" ? byDomain : byType).map((g) => {
    const m = outer.get(g)!;
    const subs = [...m.keys()].sort(groupBy === "domain" ? byType : byDomain).map((k) => ({
      key: `${groupBy}:${g}:${k}`, title: groupBy === "domain" ? TYPE_SHORT[k as ObjType] : k,
      type: groupBy === "domain" ? (k as ObjType) : undefined, items: m.get(k)!,
    }));
    return { key: `${groupBy}:${g}`, title: groupBy === "domain" ? g : TYPE_TITLE[g as ObjType],
      type: groupBy === "type" ? (g as ObjType) : undefined, count: subs.reduce((n, s) => n + s.items.length, 0), subs };
  });
}
