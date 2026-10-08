// Sunucu modu (PLATFORM_MODE=server): kimlik ters proxy başlığından (X-Remote-User / X-Remote-Groups).
// Admin rol atar → tasarımcı rapor yayınlar ve grupla paylaşır → izleyici Vitrin'de görür → grupta olmayan göremez → denetim kaydı.
import { expect, test, asUser, visual } from "./fixtures";

const BASE = "http://127.0.0.1:8092";
test.describe.configure({ mode: "serial" });

test("kimliksiz istek reddedilir", async ({ request }) => {
  expect((await request.get(`${BASE}/api/me`)).status()).toBe(401);
});

test("admin → tasarımcı → izleyici: yayın, paylaşım ve yetki", async ({ browser }) => {
  test.setTimeout(420_000);   // bu PC: demo yüklemesi (kolon profili) ilk seferde 30-90 sn sürebilir
  const admin = await asUser(browser, BASE, "KURUM\\admin");
  const ali = await asUser(browser, BASE, "KURUM\\ali");
  const veli = await asUser(browser, BASE, "KURUM\\veli", "Satis");
  const ayse = await asUser(browser, BASE, "KURUM\\ayse", "Muhasebe");
  let reportId = "";

  await test.step("admin ali'ye tasarımcı rolü verir", async () => {
    const p = admin.page;
    await p.goto("/#/admin");
    await expect(p.getByRole("heading", { name: "Yönetim" })).toBeVisible();
    await p.getByRole("combobox", { name: "Tür" }).selectOption("user");
    await p.getByRole("textbox", { name: "Kullanıcı ya da grup" }).fill("KURUM\\ali");
    await p.getByRole("combobox", { name: "Platform rolü" }).selectOption("builder");
    await p.getByRole("button", { name: "Ata" }).click();
    await expect(p.locator("table").getByText("kurum\\ali")).toBeVisible();
  });

  await test.step("ali demo raporu hazırlar ve Satis grubuyla yayınlar", async () => {
    const p = ali.page;
    await p.goto("/#/envanter");
    await expect(p.getByRole("button", { name: "Yönetim" })).toHaveCount(0);           // tasarımcı Sistem menüsünü görmez
    await p.getByRole("button", { name: /Yeni rapor/ }).first().click();
    await p.waitForURL(/#\/r\//);
    const demo = p.getByRole("button", { name: "Demo dashboard yükle" });
    const [dres] = await Promise.all([p.waitForResponse((r) => r.url().endsWith("/demo")), demo.click()]);
    expect(dres.status(), await dres.text()).toBe(200);
    await expect(visual(p, "kpi_sales")).toBeVisible({ timeout: 180_000 });
    await p.getByRole("button", { name: /^Yayınla$/ }).click();
    const dlg = p.getByRole("dialog", { name: "Vitrin'e yayınla" });
    await dlg.getByPlaceholder("Rapor neyi gösteriyor, kimin için?").fill("E2E yayın testi");
    await dlg.getByRole("textbox", { name: "Kullanıcı ya da grup adı" }).fill("Satis");
    await dlg.getByRole("button", { name: "Ekle" }).click();
    await expect(dlg.locator(".share-name", { hasText: "Satis" })).toBeVisible();
    const [res] = await Promise.all([p.waitForResponse((r) => r.url().includes("/publish")), dlg.getByRole("button", { name: "Yayınla" }).click()]);
    expect(res.status()).toBe(200);
    reportId = (await res.json()).report.id;
    await expect(p.getByRole("button", { name: /Yayında · v1/ })).toBeVisible();
  });

  await test.step("veli (Satis) yalnız Vitrin'i görür, raporu kendi yetkisiyle açar", async () => {
    const p = veli.page;
    await p.goto("/");
    const landing = p.getByTestId("landing");                                              // açılış: giriş sayfası
    await expect(landing).toBeVisible();
    const design = landing.getByRole("button", { name: /^Tasarım/ });
    await expect(design).toHaveAttribute("aria-disabled", "true");                        // tasarım yetkisi yok: kilitli
    await expect(design).toContainText("Tasarım yetkiniz yok");
    await design.click({ force: true });                                                  // aria-disabled: Playwright normalde beklerdi
    await expect(landing).toBeVisible();                                                   // kilitli kutu bir yere götürmez
    await expect(landing.getByRole("button", { name: /^Vitrin/ })).toContainText("1 rapor yayında");
    await landing.getByRole("button", { name: /^Vitrin/ }).click();
    await expect(p.locator(".home.vitrin")).toBeVisible();
    await expect(p.getByRole("navigation", { name: "Vitrin menüsü" })).toBeVisible();
    await expect(p.getByRole("button", { name: "Tasarım", exact: true })).toHaveCount(0);   // mod anahtarı yok
    await expect(p.getByRole("button", { name: "Rapor Envanteri" })).toHaveCount(0);
    const card = p.locator(".vt-card", { hasText: "E2E yayın testi" });
    await expect(card).toBeVisible();
    await expect(card.getByRole("button", { name: "Tasarımda aç" })).toHaveCount(0);
    await expect(card.getByRole("button", { name: "Paylaş" })).toHaveCount(0);
    const [res] = await Promise.all([p.waitForResponse((r) => r.url().endsWith(`/api/vitrin/${reportId}`)), card.getByRole("button", { name: "Aç" }).click()]);
    const body = await res.json();
    expect(body.spec.datasets.every((d: { sql: string }) => d.sql === "")).toBe(true);    // SQL izleyiciye gitmez
    expect(body).not.toHaveProperty("transcript");
    await expect(visual(p, "kpi_sales").locator(".db-kpi-value")).not.toHaveText("–", { timeout: 90_000 });
    await p.goto("/#/envanter");
    await expect(p).toHaveURL(/#\/vitrin/);                                              // tasarım sayfasına giremez → Vitrin
    await expect(p.locator(".home.vitrin")).toBeVisible();
    expect((await p.request.get(`${BASE}/api/sessions`)).status()).toBe(403);
  });

  await test.step("ayşe (grupta değil) raporu göremez", async () => {
    const p = ayse.page;
    await p.goto("/#/vitrin");
    await expect(p.getByText("Vitrin'de henüz rapor yok")).toBeVisible();
    expect((await p.request.get(`${BASE}/api/vitrin/${reportId}`)).status()).toBe(404);
  });

  await test.step("admin denetim kaydında yayın ve açılış olaylarını görür", async () => {
    const p = admin.page;
    await p.goto("/#/admin");
    await p.getByRole("tab", { name: "Denetim kaydı" }).click();
    const rows = p.locator("table tbody");
    await expect(rows).toContainText("Yayınlandı");
    await expect(rows).toContainText("Rapor açıldı");
    await expect(rows).toContainText("KURUM\\veli");
  });

  for (const u of [admin, ali, veli, ayse]) {
    expect(u.errors, "kullanıcı sayfasında konsol hatası").toEqual([]);
    await u.ctx.close();
  }
});
