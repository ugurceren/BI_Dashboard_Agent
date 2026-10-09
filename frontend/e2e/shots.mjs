// Tanıtım / kılavuz ekran görüntüleri: izole backend (sahte LLM, AdventureWorks demo) açılır, ekranlar hazırlanır,
// frontend/public/tanitim/*.jpg olarak kaydedilir (derlemede dist/tanitim/'e kopyalanır). Kişisel / kurum verisi içermez.
// Çalıştırma (frontend klasöründen): npm run build && npm run shots     — testlerle çakışmasın diye ayrı portlar.
import { spawn, spawnSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { chromium } from "@playwright/test";

const ROOT = process.cwd();
const PY = path.resolve(ROOT, "../backend/.venv/Scripts/python.exe");
const OUT = path.resolve(ROOT, "public/tanitim");
const LLM_PORT = 8096, API_PORT = 8094;
const BASE = `http://127.0.0.1:${API_PORT}`;
// sunucu modu + sahte kurum kullanıcısı: görüntülerde bu bilgisayarın kullanıcı adı görünmesin
const USER = { "X-Remote-User": "KURUM\\admin", "X-Remote-Groups": "BI_Yoneticiler" };
const FAKE_ENV = { E2E_FAKE_LLM_PORT: String(LLM_PORT), E2E_FAKE_MODEL: "qwen3.8-27b" };
mkdirSync(OUT, { recursive: true });

const procs = [];
const start = (args, env = {}) => {
  const p = spawn(PY, ["-X", "utf8", ...args], { cwd: ROOT, env: { ...process.env, ...env }, stdio: "ignore" });
  procs.push(p);
};
// Windows'ta venv python.exe bir başlatıcıdır, asıl Python alt süreçtir: ağaç olarak kapatılır (yoksa sunucu açık kalır)
const stopAll = () => { for (const p of procs) { try { spawnSync("taskkill", ["/PID", String(p.pid), "/T", "/F"], { stdio: "ignore" }); } catch { /* yoksay */ } } };
process.on("exit", stopAll);

async function waitFor(url, ms = 300_000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try { if ((await fetch(url)).ok) return; } catch { /* henüz açılmadı */ }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error(`açılmadı: ${url}`);
}

const api = async (method, p, body) => {
  const r = await fetch(BASE + p, { method, headers: { "Content-Type": "application/json", ...USER }, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) throw new Error(`${method} ${p} → ${r.status} ${await r.text()}`);
  return r.json();
};

async function settle(page, ms = 1500) {
  await page.waitForLoadState("networkidle").catch(() => undefined);
  await page.waitForTimeout(ms);
}

async function chat(page, text) {
  const box = page.locator(".composer-box textarea");
  await box.waitFor({ state: "visible" });
  await page.waitForFunction(() => !document.querySelector(".composer-box textarea")?.disabled, null, { timeout: 300_000 });
  await box.fill(text);
  await box.press("Enter");
  await page.waitForTimeout(800);
  await page.waitForFunction(() => !document.querySelector(".composer-box textarea")?.disabled, null, { timeout: 600_000 });
}

let dbgPage = null;
async function portFree(port) {
  try { await fetch(`http://127.0.0.1:${port}/`); return false; } catch { return true; }
}

async function main() {
  for (const p of [LLM_PORT, API_PORT]) if (!(await portFree(p))) throw new Error(`port ${p} dolu: önceki bir çalıştırma açık kalmış olabilir`);
  start(["e2e/fake_llm.py", String(LLM_PORT)], FAKE_ENV);
  start(["e2e/run_backend.py", String(API_PORT), "server"], FAKE_ENV);
  await waitFor(`http://127.0.0.1:${LLM_PORT}/v1/models`);
  await waitFor(`${BASE}/api/health`);   // sağlık kimlik istemez
  console.log("sunucular hazır");

  // --- veri: demo raporları, yayın, envanter için birkaç rapor
  const demo = await api("POST", "/api/sessions", {});
  await api("POST", `/api/sessions/${demo.id}/demo`);
  const pub = await api("POST", `/api/sessions/${demo.id}/publish`, { description: "İnternet ve bayi satış performansı, ülke ve kategori kırılımlarıyla.", grants: [] });
  const styled = await api("POST", "/api/sessions", {});
  await api("POST", `/api/sessions/${styled.id}/demo`);
  await api("PUT", `/api/sessions/${styled.id}/title`, { title: "Satış Yönetim Panosu" });
  const st = (await api("GET", `/api/sessions/${styled.id}`)).spec;
  st.title = "Satış Yönetim Panosu";
  st.theme = { ...st.theme, mode: "dark", background: "#0b1220", surface: "#111a2e", text: "#e5e7eb", mutedText: "#94a3b8", border: "#1f2a44" };
  st.pages = [{ id: "genel", title: "Genel Bakış" }, { id: "detay", title: "Ürün Detayı" }];
  for (const v of st.visuals) v.page = v.id === "product_table" ? "detay" : "genel";
  const kpi = st.visuals.find((v) => v.id === "kpi_sales");
  kpi.options = { ...kpi.options, background: "#1e3a8a", textColor: "#ffffff", valueSize: "xl" };
  await api("PUT", `/api/sessions/${styled.id}/spec`, st);
  await api("PUT", `/api/sessions/${styled.id}/status`, { status: "test" });
  await api("POST", `/api/sessions/${styled.id}/publish`, { description: "Yönetim için özet pano: KPI'lar, aylık trend ve ürün detayı sayfası.",
    grants: [{ principal_type: "group", principal: "BI_Satis_Ekibi" }, { principal_type: "user", principal: "KURUM\\ayse.yilmaz", can_export: true }] });
  // Yönetim ekranı için örnek rol atamaları (sahte kurum adları)
  for (const a of [{ principal_type: "group", principal: "BI_Tasarimcilar", platform_role: "builder" },
                   { principal_type: "group", principal: "BI_Satis_Ekibi", platform_role: "viewer", data_role: "standart" },
                   { principal_type: "user", principal: "KURUM\\ayse.yilmaz", platform_role: "builder" }]) {
    await api("PUT", "/api/admin/assignments", a);
  }
  for (const [title, status] of [["Bayi Performans Raporu", "design"], ["Müşteri Segment Analizi", "idea"]]) {
    const s = await api("POST", "/api/sessions", {});
    await api("PUT", `/api/sessions/${s.id}/title`, { title });
    await api("PUT", `/api/sessions/${s.id}/status`, { status });
  }

  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "tr-TR", baseURL: BASE, extraHTTPHeaders: USER });
  await ctx.addInitScript(() => { try { localStorage.setItem("bi.inventoryView", "list"); } catch { /* yoksay */ } });
  const page = await ctx.newPage();
  dbgPage = page;
  const shot = async (name, ms) => { await settle(page, ms); await page.screenshot({ path: path.join(OUT, `${name}.jpg`), type: "jpeg", quality: 80 }); console.log("✓", name); };

  await page.goto("/#/giris"); await shot("01-giris", 2000);

  // sohbetle rapor (sahte LLM senaryosu): ihtiyaç → model önerisi → onay → veri → tasarım
  const chatS = await api("POST", "/api/sessions", {});
  await api("PUT", `/api/sessions/${chatS.id}/title`, { title: "Bayi Satışları" });   // senaryodaki test adı görünmesin
  await page.goto(`/#/r/${chatS.id}`);
  await chat(page, "Bayi satışlarını bölge ve aya göre gösteren bir dashboard istiyorum.");
  await shot("02-ihtiyac-model", 2000);   // sohbet: tablolar ve ilişki önerisi (Model sekmesi bu fazda tüm sözlüğü gösterir, okunmaz)
  await chat(page, "Evet, yalnız bu rapora kaydet.");
  await page.locator("[data-visual-id]").first().waitFor({ timeout: 300_000 });
  const cs = (await api("GET", `/api/sessions/${chatS.id}`)).spec;
  cs.subtitle = "Bölge grubu ve aylık trend";
  await api("PUT", `/api/sessions/${chatS.id}/spec`, cs);
  await page.reload();
  await page.locator("[data-visual-id]").first().waitFor({ timeout: 300_000 });
  await shot("03-tasarim", 3000);
  await page.getByRole("tab", { name: /^Veri/ }).click(); await shot("04-veri", 3000);
  await page.getByRole("tab", { name: /^Model/ }).click(); await shot("05-model", 3000);

  await page.goto(`/#/r/${demo.id}`);
  await page.getByRole("tab", { name: "Dashboard" }).click();                  // önceki rapordan kalan sekme
  await page.locator("[data-visual-id]").first().waitFor({ timeout: 60_000 });
  await page.locator(".db-filter").first().getByRole("button").first().click(); await shot("06-filtre", 1500);
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "Spec" }).click(); await shot("07-spec", 1000);
  await page.getByRole("tab", { name: "Dashboard" }).click();
  await page.getByRole("button", { name: /Yayında|Yayınla/ }).first().click(); await shot("08-yayinla", 1000);
  await page.keyboard.press("Escape");

  await page.goto(`/#/v/${demo.id}`); await page.locator("[data-visual-id]").first().waitFor(); await shot("09-dashboard", 3000);
  await page.goto(`/#/v/${styled.id}`); await page.locator("[data-visual-id]").first().waitFor(); await shot("10-koyu-tema-sayfalar", 3000);
  await page.goto("/#/vitrin"); await shot("11-vitrin", 2000);
  await page.goto(`/#/vitrin/${pub.report.id}`); await page.locator("[data-visual-id]").first().waitFor(); await shot("12-vitrin-rapor", 3000);
  await page.goto("/#/envanter"); await shot("13-envanter", 2000);
  await page.goto("/#/query");
  await page.getByRole("complementary", { name: "Yetkili nesneler" }).waitFor();
  const ed = page.getByTestId("sql-editor").locator(".cm-content");
  await ed.click(); await page.keyboard.press("Control+A"); await page.keyboard.press("Delete");
  await ed.pressSequentially("SELECT TOP 10 SalesTerritoryGroup, SalesTerritoryCountry FROM dbo.DimSalesTerritory", { delay: 0 });
  await page.keyboard.press("Control+Enter"); await page.locator("table").last().waitFor(); await shot("14-sorgu", 2000);
  await page.goto("/#/settings"); await shot("15-ayarlar", 2000);
  await page.goto("/#/admin"); await shot("16-yonetim", 2000);

  await browser.close();
}

main().then(() => { stopAll(); process.exit(0); }, async (e) => {
  console.error(e);
  if (dbgPage) await dbgPage.screenshot({ path: "test-results/shots-hata.png" }).catch(() => undefined);   // hata anındaki ekran
  if (dbgPage) console.error("adres:", dbgPage.url());
  stopAll(); process.exit(1);
});
