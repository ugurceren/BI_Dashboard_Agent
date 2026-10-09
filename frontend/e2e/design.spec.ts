// Dashboard tasarımı: tüm görsel türleri, yerleşim çakışması, sayfalar, Spec sekmesi, LLM ile düzenlemeler,
// tema / KPI stili / sayı biçimleri. Raporlar API ile hazırlanır (demo dashboard + spec düzenleme; yeni SQL yok →
// soğuk veritabanı beklemesi yok), sonra arayüzde doğrulanır. Konsol / sayfa hataları testi düşürür (fixtures).
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import type { APIRequestContext, Locator, Page } from "@playwright/test";
import { chat, expect, expectTool, test, visual } from "./fixtures";

type Spec = { title: string; visuals: Record<string, unknown>[]; pages?: { id: string; title: string }[]; [k: string]: unknown };

/** Demo dashboard'lu yeni rapor; isteğe bağlı olarak spec'i değiştirip uygular. */
async function seed(request: APIRequestContext, edit?: (s: Spec) => Spec | void): Promise<{ id: string; spec: Spec }> {
  const id = (await (await request.post("/api/sessions", { data: {} })).json()).id as string;
  const demo = await request.post(`/api/sessions/${id}/demo`);
  expect(demo.status(), await demo.text()).toBe(200);
  let spec = (await demo.json()).spec as Spec;
  if (edit) {
    const copy = structuredClone(spec);
    spec = edit(copy) ?? copy;
    const r = await request.put(`/api/sessions/${id}/spec`, { data: spec });
    expect(r.status(), await r.text()).toBe(200);
    spec = (await r.json()).spec;
  }
  return { id, spec };
}

async function open(page: Page, id: string) {
  await page.goto(`/#/r/${id}`);
  await expect(page.locator("[data-visual-id]").first()).toBeVisible({ timeout: 120_000 });
}

const noVisualErrors = (page: Page) => expect(page.getByText("Görsel çizilemedi")).toHaveCount(0);

/** Görünür görsel hücreleri birbirine binmemeli (yerleşim / ızgara hatası). */
async function expectNoOverlap(page: Page) {
  const cells = page.locator("[data-visual-id]:visible");
  const boxes: { id: string; x: number; y: number; w: number; h: number }[] = [];
  for (let i = 0; i < await cells.count(); i++) {
    const b = await cells.nth(i).boundingBox();
    if (b) boxes.push({ id: (await cells.nth(i).getAttribute("data-visual-id")) ?? `#${i}`, x: b.x, y: b.y, w: b.width, h: b.height });
  }
  const overlaps: string[] = [];
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i], b = boxes[j];
      const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
      const oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
      if (ox > 2 && oy > 2) overlaps.push(`${a.id} ↔ ${b.id} (${Math.round(ox)}×${Math.round(oy)} px)`);
    }
  }
  expect(overlaps, "üst üste binen görseller").toEqual([]);
  return boxes;
}

// demo dataset'lerinden kurulan, henüz test edilmemiş görsel türleri
const EXTRA = [
  { id: "x_pie", type: "pie", title: "Kategori Payı", datasetId: "category_sales", encoding: { category: "category", value: "sales_amount" } },
  { id: "x_line", type: "line", title: "Aylık Satış (çizgi)", datasetId: "monthly_trend", encoding: { x: "year_month", y: ["sales_amount"] } },
  { id: "x_scatter", type: "scatter", title: "Adet / Tutar", datasetId: "top_products", encoding: { x: "quantity", y: ["sales_amount"] } },
  { id: "x_heat", type: "heatmap", title: "Bölge × Kategori", datasetId: "category_sales",
    encoding: { x: "territory_group", category: "category", value: "sales_amount" } },
  { id: "x_funnel", type: "funnel", title: "Ülke Hunisi", datasetId: "country_sales", encoding: { category: "country", value: "sales_amount" } },
  { id: "x_gauge", type: "gauge", title: "Brüt Kâr Marjı", datasetId: "kpi_summary", encoding: { value: "gross_margin" },
    options: { format: "percent", target: 0.5 } },
  { id: "x_tree", type: "treemap", title: "Alt Kategori Ağacı", datasetId: "top_products", encoding: { category: "subcategory", value: "sales_amount" } },
  { id: "x_matrix", type: "matrix", title: "Bölge > Ülke Matrisi", datasetId: "country_sales",
    encoding: { rows: ["territory_group", "country"], values: ["sales_amount"] } },
  { id: "x_text", type: "text", title: "Not", options: { text: "Bu rapor uçtan uca test için hazırlandı.\nİkinci satır." } },
];
const CHART_TYPES = new Set(["line", "area", "bar", "combo", "pie", "donut", "funnel", "treemap", "heatmap", "scatter", "gauge"]);

