# Backend ↔ Frontend Sözleşmesi

Backend: FastAPI, `http://localhost:8000`. Tüm uç noktalar `/api` altında. Frontend (Vite, `http://localhost:5173`) `/api` isteklerini backend'e proxy'ler.

## 1. Report Spec (dashboard tanımı)

Agent'ın ürettiği tek çıktı budur. Frontend bu JSON'u render eder; LLM hiçbir zaman HTML/JS üretmez.

```ts
type FieldType = "number" | "string" | "date";
type ValueFormat = "number" | "currency" | "percent" | "compact"; // tr-TR locale

interface DatasetField { name: string; label?: string; type: FieldType; format?: ValueFormat }

interface Dataset {
  id: string;              // snake_case, benzersiz
  description: string;
  sql: string;             // backend doğrular + çalıştırır; frontend sadece gösterir
  fields: DatasetField[];  // backend çalıştırınca doldurur
}

type VisualType =
  | "kpi" | "line" | "area" | "bar" | "pie" | "donut" | "table"
  | "scatter" | "heatmap" | "funnel" | "gauge" | "treemap" | "combo" | "text" | "matrix";

interface Visual {
  id: string;
  type: VisualType;
  title: string;
  subtitle?: string;
  datasetId?: string;      // "text" dışında zorunlu
  encoding: {
    x?: string;            // kategori/zaman ekseni (line/area/bar/scatter/heatmap/combo)
    y?: string[];          // bir veya birden çok ölçü
    series?: string;       // uzun formatta seri ayırıcı alan (y tek eleman olmalı)
    category?: string;     // pie/donut/funnel/treemap etiket alanı; heatmap'te y ekseni
    value?: string;        // kpi/pie/donut/funnel/gauge/treemap/heatmap değer alanı
    columns?: string[];    // table: gösterilecek kolonlar (yoksa hepsi)
    // matrix (pivot tablo; veri uzun formatta gelir, istemcide pivotlanır)
    rows?: string[];       // satır boyutları, 1-2 seviye (ör. ["region", "branch"]); gruplar aç/kapa
    columnDim?: string;    // sütun boyutu: değerleri veriden sütun olur (ör. year_month); yoksa yalnız ölçü sütunları
    values?: string[];     // ölçü(ler); birden çoksa her sütun değerinin altında yan yana
  };
  options?: {
    stacked?: boolean;
    horizontal?: boolean;  // bar
    smooth?: boolean;      // line/area
    showLabels?: boolean;
    showLegend?: boolean;
    format?: ValueFormat;  // değer formatı
    currency?: string;     // varsayılan "TRY"
    decimals?: number;
    sort?: "asc" | "desc"; // değer alanına göre
    limit?: number;        // ilk N satır
    // kpi
    aggregate?: "sum" | "avg" | "count" | "first" | "last" | "min" | "max"; // çok satır varsa, varsayılan "sum"
    deltaField?: string;   // karşılaştırma (ör. büyüme oranı, yüzde olarak 0.12 = %12)
    deltaLabel?: string;   // "geçen yıla göre"
    sparklineDatasetId?: string;
    sparklineField?: string;
    target?: number;       // gauge/kpi hedef
    // combo: y[0] bar, geri kalanı line (ikinci eksen)
    // text
    text?: string;         // markdown değil, düz metin (\n satır sonu)
    color?: string;        // tek seri rengi (hex) — yoksa theme.palette; kpi'da değer + şerit + sparkline rengi
    // kpi kart stili
    background?: string;   // kart zemini (hex)
    textColor?: string;    // yazı rengi (hex); yoksa zemine göre okunur renk
    valueSize?: "sm" | "md" | "lg" | "xl";
    accentBar?: boolean;   // solda renkli şerit (color ya da theme.accent)
    // matrix (hepsi varsayılan true): satır / sütun genel toplamı, grup ara toplamları
    rowTotals?: boolean;   // en altta "Genel toplam" satırı
    columnTotals?: boolean; // sağda "Toplam" sütunu (columnDim varsa)
    subtotals?: boolean;   // grup satırında ara toplam
    maxColumns?: number;   // sütun boyutu sınırı: dönemde son N, diğerlerinde en büyük N + "Diğer"
    conditionalColor?: boolean; // hücre zemini değere göre renklenir (ölçü başına)
  };
  // Şemada olmayan alanlar atılır ve araç sonucunda "UYGULANMADI" olarak modele bildirilir.
  position: { x: number; y: number; w: number; h: number }; // 12 kolon ızgara, h satır birimi
}

interface Filter {
  id: string;
  label: string;
  table?: string;          // model kolonu: ör. "dbo.DimSalesTerritory" — Power BI dilimleyicisi gibi
  column?: string;         //   ör. "SalesTerritoryGroup"; seçim ilişkiler üzerinden (boyut → fact) tüm dataset SQL'lerine uygulanır
  field?: string;          // yedek: dataset kolon adı (table+column yoksa kökeni bulunur; bağımsız HTML'de istemci tarafı filtre)
  type: "select" | "multiselect";
}

interface Theme {
  mode: "light" | "dark";
  palette: string[];       // seri renkleri (hex), en az 3
  background: string;      // sayfa arka planı
  surface: string;         // kart arka planı
  text: string;
  mutedText: string;
  accent: string;
  border: string;
  fontFamily: string;      // ör. "Inter, system-ui, sans-serif"
  radius: number;          // kart köşe yarıçapı px
  cardStyle: "flat" | "outlined" | "elevated";
  density: "compact" | "comfortable";
  headerStyle: "plain" | "banner"; // banner: başlık alanı accent renkli şerit
}

interface ReportSpec {
  version: 1;
  title: string;
  subtitle?: string;
  theme: Theme;
  layout: { columns: 12; rowHeight: number };  // rowHeight px (ör. 90)
  filters: Filter[];
  datasets: Dataset[];
  visuals: Visual[];
}
```

