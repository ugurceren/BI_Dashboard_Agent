// Ortak yardımcılar: sohbet gönderme (SSE akışı bitene kadar bekler), yeni rapor, SQL editörüne yazma, kullanıcı bağlamı.
import { expect, type Browser, type BrowserContext, type Page } from "@playwright/test";

/** Sohbete mesaj yazar ve agent'ın turu bitene kadar bekler (gönderim sırasında giriş kutusu kilitlenir). */
export async function chat(page: Page, text: string, timeout = 180_000) {
  const box = page.locator(".composer-box textarea");
  await expect(box).toBeEnabled({ timeout });
  await box.fill(text);
  await box.press("Enter");
  await expect(page.locator(".msg-user").filter({ hasText: text }).first()).toBeVisible();
  await expect(box).toBeDisabled({ timeout: 10_000 }).catch(() => undefined);   // çok kısa turlarda kilit görülmeyebilir
  await expect(box).toBeEnabled({ timeout });
}

/** Araç adımı (ör. propose_model) sohbette başarıyla görünür mü? */
export async function expectTool(page: Page, name: string, ok = true) {
  await expect(page.locator(`.tool-row[data-tool="${name}"][data-ok="${ok ? 1 : 0}"]`).last()).toBeVisible({ timeout: 240_000 });
}

/** Tasarım modunda envanterden yeni rapor açar; rapor id'sini döner. */
export async function newReport(page: Page): Promise<string> {
  await page.goto("/#/envanter");
  await page.getByRole("button", { name: /Yeni rapor/ }).first().click();
  await page.waitForURL(/#\/r\/[A-Za-z0-9]+/);
  return /#\/r\/([A-Za-z0-9]+)/.exec(page.url())![1];
}

/** CodeMirror SQL editörünün içeriğini değiştirir. */
export async function setSql(page: Page, sql: string) {
  const ed = page.getByTestId("sql-editor").locator(".cm-content");
  await ed.click();
  await page.keyboard.press("Control+A");
  await page.keyboard.press("Delete");
  await ed.pressSequentially(sql, { delay: 0 });
}

/** Görsel hücresi (dashboard). */
export const visual = (page: Page, id: string) => page.locator(`[data-visual-id="${id}"]`);

/** Sunucu modu: IIS / Windows kimlik doğrulamasının ilettiği başlıklarla ayrı kullanıcı bağlamı. */
export async function asUser(browser: Browser, baseURL: string, user: string, groups = ""): Promise<{ ctx: BrowserContext; page: Page }> {
  const ctx = await browser.newContext({
    baseURL, locale: "tr-TR", viewport: { width: 1440, height: 900 },
    extraHTTPHeaders: { "X-Remote-User": user, ...(groups ? { "X-Remote-Groups": groups } : {}) },
  });
  return { ctx, page: await ctx.newPage() };
}
