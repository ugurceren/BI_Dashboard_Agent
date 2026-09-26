// Spec temasından türetilmiş renkler ve CSS değişkenleri.
import type { CSSProperties } from "react";
import type { Theme } from "../types";

export const DEFAULT_THEME: Theme = {
  mode: "light",
  palette: ["#2563eb", "#0ea5e9", "#14b8a6", "#f59e0b", "#ef4444", "#8b5cf6", "#64748b"],
  background: "#f4f6fb",
  surface: "#ffffff",
  text: "#0f172a",
  mutedText: "#64748b",
  accent: "#2563eb",
  border: "#e2e8f0",
  fontFamily: "Inter, 'Segoe UI', system-ui, sans-serif",
  radius: 12,
  cardStyle: "elevated",
  density: "comfortable",
  headerStyle: "plain",
};

export function normalizeTheme(t: Partial<Theme> | undefined): Theme {
  const th = { ...DEFAULT_THEME, ...(t ?? {}) } as Theme;
  if (!Array.isArray(th.palette) || th.palette.length === 0) th.palette = DEFAULT_THEME.palette;
  if (typeof th.radius !== "number") th.radius = DEFAULT_THEME.radius;
  return th;
}

type RGB = [number, number, number];

export function parseColor(c: string): RGB | null {
  if (!c) return null;
  const s = c.trim();
  let m = /^#([0-9a-f]{3,8})$/i.exec(s);
  if (m) {
    let h = m[1];
    if (h.length === 3 || h.length === 4) h = h.slice(0, 3).split("").map((ch) => ch + ch).join("");
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  }
  m = /^rgba?\(([^)]+)\)$/i.exec(s);
  if (m) {
    const p = m[1].split(/[ ,/]+/).map(Number);
    if (p.length >= 3 && p.slice(0, 3).every(Number.isFinite)) return [p[0], p[1], p[2]];
  }
  return null;
}

const hex = (n: number) => Math.round(Math.max(0, Math.min(255, n))).toString(16).padStart(2, "0");
export const toHex = ([r, g, b]: RGB) => `#${hex(r)}${hex(g)}${hex(b)}`;

/** a'yı b'ye doğru t oranında karıştır (0 = a, 1 = b). */
export function mix(a: string, b: string, t: number): string {
  const A = parseColor(a);
  const B = parseColor(b);
  if (!A || !B) return a;
  return toHex([A[0] + (B[0] - A[0]) * t, A[1] + (B[1] - A[1]) * t, A[2] + (B[2] - A[2]) * t]);
}

export function alpha(c: string, a: number): string {
  const C = parseColor(c);
  if (!C) return c;
  return `rgba(${C[0]}, ${C[1]}, ${C[2]}, ${a})`;
}

export function luminance(c: string): number {
  const C = parseColor(c);
  if (!C) return 0.5;
  const [r, g, b] = C.map((v) => {
    const x = v / 255;
    return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** Dolgu üstündeki metin için beyaz ya da koyu mürekkep. */
export function inkOn(fill: string, dark = "#0f172a"): string {
  return luminance(fill) > 0.42 ? dark : "#ffffff";
}

export interface DerivedTheme extends Theme {
  isDark: boolean;
  grid: string;        // hairline gridline
  axis: string;        // baseline / axis line
  surface2: string;    // table zebra, subtle wells
  hover: string;
  good: string;
  goodBg: string;
  bad: string;
  badBg: string;
  deEmphasis: string;
  shadow: string;
  bannerText: string;
}

export function deriveTheme(t: Theme): DerivedTheme {
  const isDark = t.mode === "dark" || luminance(t.surface) < 0.2;
  const good = isDark ? "#34d399" : "#047857";
  const bad = isDark ? "#f87171" : "#b91c1c";
  return {
    ...t,
    isDark,
    grid: isDark ? mix(t.border, t.surface, 0.25) : mix(t.border, t.surface, 0.35),
    axis: mix(t.border, t.text, isDark ? 0.18 : 0.14),
    surface2: mix(t.surface, t.text, isDark ? 0.045 : 0.025),
    hover: mix(t.surface, t.accent, isDark ? 0.12 : 0.06),
    good,
    goodBg: alpha(isDark ? "#10b981" : "#10b981", isDark ? 0.16 : 0.12),
    bad,
    badBg: alpha("#ef4444", isDark ? 0.16 : 0.1),
    deEmphasis: mix(t.mutedText, t.surface, 0.35),
    shadow: isDark
      ? "0 1px 2px rgba(0,0,0,.35), 0 8px 24px -12px rgba(0,0,0,.55)"
      : "0 1px 2px rgba(15,23,42,.04), 0 6px 20px -10px rgba(15,23,42,.14)",
    bannerText: inkOn(t.accent),
  };
}

export function themeCssVars(d: DerivedTheme): CSSProperties {
  const compact = d.density === "compact";
  const vars: Record<string, string> = {
    "--db-bg": d.background,
    "--db-surface": d.surface,
    "--db-surface-2": d.surface2,
    "--db-text": d.text,
    "--db-muted": d.mutedText,
    "--db-accent": d.accent,
    "--db-accent-soft": alpha(d.accent, d.isDark ? 0.2 : 0.1),
    "--db-border": d.border,
    "--db-grid": d.grid,
    "--db-hover": d.hover,
    "--db-font": d.fontFamily || DEFAULT_THEME.fontFamily,
    "--db-radius": `${Math.max(0, d.radius)}px`,
    "--db-radius-sm": `${Math.max(0, Math.min(d.radius, 10) * 0.6)}px`,
    "--db-gap": compact ? "12px" : "18px",
    "--db-pad": compact ? "12px 14px" : "16px 18px",
    "--db-pad-x": compact ? "14px" : "18px",
    "--db-good": d.good,
    "--db-good-bg": d.goodBg,
    "--db-bad": d.bad,
    "--db-bad-bg": d.badBg,
    "--db-shadow": d.shadow,
    "--db-banner-text": d.bannerText,
    "--db-banner-bg": `linear-gradient(135deg, ${d.accent} 0%, ${mix(d.accent, d.isDark ? "#000000" : "#0b1220", 0.28)} 100%)`,
    "colorScheme": d.isDark ? "dark" : "light",
  };
  return vars as unknown as CSSProperties;
}

/** Sıralı (tek ton) rampa: yüzeye yakın açık → koyu. */
export function sequentialRamp(base: string, surface: string, steps = 6): string[] {
  const out: string[] = [];
  for (let i = 0; i < steps; i++) {
    const t = 0.85 - (0.85 * i) / (steps - 1);
    out.push(mix(base, surface, t));
  }
  return out;
}