Örnek: `docs/demo_spec.json`.

## 2. Session durumu

```ts
type Phase = "requirements" | "data" | "design";

interface TranscriptItem {
  id: string;
  role: "user" | "assistant" | "tool" | "system";
  content: string;             // user/assistant metni; tool için kısa özet
  images?: string[];           // user: yüklenen görsellerin data URL'leri (küçültülmüş)
  tool?: { name: string; arguments: any; ok: boolean; summary: string; durationMs: number };
  phase: Phase;
  createdAt: string;           // ISO
}

interface Requirements {
  report_title: string; business_goal: string; audience: string;
  kpis: string[]; dimensions: string[]; time_range: string; filters: string[]; notes?: string;
}

interface DesignBrief {         // örnek görselden / tarif edilen tasarımdan çıkarılır
  summary: string;
  mode?: "light" | "dark";
  palette?: string[]; background?: string; accent?: string;
  layout?: string; chart_types?: string[]; style_notes?: string[];
}

interface SessionState {
  id: string;
  title: string;
  phase: Phase;
  transcript: TranscriptItem[];
  requirements: Requirements | null;
  datasets: Dataset[];          // data fazında kaydedilen
  design_brief: DesignBrief | null;
  spec: ReportSpec | null;
  spec_version: number;         // spec her değiştiğinde +1
  busy: boolean;
  data_mode: "chat" | "query";  // İhtiyaç / Veri fazında çalışma modu: sohbet ya da hazır sorgu
  query_drafts: { id: string; title: string; sql: string }[];   // sorgu modundaki taslaklar (henüz dataset değil)
}
```

## 3. Uç noktalar

