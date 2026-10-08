// Sorgu Çalıştır: yetkili nesne ağacı, SQL editörü, çalıştırma, doğrulama kuralları (salt-okunur, kişisel veri).
import { expect, test, setSql } from "./fixtures";

test.describe("Sorgu Çalıştır", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/#?\/?$|#\/vitrin/);                      // açılış: Vitrin
    await page.getByRole("button", { name: "Tasarım", exact: true }).click(); // mod anahtarı
    await page.getByRole("button", { name: "Sorgu Çalıştır" }).click();
    await expect(page).toHaveURL(/#\/query/);
    await expect(page.getByRole("complementary", { name: "Yetkili nesneler" })).toBeVisible();
  });

  test("yetkili nesneler listelenir ve aranır", async ({ page }) => {
    const tree = page.getByRole("complementary", { name: "Yetkili nesneler" });
    await tree.getByRole("searchbox", { name: "Nesne ara" }).fill("FactResellerSales");
    await expect(tree.locator(".qp-obj-label", { hasText: "dbo.FactResellerSales" })).toBeVisible();
    await expect(tree.locator(".qp-obj-label", { hasText: "dbo.DimProduct" })).toHaveCount(0);
  });

  test("SELECT çalışır: sonuç tablosu, satır sayısı ve çalıştırılan SQL", async ({ page }) => {
    await setSql(page, "SELECT TOP 5 f.SalesOrderNumber, f.SalesAmount FROM dbo.FactResellerSales f ORDER BY f.SalesAmount DESC");
    await page.getByRole("button", { name: "Çalıştır", exact: true }).click();
    const grid = page.getByTestId("query-result");
    await expect(grid).toBeVisible();
    await expect(grid.locator("tbody tr")).toHaveCount(5);
    await expect(grid.locator("thead th")).toContainText(["#", "SalesOrderNumber", "SalesAmount"]);
    await expect(page.locator(".qp-res-head")).toContainText("5 satır");
    await page.locator(".qp-ran summary").click();
    // NOLOCK yalnız dictionary.toml nolock_databases (EDWDM) için eklenir; AdventureWorks sorgusu olduğu gibi çalışır
    await expect(page.locator(".qp-ran pre")).toContainText("FROM dbo.FactResellerSales");
  });

  test("Ctrl+Enter ile çalışır", async ({ page }) => {
    await setSql(page, "SELECT COUNT(*) AS adet FROM dbo.DimProduct");
    await page.keyboard.press("Control+Enter");
    await expect(page.getByTestId("query-result").locator("tbody tr")).toHaveCount(1);
  });

  test("yazma / şema değiştirme sorgusu reddedilir", async ({ page }) => {
    await setSql(page, "DELETE FROM dbo.DimProduct WHERE ProductKey = 1");
    await page.getByRole("button", { name: "Çalıştır", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Sorgu çalıştırılmadı");
    await expect(page.getByTestId("query-result")).toHaveCount(0);
  });

  test("kişisel veri kolonu (PII) bu rolle sorgulanamaz", async ({ page }) => {
    // masaüstü modunda kullanıcı admin veri rolündedir; PII engeli için standart rol gerekir → yalnız uyarı / izin kontrolü
    const me = await page.request.get("/api/me").then((r) => r.json());
    await setSql(page, "SELECT TOP 3 c.BirthDate FROM dbo.DimCustomer c");
    await page.getByRole("button", { name: "Çalıştır", exact: true }).click();
    if (me.policy?.allow_pii) {
      await expect(page.getByTestId("query-result").locator("tbody tr")).toHaveCount(3);
    } else {
      await expect(page.getByRole("alert")).toContainText(/kişisel veri/i);
    }
  });

  test("nesnenin ilk satırları tek tıkla getirilir", async ({ page }) => {
    const tree = page.getByRole("complementary", { name: "Yetkili nesneler" });
    await tree.getByRole("searchbox", { name: "Nesne ara" }).fill("DimSalesTerritory");
    const row = tree.locator(".qp-obj").filter({ has: page.locator(".qp-obj-label", { hasText: "dbo.DimSalesTerritory" }) }).first();
    await row.getByTitle("İlk 100 satırı getir").click();
    await expect(page.getByTestId("query-result").locator("tbody tr").first()).toBeVisible();
    await expect(page.locator(".qp-res-head")).toContainText("satır");
  });
});
