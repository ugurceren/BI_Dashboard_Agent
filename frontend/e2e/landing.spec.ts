// Giriş sayfası: iki büyük kutu (Vitrin · Tasarım), seçim hatırlanır, logodan dönülür, dar ekranda kutular alt alta.
import { expect, test } from "./fixtures";

test("giriş sayfası: kutular, seçimin hatırlanması, logodan dönüş", async ({ page }) => {
  await page.goto("/");
  const landing = page.getByTestId("landing");
  await expect(landing).toBeVisible();                                                  // seçim yokken açılış
  await expect(page.locator(".sidebar")).toHaveCount(0);                                // tam ekran sahne
  const vitrin = landing.getByRole("button", { name: /^Vitrin/ });
  const design = landing.getByRole("button", { name: /^Tasarım/ });
  await expect(vitrin).toBeVisible();
  await expect(design).not.toHaveAttribute("aria-disabled", "true");                    // masaüstü: herkes tasarımcı
  await expect(landing).toHaveCSS("background-color", "rgb(0, 0, 0)");                  // tema ne olursa olsun koyu

  await test.step("Tasarım → envanter; yeniden açılınca doğrudan envanter", async () => {
    await design.click();
    await expect(page).toHaveURL(/#\/envanter/);
    await expect(page.getByRole("button", { name: /Yeni rapor/ }).first()).toBeVisible();
    await page.goto("/");
    await expect(page.getByRole("button", { name: /Yeni rapor/ }).first()).toBeVisible();
    await expect(landing).toHaveCount(0);
  });

  await test.step("logo → giriş sayfası; Vitrin seçilince hatırlanır", async () => {
    await page.getByRole("link", { name: "BI Lens giriş sayfası" }).click();
    await expect(page).toHaveURL(/#\/giris/);
    await expect(landing).toBeVisible();
    await vitrin.click();
    await expect(page).toHaveURL(/#\/vitrin/);
    await expect(page.locator(".home.vitrin")).toBeVisible();
    await page.goto("/");
    await expect(page.locator(".home.vitrin")).toBeVisible();
  });

  await test.step("Kontrol Paneli: Yönetim ve Bağlantı Ayarları; seçim hatırlanmaz", async () => {
    await page.goto("/#/giris");
    const control = landing.getByRole("group", { name: "Kontrol Paneli" });
    await expect(control).toBeVisible();
    await expect(control.getByText(/Dil modeli:/)).toBeVisible();
    await control.getByRole("button", { name: /Bağlantı Ayarları/ }).click();
    await expect(page).toHaveURL(/#\/settings/);
    await page.goto("/#/giris");
    await control.getByRole("button", { name: /^Yönetim/ }).click();
    await expect(page).toHaveURL(/#\/admin/);
    await page.goto("/#/giris");
    await control.locator(".ld-stage").click();                                  // kutunun kendisi: Yönetim
    await expect(page).toHaveURL(/#\/admin/);
    await page.goto("/");
    await expect(page.locator(".home.vitrin")).toBeVisible();                     // son seçim hâlâ Vitrin
  });

  await test.step("klavye: kutular Tab ile seçilip Enter ile açılır", async () => {
    await page.goto("/#/giris");
    await vitrin.focus();
    await page.keyboard.press("Tab");
    await expect(design).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/#\/envanter/);
  });
});

test("giriş sayfası dar ekranda: üç kutu alt alta, yatay kaydırma yok", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/#/giris");
  const cards = page.getByTestId("landing").locator(".ld-card");
  await expect(cards).toHaveCount(3);
  const [a, b] = [await cards.nth(0).boundingBox(), await cards.nth(1).boundingBox()];
  expect(b!.y).toBeGreaterThan(a!.y + a!.height - 1);
  const c = await cards.nth(2).boundingBox();
  expect(c!.y).toBeGreaterThan(b!.y + b!.height - 1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
});
