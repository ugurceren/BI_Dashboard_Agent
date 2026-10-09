// Uçtan uca (tarayıcı) testleri: gerçek arayüz + gerçek backend + yerel AdventureWorksDW; LLM senaryolu sahte sunucu.
// Çalıştırma: npm run e2e   ·   gerçek LLM duman testi: npm run e2e:llm   ·   ayrıntılar: docs/TESTING.md
import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

const PY = `"${path.resolve(process.cwd(), "../backend/.venv/Scripts/python.exe")}"`;   // frontend klasöründen çalıştırılır
const LLM = !!process.env.E2E_LLM;
// E2E_SERVERS=desktop|server|none: yalnız o aşamanın backend'i açılır (8 GB RAM'li PC'de bellek baskısı ve soğuk sorgular azalır).
// npm run e2e iki aşamayı sırayla çalıştırır (e2e/run-all.mjs); tek başına "npx playwright test" ikisini birden açar.
const SERVERS = (process.env.E2E_SERVERS ?? "desktop,server").split(",");
const want = (s: string) => SERVERS.includes(s);

const backend = (port: number, mode: string, extra = "") => ({
  command: `${PY} -X utf8 e2e/run_backend.py ${port} ${mode} ${extra}`.trim(),
  url: `http://127.0.0.1:${port}/api/health`, reuseExistingServer: false, timeout: 300_000,
});

export default defineConfig({
  testDir: "./e2e",
  timeout: 300_000,
  expect: { timeout: 60_000 },      // yavaş / belleği az PC: ilk sorgular 10-90 sn sürebiliyor
  fullyParallel: false,
  workers: 1,                       // backend durumu (oturumlar, roller) testler arasında paylaşılır
  retries: 0,
  reporter: [["list"], ["html", { outputFolder: `e2e/report/${SERVERS.join("-")}`, open: "never" }]],
  outputDir: `test-results/${SERVERS.join("-")}`,   // aşama başına ayrı: sonraki aşama öncekinin izlerini silmesin
  use: {
    ...devices["Desktop Chrome"],
    viewport: { width: 1440, height: 900 },
    locale: "tr-TR",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  projects: [
    ...(want("desktop") ? [{ name: "masaustu", testMatch: /(^|[\\/])(landing|inventory|query|dashboard|demo|design|flow)\.spec\.ts$/, use: { baseURL: "http://127.0.0.1:8090" } }] : []),
    ...(want("server") ? [{ name: "sunucu", testMatch: /vitrin\.spec\.ts/, use: { baseURL: "http://127.0.0.1:8092" } }] : []),
    ...(LLM ? [{ name: "gercek-llm", testMatch: /llm-(smoke|design)\.spec\.ts/, timeout: 900_000,
      use: { baseURL: "http://127.0.0.1:8093", actionTimeout: 30_000 } }] : []),
  ],
  webServer: [
    { command: `${PY} -X utf8 e2e/fake_llm.py 8091`, url: "http://127.0.0.1:8091/v1/models", reuseExistingServer: false, timeout: 30_000 },
    ...(want("desktop") ? [backend(8090, "desktop")] : []),
    ...(want("server") ? [backend(8092, "server")] : []),
    ...(LLM ? [backend(8093, "desktop", "--real-llm")] : []),
  ],
});