test.describe("Dashboard tasarımı", () => {
  test("15 görsel türünün hepsi gerçek veriyle çizilir, tablo görünümünde satır vardır", async ({ page }) => {
    const { id, spec } = await seed(page.request, (s) => { s.visuals = [...s.visuals.map((v) => ({ ...v })), ...EXTRA]; });
    expect(new Set(spec.visuals.map((v) => v.type)).size).toBe(15);
    await open(page, id);
    for (const v of spec.visuals) {
      const cell = visual(page, String(v.id));
      await cell.scrollIntoViewIfNeeded();
      await expect(cell, `${v.id} (${v.type})`).toBeVisible();
      await expect(cell.locator(".db-card--error"), `${v.id}: hata kartı`).toHaveCount(0);
      if (v.type === "kpi") await expect(cell.locator(".db-kpi-value")).not.toHaveText("–");
      if (v.type === "text") await expect(cell).toContainText("İkinci satır");
      if (v.type === "table") await expect(cell.locator("tbody tr").first()).toBeVisible();
      if (v.type === "matrix") {
        await expect(cell.locator("tbody tr[data-level='0']").first()).toBeVisible();
        await expect(cell.locator("tbody tr[data-level='1']").first()).toBeVisible();
        await expect(cell.locator("tfoot")).toContainText("Genel toplam");
      }
      if (CHART_TYPES.has(String(v.type))) {
        await expect(cell.locator("canvas, svg").first(), `${v.id}: grafik çizilmedi`).toBeVisible();
        await cell.getByRole("button", { name: "Tablo görünümü" }).click();
        await expect(cell.locator("tbody tr").first(), `${v.id}: tablo görünümünde veri yok`).toBeVisible();
        await cell.getByRole("button", { name: "Grafiğe dön" }).click();
      }
    }
    await noVisualErrors(page);
  });

  test("yerleşim: görseller üst üste binmez (geniş ve dar ekran, ekleme / silme sonrası)", async ({ page }) => {
    const { id } = await seed(page.request, (s) => { s.visuals = [...s.visuals, ...EXTRA.slice(0, 4)]; });
    await open(page, id);
    await expectNoOverlap(page);
    await page.setViewportSize({ width: 768, height: 900 });
    await page.waitForTimeout(500);
    await expectNoOverlap(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    // görsel silinip büyütülünce de yerleşim düzgün kalmalı
    const s2 = (await (await page.request.get(`/api/sessions/${id}`)).json()).spec as Spec;
    s2.visuals = s2.visuals.filter((v) => v.id !== "kpi_orders").map((v) => (v.id === "country_bar"
      ? { ...v, position: { ...(v.position as object), h: 7 } } : v));
    expect((await page.request.put(`/api/sessions/${id}/spec`, { data: s2 })).status()).toBe(200);
    await page.reload();
    await expect(visual(page, "country_bar")).toBeVisible();
    await expect(visual(page, "kpi_orders")).toHaveCount(0);
    await expectNoOverlap(page);
  });

  test("sayfalar: sekmeler, her sayfanın kendi ızgarası, filtre sayfa değişince korunur, Canlı ve HTML", async ({ page }) => {
    const { id } = await seed(page.request, (s) => {
      s.pages = [{ id: "genel", title: "Genel Bakış" }, { id: "detay", title: "Ürün Detayı" }];
      s.visuals = s.visuals.map((v) => ({ ...v, page: v.id === "product_table" || v.id === "category_donut" ? "detay" : "genel" }));
    });
    await open(page, id);
    const tabs = page.getByRole("tablist", { name: "Rapor sayfaları" });
    await expect(tabs.getByRole("tab")).toHaveText(["Genel Bakış", "Ürün Detayı"]);
    await expect(visual(page, "kpi_sales")).toBeVisible();
    await expect(visual(page, "product_table")).toHaveCount(0);

    // filtre seç → sayfa değiştir → filtre korunur
    const filter = page.locator(".db-filter", { hasText: "Bölge Grubu" });
    await filter.getByRole("button").first().click();
    await page.getByRole("option", { name: "Europe" }).click();
    await page.locator(".db-pop-foot").getByRole("button", { name: "Kapat" }).click();
    await tabs.getByRole("tab", { name: "Ürün Detayı" }).click();
    await expect(visual(page, "product_table")).toBeVisible();
    await expect(visual(page, "kpi_sales")).toHaveCount(0);
    await expect(page.locator(".db-filter", { hasText: "Bölge Grubu" }).locator(".db-select-text")).toHaveText("Europe");
    // ikinci sayfanın görselleri kendi ızgarasının en üstünden başlar (birinci sayfanın altında değil)
    const grid = await page.locator(".db-grid, [class*='db-grid']").first().boundingBox();
    const first = await visual(page, "category_donut").boundingBox();
    expect(first && grid ? first.y - grid.y : 999, "ikinci sayfa üstten başlamalı").toBeLessThan(60);
    await expectNoOverlap(page);

    // Canlı görünümde de sekmeler var
    await page.getByRole("group", { name: "Rapor görünümü" }).getByRole("button", { name: "Canlı" }).click();
    await expect(page.getByRole("tablist", { name: "Rapor sayfaları" }).getByRole("tab")).toHaveCount(2);

    // HTML dışa aktarma iki sayfayı da içerir
    const html = await (await page.request.get(`/api/sessions/${id}/export/html`)).text();
    expect(html).toContain("Ürün Detayı");
    expect(html).toContain("Genel Bakış");
  });

  test("Spec sekmesi: hatalı JSON, şemaya aykırı spec ve geçerli düzenleme (Ctrl+S)", async ({ page }) => {
    const { id } = await seed(page.request);
    await open(page, id);
    await page.getByRole("tab", { name: "Spec" }).or(page.getByRole("button", { name: "Spec" })).first().click();
    const editor = page.locator("textarea.spec-editor");
    const original = await editor.inputValue();
    const apply = page.getByRole("button", { name: "Uygula" });

    await editor.fill(original.replace(/^\{/, "{ hatalı"));
    await expect(page.getByText(/JSON hatası/)).toBeVisible();
    await expect(apply).toBeDisabled();

    const bad = JSON.parse(original);
    bad.visuals[0].datasetId = "olmayan_veri";                              // kayıtlı olmayan dataset
    await editor.fill(JSON.stringify(bad, null, 2));
    await apply.click();
    await expect(page.getByText("Spec doğrulanamadı")).toBeVisible();
    await expect(page.locator(".banner-error")).toContainText("olmayan_veri");
    // ızgaraya sığmayan konum (x + w > 12) hata değildir: sistem görseli sığdırır
    const fit = JSON.parse(original);
    fit.visuals[0].position = { x: 10, y: 0, w: 6, h: 2 };
    await editor.fill(JSON.stringify(fit, null, 2));
    await apply.click();
    await expect(page.locator(".pill-ok", { hasText: "uygulandı" })).toBeVisible();
    const placed = (await (await page.request.get(`/api/sessions/${id}`)).json()).spec.visuals[0].position;
    expect(placed.x + placed.w).toBeLessThanOrEqual(12);

    const good = JSON.parse(original);
    good.title = "Spec ile değişen başlık";
    await editor.fill(JSON.stringify(good, null, 2));
    await editor.press("Control+s");
    await expect(page.locator(".pill-ok", { hasText: "uygulandı" })).toBeVisible();
    await page.getByRole("tab", { name: "Dashboard" }).or(page.getByRole("button", { name: "Dashboard" })).first().click();
    await expect(page.locator(".db-title")).toHaveText("Spec ile değişen başlık");
  });

  test("tema ve biçimler: koyu tema, KPI zemininde okunur yazı, Türkçe sayı biçimleri, boş veri", async ({ page }) => {
    const { id } = await seed(page.request, (s) => {
      s.theme = { ...(s.theme as object), mode: "dark", background: "#0b1220", surface: "#111a2e", text: "#e5e7eb", mutedText: "#94a3b8",
        border: "#1f2a44" };
      s.visuals = s.visuals.map((v) => (v.id === "kpi_orders" ? { ...v, options: { ...(v.options as object), background: "#fef3c7" } } : v));
    });
    await open(page, id);
    const root = page.locator(".db-root");
    await expect(root).toHaveClass(/is-dark/);
    await expect(root).toHaveCSS("background-color", "rgb(11, 18, 32)");
    // açık zeminli KPI'da yazı otomatik koyu (okunur) olmalı
    await expect(visual(page, "kpi_orders").locator(".db-kpi-value")).toHaveCSS("color", "rgb(15, 23, 42)");
    // biçimler: kısaltılmış para ("Mn $"), yüzde ("%"), binlik ayırıcı nokta
    await expect(visual(page, "kpi_sales").locator(".db-kpi-value")).toContainText(/Mn \$/);
    await expect(visual(page, "kpi_margin").locator(".db-kpi-value")).toContainText("%");
    await expect(visual(page, "kpi_orders").locator(".db-kpi-value")).toHaveText(/^\d{1,3}(\.\d{3})+$/);
    await noVisualErrors(page);
  });
});

test.describe("LLM ile tasarım düzenlemeleri", () => {
  test.describe.configure({ mode: "serial" });
  let id = "";
  test.beforeAll(async ({ request }) => { id = (await seed(request)).id; });

  test("grafik türü değişir: çubuk → halka", async ({ page }) => {
    await open(page, id);
    await chat(page, "Ülke grafiğini halka grafiğe çevir.");
    await expectTool(page, "update_visual");
    await expect(visual(page, "country_bar").locator("canvas").first()).toBeVisible();
    const s = (await (await page.request.get(`/api/sessions/${id}`)).json()).spec as Spec;
    expect(s.visuals.find((v) => v.id === "country_bar")?.type).toBe("donut");
    await noVisualErrors(page);
  });

  test("yeni sayfa eklenir ve görsel o sayfaya taşınır", async ({ page }) => {
    await open(page, id);
    await chat(page, "Yeni sayfa ekle.");
    await expectTool(page, "add_page");
    await chat(page, "Ürün tablosunu yeni sayfaya taşı.");
    const tabs = page.getByRole("tablist", { name: "Rapor sayfaları" });
    await expect(tabs.getByRole("tab")).toHaveText(["Özet", "Ürün Detayı"]);
    await expect(visual(page, "product_table")).toHaveCount(0);
    await tabs.getByRole("tab", { name: "Ürün Detayı" }).click();
    await expect(visual(page, "product_table")).toBeVisible();
    await expectNoOverlap(page);
  });

  test("koyu tema isteği dashboard'u gerçekten koyulaştırır", async ({ page }) => {
    await open(page, id);
    await chat(page, "Dashboard'u koyu tema yap.");
    await expectTool(page, "update_report");
    const root = page.locator(".db-root");
    await expect(root).toHaveClass(/is-dark/);
    const bg = await root.evaluate((el) => getComputedStyle(el).backgroundColor);
    const [r, g, b] = bg.match(/\d+/g)!.map(Number);
    expect(r + g + b, `koyu temada arka plan koyu olmalı (${bg})`).toBeLessThan(200);
  });

  test("desteklenmeyen stil alanı: uygulanmaz ve dashboard değişmez", async ({ page }) => {
    await open(page, id);
    const before = (await (await page.request.get(`/api/sessions/${id}`)).json()).spec_version;
    await chat(page, "Satış tutarı KPI'sının fontSize değerini 40 yap.");
    await expectTool(page, "update_visual", false);                       // araç başarısız (uygulanmadı)
    await expect(page.locator('.tool-row[data-tool="update_visual"]').last()).toContainText(/uygulanmadı/i);
    const after = (await (await page.request.get(`/api/sessions/${id}`)).json()).spec_version;
    expect(after).toBe(before);
  });

  test("karışık istek: desteklenen alan uygulanır, desteklenmeyen bildirilir", async ({ page }) => {
    await open(page, id);
    await chat(page, "KPI için karışık stil: kırmızı renk ve etiket konumu üstte.");
    await expectTool(page, "update_visual");
    await expect(page.locator('.tool-row[data-tool="update_visual"]').last()).toContainText("bazı alanlar uygulanmadı");
    await expect(visual(page, "kpi_sales").locator(".db-kpi-value")).toHaveCSS("color", "rgb(220, 38, 38)");
  });

  test("KPI kart stili: lacivert zemin, otomatik açık yazı, büyük değer", async ({ page }) => {
    await open(page, id);
    await chat(page, "Sipariş KPI'sının zeminini lacivert yap, değeri büyüt.");
    await expectTool(page, "update_visual");
    const kpi = visual(page, "kpi_orders");
    await expect(kpi.locator(".db-kpi")).toHaveCSS("background-color", "rgb(15, 23, 42)");
    await expect(kpi.locator(".db-kpi-value")).toHaveCSS("color", "rgb(248, 250, 252)");
    await expect(kpi.locator(".db-kpi")).toHaveClass(/is-xl/);
  });

  test("görsel kaldırılınca boşluk kalmaz, diğerleri bozulmaz", async ({ page }) => {
    await open(page, id);
    await chat(page, "Aktif müşteri KPI'sını kaldır.");
    await expectTool(page, "remove_visual");
    await expect(visual(page, "kpi_customers")).toHaveCount(0);
    await expectNoOverlap(page);
    await noVisualErrors(page);
  });
});

// ---------- matris (pivot tablo) ----------

/** "1.234.567" (tr-TR, ondalıksız) → 1234567 */
const num = (t: string | null) => Number((t ?? "").replace(/[^\d,-]/g, "").replace(",", ".")) || 0;
const lastCell = async (row: Locator) => num(await row.locator("td").last().textContent());

test.describe("Matris (pivot tablo)", () => {
  test("sohbetle pivot tablo: gruplar açılıp kapanır, toplamlar doğru, sıralanır, HTML dışa aktarmada çalışır", async ({ page }) => {
    test.setTimeout(360_000);
    const { id } = await seed(page.request);
    await open(page, id);
    await chat(page, "Bölge ve ülkeye göre son 6 ay satış tutarını, toplamlarıyla pivot tablo olarak ekle.");
    await expectTool(page, "add_dataset");
    await expectTool(page, "add_visual");

    const s = (await (await page.request.get(`/api/sessions/${id}`)).json()).spec as Spec;
    const mv = s.visuals.find((v) => v.id === "pivot_territory") as { type: string; encoding: Record<string, unknown> } | undefined;
    expect(mv?.type).toBe("matrix");
    expect(mv?.encoding).toMatchObject({ rows: ["territory_group", "country"], columnDim: "year_month", values: ["sales_amount"] });

    const cell = visual(page, "pivot_territory");
    await cell.scrollIntoViewIfNeeded();
    await expect(cell.locator(".db-card--error")).toHaveCount(0);
    // 6 ay sütunu + Toplam (veriden dinamik), köşe hücresi
    await expect(cell.locator("thead th")).toHaveCount(8);
    await expect(cell.locator("thead th").last()).toContainText("Toplam");
    const groups = cell.locator("tbody tr[data-level='0']");
    const leaves = cell.locator("tbody tr[data-level='1']");
    await expect(groups).toHaveCount(3);
    const leafCount = await leaves.count();
    expect(leafCount).toBeGreaterThanOrEqual(6);

    // genel toplam = dataset toplamı; her grubun toplamı = ülkelerinin toplamı (yuvarlama payıyla)
    const data = await (await page.request.get(`/api/sessions/${id}/dashboard-data`)).json();
    const ds = data.datasets.territory_monthly as { columns: string[]; rows: unknown[][] };
    const vi = ds.columns.indexOf("sales_amount");
    const expected = ds.rows.reduce((a, r) => a + Number(r[vi] ?? 0), 0);
    const grand = await lastCell(cell.locator("tfoot tr"));
    expect(Math.abs(grand - expected)).toBeLessThanOrEqual(1);
    let groupSum = 0;
    for (let i = 0; i < 3; i++) groupSum += await lastCell(groups.nth(i));
    expect(Math.abs(groupSum - grand)).toBeLessThanOrEqual(3);
    const rowsAll = await cell.locator("tbody tr").evaluateAll((trs) => trs.map((t) => ({
      level: t.getAttribute("data-level"), total: t.querySelector("td:last-child")?.textContent ?? "" })));
    const firstLeaves = rowsAll.slice(1, rowsAll.findIndex((r, i) => i > 0 && r.level === "0"));
    const leafSum = firstLeaves.reduce((a, r) => a + num(r.total), 0);
    expect(Math.abs(leafSum - await lastCell(groups.first()))).toBeLessThanOrEqual(firstLeaves.length);

    // aç / kapa: grup kapanınca ülkeleri gizlenir, ara toplam görünür kalır; "Tümünü daralt / genişlet"
    await groups.first().getByRole("button", { name: /daralt$/ }).click();
    await expect(leaves).toHaveCount(leafCount - firstLeaves.length);
    await expect(groups.first().locator("td").last()).not.toHaveText("");
    await groups.first().getByRole("button", { name: /genişlet$/ }).click();
    await expect(leaves).toHaveCount(leafCount);
    await cell.getByRole("button", { name: "Tümünü daralt" }).click();
    await expect(leaves).toHaveCount(0);
    await expect(groups).toHaveCount(3);
    await cell.getByRole("button", { name: "Tümünü genişlet" }).click();
    await expect(leaves).toHaveCount(leafCount);

    // Toplam başlığına tıklayınca gruplar büyükten küçüğe, ikinci tıkta küçükten büyüğe
    await cell.locator("thead th").last().click();
    const desc: number[] = [];
    for (let i = 0; i < 3; i++) desc.push(await lastCell(groups.nth(i)));
    expect(desc).toEqual([...desc].sort((a, b) => b - a));
    await cell.locator("thead th").last().click();
    const asc: number[] = [];
    for (let i = 0; i < 3; i++) asc.push(await lastCell(groups.nth(i)));
    expect(asc).toEqual([...desc].reverse());
    await noVisualErrors(page);

    // HTML dışa aktarma: bağımsız dosyada matris çizilir, toplamı aynıdır, gruplar açılıp kapanır
    const res = await page.request.get(`/api/sessions/${id}/export/html`);
    expect(res.status()).toBe(200);
    const file = join(mkdtempSync(join(tmpdir(), "bi-matris-")), "rapor.html");
    writeFileSync(file, await res.text(), "utf-8");
    await page.goto(pathToFileURL(file).href);
    const xc = visual(page, "pivot_territory");
    await xc.scrollIntoViewIfNeeded();
    await expect(xc.locator("tbody tr[data-level='0']")).toHaveCount(3);
    expect(Math.abs(await lastCell(xc.locator("tfoot tr")) - grand)).toBeLessThanOrEqual(0);
    await xc.getByRole("button", { name: "Tümünü daralt" }).click();
    await expect(xc.locator("tbody tr[data-level='1']")).toHaveCount(0);
  });
});
