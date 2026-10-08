// LLM'siz hızlı yol: demo dashboard yükle → görseller gerçek veriyle çizilir → dilimleyici ve görselden çapraz filtre.
import { expect, test } from "@playwright/test";
import { newReport, visual } from "./fixtures";

test("demo dashboard yüklenir, dilimleyici ve çapraz filtre çalışır", async ({ page }) => {
  await newReport(page);
  const demo = page.getByRole("button", { name: "Demo dashboard yükle" });
  await expect(demo).toBeEnabled();
  const [res] = await Promise.all([page.waitForResponse((r) => r.url().endsWith("/demo")), demo.click()]);
  expect(res.status(), await res.text()).toBe(200);
  for (const v of ["kpi_sales", "kpi_orders", "trend", "country_bar", "category_donut"]) await expect(visual(page, v)).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText("Görsel çizilemedi")).toHaveCount(0);

  const sales = visual(page, "kpi_sales").locator(".db-kpi-value");
  await expect(sales).not.toHaveText("–");
  const before = (await sales.textContent())?.trim();

  // görselden çapraz filtre: ülke çubuğuna tıklanınca diğer görseller süzülür
  const chart = visual(page, "country_bar").locator("canvas").first();
  await expect(chart).toBeVisible();
  const box = (await chart.boundingBox())!;
  await page.waitForTimeout(800);                                   // ECharts giriş animasyonu
  await chart.click({ position: { x: box.width * 0.35, y: Math.min(40, box.height / 4) } });
  await expect(sales).not.toHaveText(before ?? "", { timeout: 20_000 });

  // filtreyi temizle → değer geri gelir
  await page.getByRole("button", { name: /Temizle|Filtreleri temizle/ }).first().click();
  await expect(sales).toHaveText(before ?? "");
});
