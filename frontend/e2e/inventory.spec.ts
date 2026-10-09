// Rapor envanteri akıllı tablo (liste görünümü): başlıktan sıralama, kolon filtreleri, kolon göster-gizle (hatırlanır), CSV.
import { readFileSync } from "node:fs";
import { expect, test } from "./fixtures";

test("rapor envanteri akıllı tablo: sıralama, filtre, kolonlar, CSV", async ({ page, request }) => {
  test.setTimeout(300_000);
  const tag = `ENV${Date.now() % 100000}`;
  const make = async (title: string, status: string, demo = false) => {
    const s = await (await request.post("/api/sessions", { data: {} })).json();
    if (demo) expect((await request.post(`/api/sessions/${s.id}/demo`, { timeout: 240_000 })).status()).toBe(200);
    expect((await request.put(`/api/sessions/${s.id}/title`, { data: { title } })).status()).toBe(200);
    expect((await request.put(`/api/sessions/${s.id}/status`, { data: { status } })).status()).toBe(200);
  };
  await make(`${tag} Bravo`, "test");
  await make(`${tag} Alfa`, "design", true);   // "Canlıda" yalnız Yayınla ile verilir   // demo: görsel ve veri kümesi sayısı olan tek rapor
  await make(`${tag} Charlie`, "idea");

  await page.addInitScript(() => { try { localStorage.setItem("bi.inventoryView", "list"); } catch { /* yoksay */ } });
  await page.goto("/#/envanter");
  await page.getByRole("searchbox", { name: "Rapor ara" }).fill(tag);       // diğer testlerin raporlarını ayır
  const table = page.locator("table.rl-smart");
  const names = () => table.locator("tbody .rl-name").allTextContents();
  await expect(table.locator("tbody tr")).toHaveCount(3);
  await expect(page.locator(".sm-total")).toHaveText("3 rapor");
  const header = (label: string) => table.locator("thead th", { has: page.getByRole("button", { name: `${label} kolonuna göre sırala` }) });

  await test.step("başlıktan sıralama: Rapor (A→Z, Z→A), Görsel (çoktan aza)", async () => {
    await page.getByRole("button", { name: "Rapor kolonuna göre sırala" }).click();
    await expect(header("Rapor")).toHaveAttribute("aria-sort", "ascending");
    expect(await names()).toEqual([`${tag} Alfa`, `${tag} Bravo`, `${tag} Charlie`]);
    await page.getByRole("button", { name: "Rapor kolonuna göre sırala" }).click();
    await expect(header("Rapor")).toHaveAttribute("aria-sort", "descending");
    expect(await names()).toEqual([`${tag} Charlie`, `${tag} Bravo`, `${tag} Alfa`]);
    await page.getByRole("button", { name: "Görsel kolonuna göre sırala" }).click();
    await expect(header("Görsel")).toHaveAttribute("aria-sort", "descending");
    expect((await names())[0]).toBe(`${tag} Alfa`);
    await page.getByRole("button", { name: "Statü kolonuna göre sırala" }).click();   // Fikir → Tasarım → Test → Canlıda
    expect(await names()).toEqual([`${tag} Charlie`, `${tag} Alfa`, `${tag} Bravo`]);
  });

  await test.step("kolon filtreleri: statü değer listesi ve rapor adında metin", async () => {
    await page.getByRole("button", { name: "Statü filtresi" }).click();
    const pop = page.getByRole("dialog", { name: "Statü filtresi" });
    await expect(pop).toBeVisible();
    const box = await pop.boundingBox();
    expect(box!.height, "filtre paneli tablo kutusunda kesilmemeli").toBeGreaterThan(60);
    await pop.getByRole("checkbox", { name: /Test/ }).check();
    await pop.getByRole("checkbox", { name: /Tasarım/ }).check();
    await expect(table.locator("tbody tr")).toHaveCount(2);
    await expect(page.locator(".sm-total")).toHaveText("2 / 3 rapor");
    await page.keyboard.press("Escape");
    await expect(pop).toHaveCount(0);
    await page.getByRole("button", { name: "Rapor filtresi" }).click();
    await page.getByRole("textbox", { name: "Rapor içinde ara" }).fill("alfa");
    await expect(table.locator("tbody tr")).toHaveCount(1);
    await page.mouse.click(5, 5);                                               // dışarı tıklayınca kapanır
    await page.getByRole("button", { name: /Filtreleri temizle \(2\)/ }).click();
    await expect(table.locator("tbody tr")).toHaveCount(3);
  });

  await test.step("kolon göster-gizle, sayfa yenilenince korunur", async () => {
    await page.getByRole("button", { name: "Kolonlar" }).click();
    const pop = page.getByRole("dialog", { name: "Kolonlar" });
    await pop.getByRole("checkbox", { name: "Hedef kitle" }).uncheck();
    await pop.getByRole("checkbox", { name: "Faz" }).check();
    await expect(pop.getByRole("checkbox", { name: "Rapor" })).toBeDisabled();      // ad kolonu gizlenemez
    await page.keyboard.press("Escape");
    await expect(table.getByRole("button", { name: "Hedef kitle kolonuna göre sırala" })).toHaveCount(0);
    await expect(table.getByRole("button", { name: "Faz kolonuna göre sırala" })).toBeVisible();
    await page.reload();
    await page.getByRole("searchbox", { name: "Rapor ara" }).fill(tag);
    await expect(table.getByRole("button", { name: "Faz kolonuna göre sırala" })).toBeVisible();
    await expect(table.getByRole("button", { name: "Hedef kitle kolonuna göre sırala" })).toHaveCount(0);
    await expect(header("Statü")).toHaveAttribute("aria-sort", "ascending");       // sıralama da hatırlandı
  });

  await test.step("CSV indir: görünen satır ve kolonlar, Excel için ; ayraç", async () => {
    const [dl] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "CSV indir" }).click()]);
    expect(dl.suggestedFilename()).toMatch(/^rapor-envanteri-\d{4}-\d{2}-\d{2}\.csv$/);
    const text = readFileSync(await dl.path(), "utf8");
    const lines = text.replace(/^﻿/, "").trim().split(/\r?\n/);
    expect(lines[0]).toContain("Statü;Rapor;Amaç;Faz");
    expect(lines).toHaveLength(4);
    expect(text).toContain(`${tag} Alfa`);
    expect(text).not.toContain("Hedef kitle");
  });

  await page.getByRole("button", { name: "Kolonlar" }).click();                  // sonraki testler için varsayılan
  await page.getByRole("dialog", { name: "Kolonlar" }).getByRole("button", { name: "Varsayılana dön" }).click();
});
