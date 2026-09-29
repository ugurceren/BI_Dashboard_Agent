// Backend ↔ Frontend sözleşmesi (docs/CONTRACT.md) — birebir.

export type FieldType = "number" | "string" | "date";
export type ValueFormat = "number" | "currency" | "percent" | "compact";

export interface DatasetField {
  name: string;
  label?: string;
  type: FieldType;
  format?: ValueFormat;
}

export interface Dataset {
  id: string;
  description: string;
  sql: string;
  fields: DatasetField[];
  /** kalıcılaştırıldıysa kaynak view (ör. rpt.v_bolge_satis) */
  view?: string | null;
  original_sql?: string | null;
}

export type VisualType =
  | "kpi" | "line" | "area" | "bar" | "pie" | "donut" | "table"
  | "scatter" | "heatmap" | "funnel" | "gauge" | "treemap" | "combo" | "text";

export interface VisualEncoding {
  x?: string;
  y?: string[];
  series?: string;
  category?: string;
  value?: string;
  columns?: string[];
}

export interface VisualOptions {
  stacked?: boolean;
  horizontal?: boolean;
  smooth?: boolean;
  showLabels?: boolean;
  showLegend?: boolean;
  format?: ValueFormat;
  currency?: string;
  decimals?: number;
  sort?: "asc" | "desc";
  limit?: number;
  aggregate?: "sum" | "avg" | "first" | "last" | "min" | "max";
  deltaField?: string;
  /** önceki dönem değeri: deltaField yoksa değişim buradan hesaplanır (tutar → %, oran → puan) */
  compareField?: string;
  deltaLabel?: string;
  sparklineDatasetId?: string;
  sparklineField?: string;
  target?: number;
  text?: string;
  color?: string;
}

export interface Visual {
  id: string;
  type: VisualType;
  title: string;
  subtitle?: string;
  datasetId?: string;
  encoding: VisualEncoding;
  options?: VisualOptions;
  position: { x: number; y: number; w: number; h: number };
}

export interface Filter {
  id: string;
  label: string;
  /** model kolonu (Power BI dilimleyicisi gibi ilişkiler üzerinden tüm görsellere yayılır) */
  table?: string;
  column?: string;
  /** yedek: dataset kolon adı (bağımsız HTML'de istemci tarafı filtre) */
  field?: string;
  type: "select" | "multiselect";
}

export interface Theme {
  mode: "light" | "dark";
  palette: string[];
  background: string;
  surface: string;
  text: string;
  mutedText: string;
  accent: string;
  border: string;
  fontFamily: string;
  radius: number;
  cardStyle: "flat" | "outlined" | "elevated";
  density: "compact" | "comfortable";
  headerStyle: "plain" | "banner";
}

export interface ReportSpec {
  version: 1;
  title: string;
  subtitle?: string;
  theme: Theme;
  layout: { columns: 12; rowHeight: number };
  filters: Filter[];
  datasets: Dataset[];
  visuals: Visual[];
}

export type CellValue = number | string | boolean | null;

export interface DatasetData {
  columns: string[];
  rows: CellValue[][];
  error?: string;
}

export interface DashboardData {
  datasets: Record<string, DatasetData>;
  /** dataset → uygulanan model filtre anahtarları ('şema.tablo.kolon') */
  applied?: Record<string, string[]>;
  /** dataset → {alan: model kolonu anahtarı} (export'ta istemci tarafı filtre için) */
  bindings?: Record<string, Record<string, string>>;
  /** filtre id → model kolonu anahtarı (export) */
  filter_keys?: Record<string, string | null>;
}

// ---- Model filtreleri (GET /filters, POST /dashboard-data) ----
export interface FilterInfo {
  id: string;
  label: string;
  type: "select" | "multiselect";
  field?: string | null;
  key: string | null;          // 'şema.tablo.kolon'
  options: CellValue[];
}

export interface FiltersResponse {
  filters: FilterInfo[];
  bindings: Record<string, Record<string, string>>;
}

