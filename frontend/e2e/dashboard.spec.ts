// Dashboard oluşturma: sohbetle İhtiyaç → Veri (model onayı) → Tasarım → dashboard → filtre → HTML → Canlı → stil değişikliği.
// LLM senaryolu sahte sunucudur (e2e/fake_llm.py); araçlar, SQL doğrulaması ve veritabanı (AdventureWorksDW) gerçektir.
import { expect, test } from "@playwright/test";
import { chat, expectTool, newReport, visual } from "./fixtures";

test.describe.configure({ mode: "serial" });

test("sohbetle dashboard oluşturulur, filtrelenir, dışa aktarılır ve güncellenir", async ({ page, context }) => {
  test.setTimeout(600_000);   // bu PC: soğuk veritabanında tek bir adım dakikalar sürebiliyor
  const id = await newReport(page);
  const step = (name: string) => page.locator(".step button[aria-current='step']", { hasText: name });

  await test.step("İhtiyaç → Veri: agent tabloları ve ilişki önerisini sorar", async () => {
    await expect(step("İhtiyaç")).toBeVisible();
    await chat(page, "Bayi satışlarını bölge ve aya göre gösteren bir dashboard istiyorum.");
    await expectTool(page, "save_requirements");
    await expectTool(page, "search_dictionary");
    await expectTool(page, "propose_model");
    await expect(step("Veri")).toBeVisible();
    const ask = page.locator(".msg-assistant").last();
    await expect(ask).toContainText("dbo.FactResellerSales");
    await expect(ask).toContainText("devam edeyim mi");
    await expect(ask).toContainText("yalnız bu rapora");
    await expect(page.locator('.tool-row[data-tool="save_relationships"]')).toHaveCount(0);   // onay olmadan kayıt yok
  });

  await test.step("Onay → ilişki (rapora özel) → veri → tasarım → dashboard", async () => {
    await chat(page, "Evet, yalnız bu rapora kaydet.");
    await expectTool(page, "save_relationships");
    await expect(page.locator('.tool-row[data-tool="save_relationships"] .tool-summary')).toContainText("rapor modeline");
    await expectTool(page, "run_sql");
    await expectTool(page, "save_datasets");
    await expectTool(page, "create_report_spec");
    await expect(step("Tasarım")).toBeVisible();
    for (const v of ["k_sales", "k_orders", "b_region", "l_month"]) await expect(visual(page, v)).toBeVisible({ timeout: 90_000 });
    await expect(page.getByText("Görsel çizilemedi")).toHaveCount(0);
    await expect(visual(page, "k_sales").locator(".db-kpi-value")).not.toHaveText("–");
    await expect(visual(page, "b_region").locator("canvas, svg").first()).toBeVisible();
  });

  await test.step("Bölge Grubu filtresi KPI'yı değiştirir", async () => {
    const kpi = visual(page, "k_orders").locator(".db-kpi-value");
    const before = (await kpi.textContent())?.trim();
    const filter = page.locator(".db-filter", { hasText: "Bölge Grubu" });
    await filter.getByRole("button").first().click();
    await page.getByRole("option", { name: "Europe" }).click();
    await page.locator(".db-pop-foot").getByRole("button", { name: "Kapat" }).click();
    await expect(filter.locator(".db-select-text")).toHaveText("Europe");
    await expect(kpi).not.toHaveText(before ?? "");
    await expect(page.locator(".db-badge", { hasText: "Filtre dışı" })).toHaveCount(0);   // filtre tüm görsellere uygulanır
  });

  await test.step("HTML indir: bağımsız dosya rapor verisini içerir", async () => {
    // düğme dışa aktarma adresini yeni sekmede açar (tarayıcı dosyayı indirir); içerik aynı adresten doğrulanır
    const popup = context.waitForEvent("request", (r) => /\/api\/sessions\/[A-Za-z0-9]+\/export\/html$/.test(r.url()));
    await page.getByRole("button", { name: /HTML indir/ }).click();
    const url = (await popup).url();
    expect(url).toContain(`/api/sessions/${id}/export/html`);
    const res = await page.request.get(url);
    expect(res.status()).toBe(200);
    expect(res.headers()["content-disposition"]).toMatch(/attachment; filename=.*\.html/);
    const text = await res.text();
    expect(text).toContain('id="report-data"');
    expect(text).toContain("E2E Bayi Sat");
    for (const p of context.pages()) if (p !== page) await p.close().catch(() => undefined);
  });

  await test.step("Canlı görünüm tam sayfa dashboard'u gösterir", async () => {
    await page.getByRole("group", { name: "Rapor görünümü" }).getByRole("button", { name: "Canlı" }).click();
    await expect(page).toHaveURL(new RegExp(`#/v/${id}`));
    await expect(visual(page, "k_sales")).toBeVisible();
    await page.getByRole("group", { name: "Rapor görünümü" }).getByRole("button", { name: "Tasarım" }).click();
    await expect(page).toHaveURL(new RegExp(`#/r/${id}`));
  });

  await test.step("Stil isteği: KPI rengi değişir", async () => {
    await chat(page, "Satış Tutarı KPI'sını yeşil yap.");
    await expectTool(page, "update_visual");
    await expect(visual(page, "k_sales").locator(".db-kpi-value")).toHaveCSS("color", "rgb(22, 163, 74)");
  });
});
