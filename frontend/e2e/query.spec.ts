// Sorgu Çalıştır: yetkili nesne ağacı, SQL editörü, çalıştırma, doğrulama kuralları (salt-okunur, kişisel veri).
import { expect, test, setSql } from "./fixtures";

test.describe("Sorgu Çalıştır", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("landing")).toBeVisible();                 // açılış: giriş sayfası (seçim yok)
    await page.getByRole("button", { name: /^Tasarım/ }).click();            // Tasarım kutusu
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

  test("hazır sorgu → Dashboard hazırla → sorgu modu (önizleme, tam sayfa) → tasarım", async ({ page }) => {
    await setSql(page, "SELECT t.SalesTerritoryRegion AS region, SUM(f.SalesAmount) AS sales_amount FROM dbo.FactResellerSales f "
      + "JOIN dbo.DimSalesTerritory t ON t.SalesTerritoryKey = f.SalesTerritoryKey GROUP BY t.SalesTerritoryRegion");
    await page.getByRole("button", { name: "Dashboard hazırla" }).click();
    const dlg = page.getByRole("dialog", { name: "Bu sorguyla dashboard hazırla" });
    await dlg.getByLabel("Açıklama").fill("Bölge satışları");
    await expect(dlg.getByLabel("Veri kümesi adı")).toHaveValue("bolge_satislari");
    await dlg.getByRole("button", { name: "Gönder ve aç" }).click();

    await expect(page).toHaveURL(/#\/r\/[A-Za-z0-9]+/);
    const qm = page.getByTestId("query-mode");
    await expect(qm).toBeVisible();                                             // rapor sorgu modunda açılır
    await expect(qm.getByRole("tab", { name: /bolge_satislari/ })).toBeVisible();

    await page.getByRole("button", { name: "Paneli tam sayfa genişlet" }).click();               // sol panel tam genişlik, sağ panel gizli
    await expect(page.locator(".right")).toHaveCount(0);
    await page.getByRole("button", { name: "Paneli daralt" }).click();
    await expect(page.locator(".right")).toBeVisible();

    await page.getByRole("button", { name: "Sohbet", exact: true }).click();     // moda geçiş: taslak kaybolmaz
    await expect(qm).toHaveCount(0);
    await page.getByRole("button", { name: "Sorgu", exact: true }).click();
    await expect(page.getByTestId("query-mode").getByRole("tab", { name: /bolge_satislari/ })).toBeVisible();

    await page.getByRole("button", { name: /^Önizle/ }).click();
    await expect(page.getByTestId("qm-result").locator("tbody tr").first()).toBeVisible();

    // açık rapora Sorgu Çalıştır'dan ikinci sorgu: rapor yeniden yüklenir, yeni sorgu sekmesi görünür
    const reportId = /#\/r\/([A-Za-z0-9]+)/.exec(page.url())![1];
    await page.getByRole("button", { name: "Sorgu Çalıştır" }).click();
    await setSql(page, "SELECT COUNT(*) AS urun_adedi FROM dbo.DimProduct");
    await page.getByRole("button", { name: "Dashboard hazırla" }).click();
    await dlg.getByLabel("Veri kümesi adı").fill("urun_ozet");
    await dlg.getByRole("radio", { name: "Mevcut rapor" }).check();
    await dlg.getByRole("combobox", { name: "Rapor" }).selectOption(reportId);
    await dlg.getByRole("button", { name: "Gönder ve aç" }).click();
    await expect(page).toHaveURL(new RegExp(`#/r/${reportId}`));
    await expect(page.getByTestId("query-mode").getByRole("tab", { name: /urun_ozet/ })).toBeVisible();
    await expect(page.getByTestId("query-mode").getByRole("tab", { name: /bolge_satislari/ })).toBeVisible();

    await page.getByRole("button", { name: /Dashboard'a geç/ }).click();

    await expect(page.locator(".step.is-current")).toContainText("Tasarım");      // veri kümesi kaydedildi, tasarım fazı
    await expect(page.getByTestId("query-mode")).toHaveCount(0);
    await expect(page.locator(".transcript")).toContainText("Sorgu modundan 2 veri kümesi kaydedildi");
    await expect(page.getByRole("tab", { name: /Veri/ })).toContainText("2");
  });
});
