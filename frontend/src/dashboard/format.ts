// tr-TR sayı / tarih biçimlendirme.
import type { DatasetField, ValueFormat, VisualOptions } from "../types";

const LOCALE = "tr-TR";

export interface FormatSpec {
  format: ValueFormat;
  /** para birimi (compact + currency için sembol eklemek dahil) */
  currency?: string;
  decimals?: number;
}

const nfCache = new Map<string, Intl.NumberFormat>();
function nf(opts: Intl.NumberFormatOptions): Intl.NumberFormat {
  const key = JSON.stringify(opts);
  let f = nfCache.get(key);
  if (!f) {
    f = new Intl.NumberFormat(LOCALE, opts);
    nfCache.set(key, f);
  }
  return f;
}

export function currencySymbol(code = "TRY"): string {
  if (code === "TRY") return "₺";
  try {
    const parts = nf({ style: "currency", currency: code, currencyDisplay: "narrowSymbol" }).formatToParts(0);
    return parts.find((p) => p.type === "currency")?.value ?? code;
  } catch {
    return code;
  }
}

const COMPACT_STEPS: [number, string][] = [
  [1e12, "Tn"],
  [1e9, "Mr"],
  [1e6, "Mn"],
  [1e3, "Bin"],
];

/** 680632746 → "680,6 Mn"; 1200 → "1,2 Bin" */
export function compactNumber(v: number, decimals?: number): string {
  const abs = Math.abs(v);
  for (const [div, suffix] of COMPACT_STEPS) {
    if (abs >= div) {
      const scaled = v / div;
      const d = decimals ?? 1;
      return `${nf({ maximumFractionDigits: d, minimumFractionDigits: 0 }).format(scaled)} ${suffix}`;
    }
  }
  return nf({ maximumFractionDigits: decimals ?? (abs < 10 && abs % 1 !== 0 ? 2 : 0) }).format(v);
}

export function toNumber(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "boolean") return v ? 1 : 0;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

export function formatValue(raw: unknown, spec: FormatSpec): string {
  const v = toNumber(raw);
  if (v === null) return raw === null || raw === undefined || raw === "" ? "–" : String(raw);
  const { format, decimals } = spec;
  switch (format) {
    case "currency": {
      const code = spec.currency ?? "TRY";
      const d = decimals ?? (Math.abs(v) >= 100 ? 0 : 2);
      try {
        return nf({ style: "currency", currency: code, minimumFractionDigits: d, maximumFractionDigits: d }).format(v);
      } catch {
        return `${nf({ maximumFractionDigits: d }).format(v)} ${code}`;
      }
    }
    case "percent": {
      const d = decimals ?? 1;
      return nf({ style: "percent", minimumFractionDigits: d, maximumFractionDigits: d }).format(v);
    }
    case "compact": {
      const s = compactNumber(v, decimals);
      return spec.currency ? `${s} ${currencySymbol(spec.currency)}` : s;
    }
    case "number":
    default: {
      const d = decimals ?? (Number.isInteger(v) ? 0 : Math.abs(v) >= 100 ? 0 : 2);
      return nf({ minimumFractionDigits: 0, maximumFractionDigits: d }).format(v);
    }
  }
}

/** Eksen tikleri için kısa biçim (her zaman kompakt, yüzde ise yüzde). */
export function formatAxis(v: number, spec: FormatSpec): string {
  if (spec.format === "percent") {
    return nf({ style: "percent", maximumFractionDigits: 0 }).format(v);
  }
  const abs = Math.abs(v);
  const s = abs < 10000 ? nf({ maximumFractionDigits: abs < 10 && abs % 1 !== 0 ? 2 : 0 }).format(v) : compactNumber(v);
  return spec.currency && (spec.format === "currency" || spec.format === "compact") ? `${s} ${currencySymbol(spec.currency)}` : s;
}

/**
 * Bir görseldeki bir alanın etkin formatı.
 * Öncelik: options.format → alanın format'ı → number.
 * Kompakt + para birimi: alan currency ise veya options.currency açıkça verilmişse ₺ eklenir.
 * Yüzde alanlar kompakta çevrilmez.
 */
export function resolveFormat(
  field: DatasetField | undefined,
  options: VisualOptions | undefined,
  preferField = false,
): FormatSpec {
  const fieldFmt = field?.format;
  const optFmt = options?.format;
  let format: ValueFormat =
    (preferField ? fieldFmt ?? optFmt : optFmt ?? fieldFmt) ?? "number";
  if (fieldFmt === "percent" && format === "compact") format = "percent";
  let currency: string | undefined;
  if (format === "currency") currency = options?.currency ?? "TRY";
  else if (format === "compact" && (fieldFmt === "currency" || (!fieldFmt && options?.currency)))
    currency = options?.currency ?? "TRY";
  return { format, currency, decimals: options?.decimals };
}

// ---- Tarih / dönem etiketleri ----

const MONTHS_SHORT = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"];
const MONTHS_LONG = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];

const YM_RE = /^(\d{4})-(\d{2})$/;
const DATE_RE = /^(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?$/;

export function isPeriodLike(v: unknown): boolean {
  return typeof v === "string" && (YM_RE.test(v) || DATE_RE.test(v));
}

/** Eksen etiketi: "2025-01" → "Oca 25", "2026-03-14" → "14 Mar" */
export function formatCategory(v: unknown, short = true): string {
  if (v === null || v === undefined) return "(boş)";
  if (typeof v === "string") {
    let m = YM_RE.exec(v);
    if (m) {
      const mi = Number(m[2]) - 1;
      if (mi >= 0 && mi < 12)
        return short ? `${MONTHS_SHORT[mi]} ${m[1].slice(2)}` : `${MONTHS_LONG[mi]} ${m[1]}`;
    }
    m = DATE_RE.exec(v);
    if (m) {
      const mi = Number(m[2]) - 1;
      if (mi >= 0 && mi < 12)
        return short ? `${Number(m[3])} ${MONTHS_SHORT[mi]} ${m[1].slice(2)}` : `${Number(m[3])} ${MONTHS_LONG[mi]} ${m[1]}`;
    }
    return v;
  }
  if (typeof v === "number") return nf({ maximumFractionDigits: 2 }).format(v);
  return String(v);
}

export function escapeHtml(s: unknown): string {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
