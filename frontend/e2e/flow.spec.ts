// İhtiyaç → Veri → Tasarım akışının hata ve ret yolları ("[detay]" senaryosu, e2e/fake_llm.py).
// Model bilerek hata yapar: KPI'sız gereksinim, onaysız ilişki kaydı, hatalı / yazma SQL'i, kısmen hatalı dataset,
// bilinmeyen kolonlu spec. Harness'in bunları yakalaması, arayüzün durumu doğru göstermesi ve akışın yine tamamlanması
// test edilir. Araçlar, SQL doğrulaması ve veritabanı (AdventureWorksDW) gerçektir.
import { expect, test, chat, expectTool, newReport, visual } from "./fixtures";

test.describe.configure({ mode: "serial" });

test("ihtiyaç, veri ve tasarım fazlarında hatalar yakalanır, kullanıcı onayı beklenir, akış tamamlanır", async ({ page }) => {
  test.setTimeout(900_000);   // bu PC: soğuk veritabanında tek bir adım dakikalar sürebiliyor
  const id = await newReport(page);
  const NAME = `Bayi Takip Panosu ${Date.now() % 100000}`;   // aynı backend'de tekrar koşulabilsin (ad benzersiz olmalı)
  const current = (name: string) => page.locator(".step button[aria-current='step']", { hasText: name });
  const stepBtn = (name: string) => page.locator(".stepper button", { hasText: name });
  const tool = (name: string) => page.locator(`.tool-row[data-tool="${name}"]`);
  const session = async () => (await page.request.get(`/api/sessions/${id}`)).json();
  const tab = (name: string) => page.getByRole("tab", { name: new RegExp(`^${name}`) });

  await test.step("İhtiyaç: KPI'sız gereksinim reddedilir, faz ilerlemez, agent KPI sorar", async () => {
    await chat(page, "[detay] Bayi satışlarını izleyeceğim bir rapor istiyorum.");
    await expectTool(page, "save_requirements", false);
    await expect(tool("save_requirements").last().locator(".tool-summary")).toContainText("KPI eksik");
    await expect(page.locator(".msg-assistant").last()).toContainText("Hangi KPI");
    await expect(current("İhtiyaç")).toBeVisible();
    await expect(stepBtn("Veri")).toBeDisabled();                                  // gereksinim yok: ileri gidilemez
    const res = await page.request.post(`/api/sessions/${id}/phase`, { data: { phase: "data" } });
    expect(res.status(), "gereksinimsiz veri fazına geçiş").toBe(409);
    expect((await session()).phase).toBe("requirements");
  });

  await test.step("İhtiyaç: kullanıcı raporu adlandırır (agent bu adı değiştirmemeli)", async () => {
    await page.getByRole("button", { name: "Adlandır" }).click();
    await page.getByLabel("Rapor adı").fill(NAME);
    await page.getByRole("button", { name: "Kaydet" }).click();
    await expect(page.locator(".session-title")).toHaveText(NAME);
  });

  await test.step("İhtiyaç: KPI'lar verilince kayıt olur, kullanıcının verdiği ad korunur, Veri fazında agent önce tablo önerir", async () => {
    await chat(page, "Satış tutarı ve sipariş sayısı.");
    await expectTool(page, "save_requirements");
    await expect(page.locator(".session-title")).toHaveText(NAME);
    await expect(current("Veri")).toBeVisible();
    await expectTool(page, "search_dictionary");
    await expectTool(page, "propose_model");
  });

  await test.step("Veri: onaysız ilişki kaydı reddedilir, agent sorar ve bekler", async () => {
    await expectTool(page, "save_relationships", false);
    await expect(tool("save_relationships").last().locator(".tool-summary")).toContainText("onay");
    await expect(page.locator(".msg-assistant").last()).toContainText("devam edeyim mi");
    await expect(page.locator('.tool-row[data-tool="save_relationships"][data-ok="1"]')).toHaveCount(0);
    await expect(tool("run_sql")).toHaveCount(0);                                   // onaydan önce SQL yok
    const s = await session();
    expect(s.phase).toBe("data");
    expect(s.datasets ?? []).toHaveLength(0);
  });

  await test.step("Veri: kullanıcı reddeder → yeni öneri, yine kayıt yok", async () => {
    await chat(page, "Hayır, DimReseller da eklensin.");
    await expect(tool("propose_model")).toHaveCount(2);
    await expect(tool("propose_model").last()).toHaveAttribute("data-ok", "1");
    await expect(tool("propose_model").last().locator(".tool-summary")).toContainText("4 tablo");
    await expect(page.locator('.tool-row[data-tool="save_relationships"][data-ok="1"]')).toHaveCount(0);
    await expect(page.locator(".msg-assistant").last()).toContainText("devam edeyim mi");
  });

  await test.step("Veri: onay (ortak model) → hatalı ve yazma SQL'i reddedilir → kısmi dataset kaydı, faz ilerlemez", async () => {
    await chat(page, "Evet, ortak modele kaydet.");
    await expectTool(page, "save_relationships");
    await expect(tool("save_relationships").last().locator(".tool-summary")).toContainText("ortak modele");
    await expect(tool("run_sql")).toHaveCount(3);
    const runs = tool("run_sql");
    await expect(runs.nth(0)).toHaveAttribute("data-ok", "0");                      // olmayan kolon
    await expect(runs.nth(1)).toHaveAttribute("data-ok", "0");                      // DELETE
    await expect(runs.nth(2)).toHaveAttribute("data-ok", "1");
    await expectTool(page, "save_datasets", false);
    await expect(tool("save_datasets").last().locator(".tool-summary")).toContainText("1 kaydedildi, 1 hatalı");
    await expect(current("Veri")).toBeVisible();                                    // eksik veriyle tasarıma geçilmez
    await expect(page.locator(".msg-assistant").last()).toContainText("kaydedilemedi");
    const s = await session();
    expect(s.datasets.map((d: { id: string }) => d.id)).toEqual(["kpi"]);
  });

  await test.step("Veri sekmesi: kaydedilen dataset taslak olarak, SQL'i ve önizlemesiyle görünür; sözlük araması çalışır", async () => {
    await tab("Veri").click();
    const card = page.locator(".ds-card", { has: page.locator("code", { hasText: /^kpi$/ }) });
    await expect(card).toBeVisible();
    await expect(card.locator(".pill-muted", { hasText: "taslak" })).toBeVisible();
    await expect(page.locator(".ds-card")).toHaveCount(1);
    await card.getByRole("button", { name: "SQL" }).click();
    await expect(card.locator("pre.sql")).toContainText("FactResellerSales");
    await expect(card.locator("table.preview tbody tr")).toHaveCount(1, { timeout: 120_000 });
    await expect(card.locator("table.preview tbody td").first()).not.toHaveText(/null|^$/);
    await page.getByPlaceholder(/Veri sözlüğünde ara/).fill("bayi");
    await expect(page.locator(".dict-hit", { hasText: "DimReseller" })).toBeVisible();
    await page.getByPlaceholder(/Veri sözlüğünde ara/).fill("");
  });

  await test.step("Veri: düzeltilen datasetler kaydedilir, JOIN'den ilişki çıkar, Tasarım fazına geçilir", async () => {
    await chat(page, "Düzeltip kaydet.");
    await expect(page.locator('.tool-row[data-tool="save_datasets"][data-ok="1"]')).toBeVisible({ timeout: 240_000 });
    await expect(current("Tasarım")).toBeVisible();
    await expect(page.locator(".msg-assistant").last()).toContainText("Nasıl bir tasarım");
    await expect(tab("Veri")).toContainText("3");
    const s = await session();
    expect(s.datasets.map((d: { id: string }) => d.id).sort()).toEqual(["kpi", "region", "yearly"]);
  });

  await test.step("Model sekmesi: ortak modele kaydedilen ve SQL JOIN'den çıkan ilişkiler görünür", async () => {
    await tab("Model").click();
    await page.getByLabel("Tablo ara").fill("DimDate");
    await page.locator(".er-search-hits button").first().click();
    const details = page.getByRole("complementary", { name: "Tablo ayrıntıları" });
    await expect(details).toBeVisible();
    const approved = details.locator(".er-d-rels > li", { hasText: "Onaylı model ilişkisi" });   // kullanıcı onaylı, ortak
    await expect(approved).toHaveCount(1);
    await expect(approved).toContainText("FullDateAlternateKey = FactResellerSales.DueDate");
    const joined = details.locator(".er-d-rels > li", { hasText: "SQL JOIN'den (bu rapora özel)" });   // yearly SQL'inden
    await expect(joined).toHaveCount(1);
    await expect(joined).toContainText("FullDateAlternateKey = FactResellerSales.OrderDate");
  });

  await test.step("Faz adımları: Veri'ye geri dönülür, kayıtlı içerikle Tasarım'a ileri geçilir, veri kaybolmaz", async () => {
    const dialogs: string[] = [];
    page.on("dialog", (d) => { dialogs.push(d.message()); void d.accept(); });
    await stepBtn("Veri").click();
    await expect(current("Veri")).toBeVisible();
    await expect(stepBtn("Tasarım")).toBeEnabled();
    await stepBtn("Tasarım").click();
    expect(dialogs).toEqual(['"Veri" fazına geri dönülsün mü? Agent bu fazdan devam edecek.',
      '"Tasarım" fazına geçilsin mi? Kayıtlı içerikle devam edilecek.']);
    await expect(current("Tasarım")).toBeVisible();
    const s = await session();
    expect(s.phase).toBe("design");
    expect(s.datasets).toHaveLength(3);
  });

  await test.step("Tasarım: bilinmeyen kolonlu spec reddedilir, düzeltilmiş spec çizilir", async () => {
    await chat(page, "Varsayılan tasarımla başla.");
    await expect(tool("create_report_spec")).toHaveCount(2, { timeout: 240_000 });
    await expect(tool("create_report_spec").first()).toHaveAttribute("data-ok", "0");
    await expect(tool("create_report_spec").last()).toHaveAttribute("data-ok", "1");
    await tab("Dashboard").click();
    for (const v of ["d_sales", "d_orders", "d_region", "d_year"]) await expect(visual(page, v)).toBeVisible({ timeout: 120_000 });
    await expect(page.getByText("Görsel çizilemedi")).toHaveCount(0);
    await expect(visual(page, "d_sales").locator(".db-kpi-value")).not.toHaveText("–");
    await expect(visual(page, "d_year").locator("canvas, svg").first()).toBeVisible();
    expect((await session()).spec.title, "kullanıcının verdiği ad dashboard başlığında da korunur").toBe(NAME);
    await expect(page.locator(".db-title, .db-header h1").first()).toHaveText(NAME);
  });

  await test.step("Tasarım: yeni veri kümesi ve görsel sohbetle eklenir", async () => {
    await chat(page, "Bayi türüne göre satışı da göster.");
    await expectTool(page, "add_dataset");
    await expectTool(page, "add_visual");
    await expect(visual(page, "d_type")).toBeVisible({ timeout: 120_000 });
    await expect(visual(page, "d_type").locator("canvas, svg").first()).toBeVisible();
    await expect(tab("Veri")).toContainText("4");
  });

  await test.step("Tasarım: sohbetle eklenen filtre çalışır (ilişki üzerinden KPI'ya yayılır)", async () => {
    await chat(page, "Bayi türü filtresi ekle.");
    await expectTool(page, "update_report");
    const filter = page.locator(".db-filter", { hasText: "Bayi Türü" });
    await expect(filter).toBeVisible();
    const kpi = visual(page, "d_orders").locator(".db-kpi-value");
    await expect(kpi).not.toHaveText("–");
    const before = (await kpi.textContent())?.trim();
    await filter.getByRole("button").first().click();
    await page.getByRole("option", { name: "Warehouse" }).click();
    await page.locator(".db-pop-foot").getByRole("button", { name: "Kapat" }).click();
    await expect(filter.locator(".db-select-text")).toHaveText("Warehouse");
    await expect(kpi).not.toHaveText(before ?? "");
    await expect(page.locator(".db-badge", { hasText: "Filtre dışı" })).toHaveCount(0);           // bayi türü tüm datasetlere yayılır
    await expect(page.locator(".db-filter", { hasText: "Bölge Grubu" })).toBeVisible();          // ilk filtre korunur
  });

  await test.step("Sayfa yenilenince faz, sohbet ve dashboard korunur", async () => {
    await page.reload();
    await expect(current("Tasarım")).toBeVisible();
    await expect(page.locator(".msg-user", { hasText: "[detay]" })).toBeVisible();
    await expect(tool("save_requirements")).toHaveCount(2);
    for (const v of ["d_sales", "d_type"]) await expect(visual(page, v)).toBeVisible({ timeout: 120_000 });
    await expect(page.locator(".session-title")).toHaveText(NAME);
  });
});
