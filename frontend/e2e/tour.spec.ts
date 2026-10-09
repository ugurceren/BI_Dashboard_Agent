// Tanıtım: açılış sayfasından sunum (klavye, adres, konuşmacı notu, görseller) ve kullanım kılavuzu (rol filtresi, arama,
// bölüm adresi, sol menüden Yardım).
import { expect, test } from "./fixtures";

test("sunum: açılış sayfasından açılır, klavyeyle gezilir, görseller yüklenir, Esc ile çıkılır", async ({ page }) => {
  await page.goto("/#/giris");
  await page.getByRole("button", { name: /Tanıtımı izle/ }).click();
  const deck = page.getByTestId("presentation");
  await expect(deck).toBeVisible();
  await expect(page).toHaveURL(/#\/tanitim\/1$/);
  const count = page.getByTestId("slide-count");
  const total = Number((await count.textContent())!.split("/")[1]);
  expect(total).toBeGreaterThanOrEqual(10);

  for (let n = 1; n <= total; n++) {                                             // tüm slaytlar: görsel yüklenmiş olmalı
    await expect(count).toHaveText(`${n} / ${total}`);
    await expect(page).toHaveURL(new RegExp(`#/tanitim/${n}$`));
    const img = deck.locator(".tr-slide img");
    if (await img.count()) {
      await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth), { message: `slayt ${n} görseli` })
        .toBeGreaterThan(0);
    }
    if (n < total) await page.keyboard.press("ArrowRight");
  }
  await page.keyboard.press("ArrowRight");                                       // son slaytta ilerlemez
  await expect(count).toHaveText(`${total} / ${total}`);
  await page.keyboard.press("Home");
  await expect(count).toHaveText(`1 / ${total}`);
  await page.keyboard.press("n");
  await expect(page.getByRole("complementary", { name: "Konuşmacı notu" })).toBeVisible();
  await page.goto("/#/tanitim/3");                                               // paylaşılabilir slayt adresi
  await expect(count).toHaveText(`3 / ${total}`);
  await deck.locator(".tr-slide .tr-shot-btn").click();                          // görsel büyür, Esc yalnız onu kapatır
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(deck).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/#\/giris/);
});

test("kılavuz: rol filtresi, arama, bölüm adresi, sol menüden Yardım", async ({ page }) => {
  await page.goto("/#/giris");
  await page.getByRole("button", { name: "Kullanım kılavuzu" }).click();
  const guide = page.getByTestId("guide");
  await expect(guide).toBeVisible();
  const nav = page.getByRole("complementary", { name: "Kılavuz bölümleri" });
  const items = nav.locator(".tr-nav-item");
  const all = await items.count();
  expect(all).toBeGreaterThanOrEqual(10);

  await nav.getByRole("button", { name: "Herkes", exact: true }).click();       // yalnız herkese açık bölümler
  const herkes = await items.count();
  expect(herkes).toBeLessThan(all);
  await expect(items.filter({ hasText: "Bağlantı ayarları" })).toHaveCount(0);
  await nav.getByRole("button", { name: "Yönetici", exact: true }).click();
  await expect(items.filter({ hasText: "Bağlantı ayarları" })).toHaveCount(1);
  await nav.getByRole("button", { name: "Tümü", exact: true }).click();

  await nav.getByRole("searchbox", { name: "Kılavuzda ara" }).fill("lacivert");   // içerikte arar
  await expect(items).toHaveCount(1);
  await items.first().click();
  await expect(page).toHaveURL(/#\/kilavuz\/tasarim-sohbet/);
  await expect(guide.getByRole("heading", { name: "Tasarımı sohbetle düzenleme" })).toBeVisible();
  await nav.getByRole("searchbox", { name: "Kılavuzda ara" }).fill("");

  await page.goto("/#/kilavuz/prod");                                           // bölüm adresi doğrudan açılır
  await expect(guide.getByRole("heading", { name: /Üretime \(prod\) kurulum/ })).toBeVisible();
  await page.reload();                                                          // ilk açılışta da bölüme kayar (başa atmaz)
  await expect.poll(() => guide.locator(".tr-guide-main").evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
  await expect(page).toHaveURL(/#\/kilavuz\/prod/);
  await expect(guide.getByRole("heading", { name: "Grafik ve tablo türleri" })).toBeAttached();
  for (const img of await guide.locator("#g-sec-prod img").all()) {
    await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth)).toBeGreaterThan(0);
  }
  await expect(guide.getByRole("heading", { name: "Sorun giderme ve SSS" })).toBeAttached();   // bölümler alt alta tek sayfada
  const main = guide.locator(".tr-guide-main");
  const toTop = guide.getByRole("button", { name: "Başa dön" });
  await main.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));          // sürükleyerek sona: menü ve adres güncellenir
  await expect(page).toHaveURL(/#\/kilavuz\/sss/);
  await expect(nav.locator(".tr-nav-item.is-on")).toContainText("Sorun giderme");
  await expect(toTop).toHaveClass(/is-on/);
  await toTop.click();
  await expect.poll(() => main.evaluate((el) => el.scrollTop)).toBe(0);
  await nav.getByRole("button", { name: /Üretime \(prod\)/ }).click();          // menüden bölüme atlar
  await expect(page).toHaveURL(/#\/kilavuz\/prod/);
  await expect.poll(() => main.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);

  await page.goto("/#/envanter");                                                // uygulama içinden: sol menü → Yardım
  await page.getByRole("button", { name: "Yardım" }).click();
  await expect(guide).toBeVisible();
});

test("tanıtım dar ekranda yatay kaydırma yapmaz", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  for (const url of ["/#/tanitim/4", "/#/kilavuz/baslarken"]) {
    await page.goto(url);
    await expect(page.locator(".tr-present, .tr-guide").first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth), url).toBeLessThanOrEqual(375);
  }
});
