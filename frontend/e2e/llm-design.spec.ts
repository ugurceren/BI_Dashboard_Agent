// Gerçek LLM ile tasarım (isteğe bağlı, npm run e2e:llm): demo dashboard üzerinde doğal dilde istekler — tüm grafik
// türleri, tablo biçimleri, sayfalar ve KPI kart stili. Model yanıtları her koşuda farklı olabilir: metin karşılaştırılmaz,
// her istekten sonra spec (API) ve çizim (tarayıcı) kontrol edilir. Yumuşak (soft) doğrulama: bir istek başarısız olsa da
// diğerleri denenir; rapor hangi isteklerin tutmadığını listeler.
import { expect, test, chat, newReport, visual } from "./fixtures";

type V = { id: string; type: string; page?: string | null; datasetId: string; encoding: Record<string, unknown>;
  options: Record<string, unknown> };
type Spec = { title: string; pages?: { id: string; title: string }[]; visuals: V[]; theme: Record<string, unknown> };

const TURN = 600_000;

test("@llm gerçek LLM: grafik türleri, tablolar, sayfalar ve KPI stili", async ({ page }) => {
  test.setTimeout(3_600_000);
  const health = await page.request.get("/api/health").then((r) => r.json());
  test.skip(!health.llm?.reachable, `LLM erişilemiyor: ${health.llm?.base_url ?? "?"}`);

  const id = await newReport(page);
  const spec = async (): Promise<Spec> => (await (await page.request.get(`/api/sessions/${id}`)).json()).spec;
  const byType = async (t: string) => (await spec()).visuals.filter((v) => v.type === t);
  const ask = async (text: string) => {
    await chat(page, text, TURN);
    await expect(page.getByText("Görsel çizilemedi")).toHaveCount(0);
    await expect.soft(page.locator(".msg").last(), "LLM hatası yok").not.toContainText(/LLM hatası|bağlam penceresi/);
  };
  const rendered = async (v: V | undefined) => {
    if (!v) return;
    const cell = visual(page, v.id);
    await expect.soft(cell, `${v.id} görünür`).toBeVisible({ timeout: 120_000 });
    if (!["kpi", "text", "table"].includes(v.type)) await expect.soft(cell.locator("canvas, svg").first(), `${v.id} çizildi`).toBeVisible();
  };
  /** Son asistan mesajı yapılmamış bir değişikliği "yaptım" diye anlatmamalı. */
  const noFalseClaim = async (before: number) => {
    const after = (await (await page.request.get(`/api/sessions/${id}`)).json()).spec_version;
    if (after === before) {
      await expect.soft(page.locator(".msg-assistant").last(), "değişiklik yokken 'yaptım' denmemeli")
        .not.toContainText(/ayarlandı|güncellendi|uygulandı|değiştirildi|olarak ayarla/);
    }
  };

  await test.step("demo dashboard", async () => {
    const [res] = await Promise.all([page.waitForResponse((r) => r.url().endsWith("/demo")),
      page.getByRole("button", { name: "Demo dashboard yükle" }).click()]);
    expect(res.status()).toBe(200);
    await expect(visual(page, "kpi_sales")).toBeVisible({ timeout: 180_000 });
  });

  // ---------------------------------------------------------------- grafik türleri
  const typeCases: [string, string, () => Promise<void>][] = [
    ["Kategori dağılımı grafiğini pasta grafik yap.", "pie", async () => {
      expect.soft((await spec()).visuals.find((v) => v.id === "category_donut")?.type).toBe("pie"); }],
    ["Aylık satış trendini alan grafiği olarak göster.", "area", async () => {
      expect.soft((await spec()).visuals.find((v) => v.id === "trend")?.type).toBe("area"); }],
    ["Kanallara göre aylık satış grafiğini yığılmış çubuk grafik yap.", "bar", async () => {
      const v = (await spec()).visuals.find((x) => x.id === "channel_area");
      expect.soft(v?.type).toBe("bar");
      expect.soft(v?.options?.stacked).toBe(true); }],
    ["Ay ve kanala göre satışı gösteren bir ısı haritası (heatmap) ekle.", "heatmap", async () => {
      expect.soft((await byType("heatmap")).length).toBeGreaterThan(0); }],
    ["Toplam satış için hedefi 30 milyon olan bir gösterge (gauge) ekle.", "gauge", async () => {
      const g = (await byType("gauge"))[0];
      expect.soft(g, "gauge eklendi").toBeTruthy();
      expect.soft(Number(g?.options?.target), "hedef 30 milyon").toBe(30_000_000); }],
    ["Ürün kategorilerine göre satışı gösteren bir huni (funnel) grafiği ekle.", "funnel", async () => {
      expect.soft((await byType("funnel")).length).toBeGreaterThan(0); }],
    ["Ürünlerde satış tutarı ile satılan adet arasındaki ilişkiyi gösteren bir dağılım (scatter) grafiği ekle.", "scatter", async () => {
      const s = (await byType("scatter"))[0];
      expect.soft(s, "scatter eklendi").toBeTruthy();
      expect.soft([s?.encoding?.x, ...((s?.encoding?.y as string[]) ?? [])].sort()).toEqual(["quantity", "sales_amount"]); }],
    ["Alt kategorilere göre satışı ağaç haritası (treemap) olarak ekle.", "treemap", async () => {
      expect.soft((await byType("treemap")).length).toBeGreaterThan(0); }],
    ["Aylık satış tutarı ile sipariş adedini birlikte gösteren bir combo grafik ekle.", "combo", async () => {
      expect.soft((await byType("combo")).some((v) => ((v.encoding.y as string[]) ?? []).length >= 2)).toBe(true); }],
    ["Rapora en üste kısa bir açıklama metni ekle: 'Veriler AdventureWorks bayi ve internet satışlarıdır.'", "text", async () => {
      // metin görseli ya da rapor alt başlığı: ikisi de "en üstte açıklama"
      const s = await spec();
      const inText = s.visuals.some((v) => v.type === "text" && /AdventureWorks/.test(String(v.options?.text ?? "")));
      expect.soft(inText || /AdventureWorks/.test(String((s as Spec & { subtitle?: string }).subtitle ?? "")), "açıklama eklendi").toBe(true);
      await expect.soft(page.getByText("Veriler AdventureWorks bayi ve internet satışlarıdır.").first()).toBeVisible(); }],
  ];
  for (const [text, type, check] of typeCases) {
    await test.step(`${type}: ${text}`, async () => {
      await ask(text);
      await check();
      for (const v of await byType(type)) await rendered(v);
    });
  }

  // ---------------------------------------------------------------- tablolar
  await test.step("tablo: en çok satan 10 ürün, satışa göre sıralı", async () => {
    const before = (await spec()).visuals.map((v) => v.id);
    await ask("En çok satan 10 ürünü satış tutarına göre büyükten küçüğe sıralı gösteren yeni bir tablo ekle.");
    // yeni tablo ya da isteği zaten karşılayan mevcut tablo (demo'daki ürün tablosu "En Çok Satan 10 Ürün" olabilir)
    const tables = (await spec()).visuals.filter((v) => v.type === "table");
    const t = tables.find((v) => !before.includes(v.id)) ?? tables.find((v) => Number(v.options?.limit) === 10);
    expect.soft(t, "ilk 10 ürün tablosu").toBeTruthy();
    expect.soft(Number(t?.options?.limit)).toBe(10);
    expect.soft(t?.options?.sort).toBe("desc");
    if (t) {
      await rendered(t);
      await expect.soft(visual(page, t.id).locator("tbody tr")).toHaveCount(10);
    }
  });
  await test.step("tablo: kolon seçimi", async () => {
    await ask("Ürün tablosunda (product_table) yalnız ürün ve satış tutarı kolonları kalsın.");
    const t = (await spec()).visuals.find((v) => v.id === "product_table");
    expect.soft(t?.encoding?.columns).toEqual(["product", "sales_amount"]);
    await expect.soft(visual(page, "product_table").locator("thead th")).toHaveCount(2);
  });

  // ---------------------------------------------------------------- sayfalar
  await test.step("sayfalar: yeni sayfa, görsel taşıma, ad değiştirme, geçişler", async () => {
    await ask("'Ürün Detayı' adında yeni bir sayfa ekle ve ürün tablosunu ile ağaç haritasını o sayfaya taşı.");
    let s = await spec();
    const detail = s.pages?.find((p) => /ürün/i.test(p.title));
    expect.soft(s.pages?.length ?? 0, "en az iki sayfa").toBeGreaterThanOrEqual(2);
    expect.soft(s.visuals.find((v) => v.id === "product_table")?.page, "tablo taşındı").toBe(detail?.id);
    await ask("İlk sayfanın adını 'Genel Özet' yap.");
    s = await spec();
    expect.soft(s.pages?.[0]?.title).toBe("Genel Özet");
    for (const p of s.pages ?? []) {
      const tab = page.getByRole("tablist", { name: "Rapor sayfaları" }).getByRole("tab", { name: p.title });
      await expect.soft(tab, `'${p.title}' sekmesi`).toHaveCount(1);
      if (!(await tab.count())) continue;
      await tab.click();
      const onPage = s.visuals.filter((v) => (v.page ?? s.pages![0].id) === p.id);
      for (const v of onPage) await rendered(v);
      const others = s.visuals.filter((v) => (v.page ?? s.pages![0].id) !== p.id);
      for (const v of others.slice(0, 3)) await expect.soft(visual(page, v.id), `${v.id} başka sayfada`).toHaveCount(0);
    }
  });

  // ---------------------------------------------------------------- KPI kart stili
  await test.step("KPI: lacivert zemin, beyaz yazı, büyük değer", async () => {
    await ask("Satış KPI kartının zeminini lacivert yap, yazılar beyaz olsun ve değer daha büyük görünsün.");
    const o = (await spec()).visuals.find((v) => v.id === "kpi_sales")?.options ?? {};
    const hex = String(o.background ?? "");
    expect.soft(hex, "lacivert hex").toMatch(/^#[0-9a-f]{6}$/i);
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
    expect.soft(b > r && b > g && r + g + b < 300, `lacivert (koyu mavi) olmalı: ${hex}`).toBe(true);
    expect.soft(["lg", "xl"]).toContain(o.valueSize);
    const firstTab = page.getByRole("tablist", { name: "Rapor sayfaları" }).getByRole("tab").first();
    if (await firstTab.count()) await firstTab.click();
    if ((await spec()).visuals.find((v) => v.id === "kpi_sales")?.type === "kpi") {
      await expect.soft(visual(page, "kpi_sales").locator(".db-kpi-value")).toHaveCSS("color", /rgb\(2[2-5]\d, 2[2-5]\d, 2[2-5]\d\)/);
    } else {
      test.info().annotations.push({ type: "not", description: "kpi_sales önceki bir istekte başka türe çevrildi; yazı rengi kontrol edilmedi" });
    }
  });
  await test.step("KPI: mavi tonları (tüm kartlar)", async () => {
    await ask("Tüm KPI kartlarının rengini mavi tonlarında yap.");
    for (const v of await byType("kpi")) {
      // değer rengi ya da kart zemini mavi tonunda olabilir (ikisi de "kartın rengi")
      const c = String(v.options.color ?? v.options.background ?? "");
      const [r, g, b] = [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
      expect.soft(c && b >= r && b >= g, `${v.id} mavi tonu olmalı: ${c}`).toBe(true);
    }
  });
  await test.step("KPI: desteklenmeyen istek (yazı tipi punto) yapılmış gibi anlatılmaz", async () => {
    const v0 = (await (await page.request.get(`/api/sessions/${id}`)).json()).spec_version;
    await ask("KPI kartlarının yazı tipini 40 punto yap.");
    await noFalseClaim(v0);
  });
  await test.step("tema: koyu tema", async () => {
    await ask("Tüm dashboard'u koyu temaya çevir.");
    expect.soft((await spec()).theme.mode).toBe("dark");
    const bg = await page.locator(".db-root").first().evaluate((el) => getComputedStyle(el).backgroundColor);
    const sum = (bg.match(/\d+/g) ?? []).slice(0, 3).map(Number).reduce((a, b) => a + b, 0);
    expect.soft(sum, `koyu arka plan (${bg})`).toBeLessThan(200);
  });
});

