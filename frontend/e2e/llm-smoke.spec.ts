// Gerçek LLM duman testi (isteğe bağlı): npm run e2e:llm — bu bilgisayarın Bağlantı Ayarları'ndaki LLM ile (ör. Spark).
// Model yanıtları her seferinde farklı olabileceği için metin karşılaştırılmaz; yalnız akışın ilerlediği kontrol edilir:
// ihtiyaç kaydedilir, veri fazına geçilir, agent tabloları ve ilişki önerisini sunup onay ister (SQL yazmadan önce).
import { expect, test, chat, newReport } from "./fixtures";

test("@llm gerçek LLM: ihtiyaçtan model onayına", async ({ page }) => {
  test.setTimeout(900_000);
  const health = await page.request.get("/api/health").then((r) => r.json());
  test.skip(!health.llm?.reachable, `LLM erişilemiyor: ${health.llm?.base_url ?? "?"}`);

  await newReport(page);
  await chat(page, "Bayi satışlarını ürün kategorisi ve bayi tipine göre gösteren bir yönetim raporu istiyorum. "
    + "Tüm dönem, satış tutarı ve sipariş sayısı yeterli; varsayılanlarla devam et.", 600_000);
  // model bir tur daha soru sorabilir: kararı ona bırakıp devam ettir
  if (!(await page.locator('.tool-row[data-tool="save_requirements"]').count())) {
    await chat(page, "Sen karar ver, varsayılanlarla devam et.", 600_000);
  }
  await expect(page.locator('.tool-row[data-tool="save_requirements"][data-ok="1"]')).toBeVisible();
  await expect(page.locator(".step button[aria-current='step']", { hasText: "Veri" })).toBeVisible();
  // öneri model tarafından (propose_model) ya da arama sınırında sistem tarafından hazırlanmış olabilir
  await expect(page.locator('.tool-row[data-ok="1"]').filter({ hasText: /propose_model|model önerisini hazırladı/ }).first())
    .toBeVisible({ timeout: 600_000 });
  await expect(page.locator(".composer-box textarea")).toBeEnabled({ timeout: 600_000 });   // tur bitti
  await expect(page.locator('.tool-row[data-tool="save_datasets"]')).toHaveCount(0);       // onay alınmadan dataset yok
  await expect(page.locator(".msg-assistant").last()).toContainText(/\?/);                // onay sorusu
});
