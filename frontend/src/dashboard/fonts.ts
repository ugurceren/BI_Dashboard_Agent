// Inter (değişken, latin + latin-ext: Türkçe karakterler ve ₺ dahil) — paketle birlikte gelir, ağ gerekmez.
import latinUrl from "@fontsource-variable/inter/files/inter-latin-wght-normal.woff2?url";
import latinExtUrl from "@fontsource-variable/inter/files/inter-latin-ext-wght-normal.woff2?url";

const LATIN =
  "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD";
const LATIN_EXT =
  "U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF";

let done = false;
export function registerFonts() {
  if (done || typeof document === "undefined" || typeof FontFace === "undefined") return;
  done = true;
  for (const [url, range] of [[latinUrl, LATIN], [latinExtUrl, LATIN_EXT]] as const) {
    try {
      const ff = new FontFace("Inter", `url(${url}) format("woff2")`, { weight: "100 900", style: "normal", unicodeRange: range, display: "swap" });
      document.fonts.add(ff);
      ff.load().catch(() => undefined);
    } catch {
      /* yoksay */
    }
  }
}
