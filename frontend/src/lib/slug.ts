// Veri kümesi adı: Türkçe karakterler sadeleşir, harf / rakam / alt çizgi; harfle başlar (backend Dataset id kuralı).
const TR: Record<string, string> = { ç: "c", ğ: "g", ı: "i", İ: "i", ö: "o", ş: "s", ü: "u", Ç: "c", Ğ: "g", Ö: "o", Ş: "s", Ü: "u" };

export function slugifyId(text: string, fallback = "sorgu"): string {
  let s = text.replace(/[çğıİöşüÇĞÖŞÜ]/g, (c) => TR[c] ?? c).toLowerCase().normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
  s = s.replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 60);
  if (!s) return fallback;
  return /^[a-z]/.test(s) ? s : `${fallback}_${s}`.slice(0, 64);
}