export interface Selection {
  key: string;
  values: CellValue[];
  exclude?: string[];          // bu dataset'lere uygulanmaz (tıklanan görselin kendisi)
}

/** Görselden tıklayarak yapılan çapraz filtre */
export interface CrossSelection {
  key: string;
  value: CellValue;
  label: string;               // "Ülke: France"
  visualId: string;
  datasetId: string;
}

// ---- Session ----

export type Phase = "requirements" | "data" | "design";

export interface ToolInfo {
  name: string;
  arguments: unknown;
  ok: boolean;
  summary: string;
  durationMs: number;
}

export interface TranscriptItem {
  id: string;
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  images?: string[];
  tool?: ToolInfo;
  phase: Phase;
  createdAt: string;
}

export interface Requirements {
  report_title: string;
  business_goal: string;
  audience: string;
  kpis: string[];
  dimensions: string[];
  time_range: string;
  filters: string[];
  notes?: string;
}

export interface DesignBrief {
  summary: string;
  mode?: "light" | "dark";
  palette?: string[];
  background?: string;
  accent?: string;
  layout?: string;
  chart_types?: string[];
  style_notes?: string[];
}

export interface SessionState {
  id: string;
  title: string;
  phase: Phase;
  transcript: TranscriptItem[];
  requirements: Requirements | null;
  datasets: Dataset[];
  design_brief: DesignBrief | null;
  spec: ReportSpec | null;
  spec_version: number;
  busy: boolean;
}

export interface SessionSummary {
  id: string;
  title: string;
  phase: Phase;
  updatedAt: string;
  // rapor envanteri kartı
  createdAt?: string;
  subtitle?: string | null;
  business_goal?: string | null;
  audience?: string | null;
  kpis?: string[];
  dimensions?: string[];
  time_range?: string | null;
  visual_count?: number;
  dataset_count?: number;
  visual_types?: string[];
  kpi_titles?: string[];
  filters?: string[];
  views?: string[];
  theme?: { mode?: string; accent?: string; background?: string; palette?: string[] } | null;
  has_spec?: boolean;
}

export interface Health {
  ok: boolean;
  llm: { reachable: boolean; model: string; base_url: string; error?: string };
  vision: { configured: boolean; model: string | null };
  data: { ok: boolean; dialect: string };
}

export interface DictionaryHit {
  table: string;
  business_name: string;
  description: string;
  score: number;
  columns: { name: string; business_name: string; role: string }[];
}

export type StreamEvent =
  | { event: "transcript"; data: TranscriptItem }
  | { event: "status"; data: { text: string } }
  | { event: "state"; data: SessionState }
  | { event: "error"; data: { message: string } }
  | { event: "done"; data: Record<string, never> };

// ---------- İlişkisel model (GET /api/dictionary/model) ----------
export type TableKind = "fact" | "dimension" | "bridge" | "view";
export type Cardinality = "N:1" | "1:1" | "N:N";

export interface ModelColumn {
  id: string;            // küçük harf (ilişki eşleştirme anahtarı)
  name: string;          // orijinal yazım
  business_name: string;
  data_type: string;
  role: string;
  is_pii: boolean;
  is_key: boolean;
  description: string;
}

export interface ModelTable {
  id: string;            // küçük harf şema.tablo
  name: string;
  schema: string;
  short_name: string;
  business_name: string;
  description: string;
  subject_area: string;
  grain: string;
  row_count: number | null;
  kind: TableKind;
  columns: ModelColumn[];
}

export interface ModelRelationship {
  id: string;
  from_table: string;    // "çok" taraf (normalize)
  to_table: string;
  pairs: [string, string][];
  pairs_display: [string, string][];
  cardinality: Cardinality | null;
  role: string;
}

export interface DataModel {
  tables: ModelTable[];
  relationships: ModelRelationship[];
  used_tables: Record<string, string[]>;   // tablo id → onu kullanan dataset id'leri
  dialect: string;
}

export interface ViewScriptResult { view: string; script: string; file: string; exists: boolean }
export interface UseViewResult { session: SessionState; view: string; lost_filters: string[] }