| Metot | Yol | Gövde | Yanıt |
|---|---|---|---|
| GET | `/api/health` | – | `{ ok, llm: { reachable, model, base_url, error? }, vision: { configured, model }, data: { ok, dialect } }` |
| GET | `/api/sessions` | – | `[{ id, title, phase, updatedAt }]` |
| POST | `/api/sessions` | `{}` | `SessionState` |
| GET | `/api/sessions/{id}` | – | `SessionState` |
| DELETE | `/api/sessions/{id}` | – | `{ ok }` |
| POST | `/api/sessions/{id}/messages` | `{ content: string, images?: string[] }` (data URL) | **SSE akışı** (aşağıda) |
| POST | `/api/sessions/{id}/phase` | `{ phase }` | `SessionState` (geri dönmek için) |
| PUT | `/api/sessions/{id}/query-drafts` | `{ mode?: "chat" \| "query", drafts?: [{ id, title, sql }] }` | `SessionState` (sorgu modu ve taslakları; en çok 20) |
| POST | `/api/sessions/{id}/query-preview` | `{ sql }` | Sorgu Çalıştır yanıtı gibi (`ok, columns, types, rows, truncated, warnings` ya da `errors`); dataset kaydıyla **aynı** doğrulama, en çok 200 satır |
| POST | `/api/sessions/{id}/datasets/from-query` | `{ datasets: [{ id, title, sql }] }` | `SessionState` ya da `422 { detail: string[] }`. Sorgular dataset olur; İhtiyaç / Veri fazındaysa Tasarım fazına geçilir (gereksinim yoksa sorgulardan kısa bir özet), tasarımdaysa dataset eklenir / güncellenir. Denetim: `datasets_from_query` |
| PUT | `/api/sessions/{id}/spec` | `ReportSpec` | `SessionState` veya `422 { detail: string[] }` |
| POST | `/api/sessions/{id}/demo` | – | Demo spec + dataset'leri yükler, `SessionState` |
| GET | `/api/sessions/{id}/dashboard-data` | – | `{ datasets: { [id]: { columns: string[], rows: any[][], error?: string } }, applied: {} }` |
| POST | `/api/sessions/{id}/dashboard-data` | `{ selections: [{ key: "şema.tablo.kolon", values: any[], exclude?: string[] }] }` | Aynı + `applied: { [datasetId]: string[] }` (hangi filtre hangi dataset'e uygulanabildi) |
| GET | `/api/sessions/{id}/filters` | – | `{ filters: [{ id, label, type, key, options: any[] }], bindings: { [datasetId]: { [alan]: "şema.tablo.kolon" } } }` |
| GET | `/api/dictionary/model?session=` | – | İlişkisel model (tablolar, kolonlar, ilişkiler: kardinalite, rol, aktif) + raporda kullanılan tablolar |
| GET | `/api/dictionary/search?q=...` | – | `[{ table, business_name, description, score, columns: [{ name, business_name, role }] }]` |
| GET | `/api/sessions/{id}/export/html` | – | Tek dosyalık, bağımsız HTML dashboard (indir) |

### SSE (`POST /messages`)

`fetch` + `ReadableStream` ile okunur (EventSource POST desteklemez). Her olay `event: <tip>\ndata: <json>\n\n`.

| event | data |
|---|---|
| `transcript` | `TranscriptItem` — yeni bir satır (user, tool adımı, assistant) |
| `status` | `{ text: string }` — "Veri sözlüğü aranıyor…" gibi geçici durum |
| `state` | `SessionState` — faz/spec/dataset değişince tam durum |
| `error` | `{ message: string }` |
| `done` | `{}` |

`rows` değerleri: sayılar number, tarihler ISO string (`"2026-01-01"`), null olabilir.

## 4. Model filtreleri (Power BI tarzı)

* Dilimleyici seçimleri ve görselden tıklamalar `selections` olarak POST edilir. Backend her dataset SQL'inin her SELECT
  bloğuna, filtre tablosundan ilişkiler üzerinden (yalnız aktif, "tek → çok" yönünde) yayılan bir koşul ekler
  (`EXISTS (SELECT 1 FROM Dim d WHERE d.Key = f.Key AND d.kolon IN (...))`, çok adımlı yollarda iç içe).
* Filtre tablosu dataset'te zaten varsa doğrudan o takma ada uygulanır (rol yapan tarih boyutunda dataset'in JOIN'i rolü belirler).
* Ulaşılamayan dataset'ler filtrelenmez; `applied` içinde o filtre anahtarı olmaz → arayüz "Filtre dışı" rozeti gösterir.
* Görselden tıklama: `bindings` ile tıklanan alanın model kolonu bulunur; seçim `exclude: [kaynak dataset]` ile gönderilir,
  kaynak görsel tüm veriyi gösterip seçili öğeyi vurgular.
