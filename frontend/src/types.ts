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
  status?: "idea" | "design" | "test" | "live" | null;
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
  owner?: string | null;
  owner_name?: string | null;
  domains?: string[];
  source_tables?: string[];
  status?: "idea" | "design" | "test" | "live";
  status_explicit?: boolean;
}

export interface Health {
  ok: boolean;
  llm: { reachable: boolean; model: string; base_url: string; error?: string };
  vision: { configured: boolean; model: string | null };
  data: { ok: boolean; dialect: string; error?: string };
  dictionary?: { tables: number; error?: string };
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

// ---- Kullanıcı ve veri erişimi (GET /api/me, /api/me/access) ----
export interface Policy { allowed_schemas: string[]; denied_tables: string[]; allow_pii: boolean; max_rows: number }

export interface Me {
  username: string;
  display_name: string;
  domain: string;
  email?: string | null;
  department?: string | null;
  title?: string | null;
  groups: string[];
  role: string;
  source: "windows" | "ldap" | "header" | string;
  domain_joined: boolean;
  policy?: Policy;
}

/** Veri Erişimim: tek nesne listesi (tablo / view / stored procedure / dataset), domain ve erişim durumuyla */
export interface AccessObject {
  type: "table" | "view" | "procedure" | "dataset";
  id: string; name: string; business_name?: string; description?: string; subject_area: string;
  accessible: boolean; reason: string | null;
  row_count?: number | null; column_count?: number; pii_columns?: string[]; pii_blocked?: boolean;   // tablo / view
  report_id?: string | null; report_title?: string; dataset_id?: string | null; created_at?: string | null;  // view / dataset
  view?: string | null; fields?: number; tables?: string[]; parameters?: string[];
  documented?: boolean;   // false: veritabanında var, sözlükte tanımsız
}

export interface AccessInfo {
  user: Me; role: string; policy: Policy; objects: AccessObject[];
  catalog?: { ok: boolean; error: string | null; undocumented: number; missing?: string[]; missing_count?: number; renamed_count?: number };
}

// ---------------------------------------------------------------- sorgu çalıştır
export interface QueryColumn { name: string; type: string; business_name?: string; description?: string; pii?: boolean; blocked?: boolean }
export interface QueryObject {
  id: string; name: string; kind: string; business_name?: string; description?: string; subject_area: string;
  row_count?: number | null; columns: QueryColumn[]; documented?: boolean;
}
export interface QueryDataset { report_id: string; report_title: string; id: string; description?: string; sql: string; view?: string | null; subject_area?: string; domains?: string[] }
export interface QueryProcedure { id: string; name: string; description?: string; parameters: string[]; tables: string[]; subject_area: string }
export interface QuerySchema {
  role: string; max_rows: number; allow_pii: boolean; allowed_schemas: string[]; objects: QueryObject[]; datasets: QueryDataset[];
  procedures?: QueryProcedure[];
}
export interface QueryRunResult {
  ok: boolean; errors?: string[]; warnings?: string[]; columns?: string[]; types?: string[];
  rows?: unknown[][]; truncated?: boolean; row_limit?: number; elapsed_ms?: number; tables?: string[];
}

// ---------------------------------------------------------------- bağlantı ayarları
export interface ConnFields {
  server: string;
  database: string;
  auth: "windows" | "sql";
  username: string;
  password?: string | null;      // gönderirken: null → kayıtlı şifre korunur
  has_password?: boolean;        // alırken
  encrypt: boolean;
  trust_server_certificate: boolean;
  same_as_data?: boolean;        // yalnız sözlük
  sources?: Record<DictRole, string[]>;  // yalnız sözlük: rol → tablolar / sayfalar
  kind?: DictKind;               // yalnız sözlük
  port?: number | null;          // MySQL
  excel_path?: string;           // Excel
  mappings?: Record<string, Record<string, string>>;  // yalnız sözlük: kaynak → alan → başlık ("" = kullanma)
}
export type DictKind = "sqlserver" | "excel" | "mysql" | "none";
export type DictRole = "tables" | "columns" | "relationships" | "metrics";
export interface DictCandidates {
  ok: boolean; error?: string;
  tables: { name: string; columns: string[]; role: DictRole | null }[];
  roles?: Record<DictRole, { label: string; required: string[]; optional: string[]; must: boolean }>;
}
export interface ConnectionSettings {
  source: "ui" | "env";
  data_source: "ui" | "env";
  dictionary_source: "ui" | "env";
  data: ConnFields;
  dictionary: ConnFields;
  drivers: string[];
  driver: string | null;
  default_dictionary_db: string;
  startup_error: string | null;
  file: string;
}
export interface ConnTestResult {
  ok: boolean; error?: string; server_name?: string; database?: string; version?: string; edition?: string;
  login?: string; driver?: string; dictionary_tables?: number;
  dictionary_counts?: Partial<Record<DictRole, number>>; warnings?: string[];
  derived_tables?: number; relationship_source?: "foreign_keys" | "name_match" | "none";
  catalog_match?: CatalogMatch;
  sources_info?: Record<string, SourceInfo>;
}

// ---------------------------------------------------------------- dil modeli (LLM) bağlantısı
export type ToolMode = "auto" | "native" | "prompt";
export interface LlmVision { enabled: boolean; same_as_main: boolean; base_url: string; model: string; api_key?: string | null; has_api_key?: boolean }
export interface LlmSettings {
  source?: "ui" | "env";
  base_url: string; model: string; tool_mode: ToolMode; extra_body?: Record<string, unknown> | null;
  api_key?: string | null; has_api_key?: boolean;
  vision: LlmVision;
}
export interface LlmTestResult { ok: boolean; error?: string; model?: string; elapsed_ms?: number; reply?: string; tools?: "native" | "prompt" }

/** Sözlükteki tablo / view adlarının veri kaynağındaki nesnelerle eşleşmesi (Ayarlar → sözlük testi). */
export interface CatalogMatch {
  ok: boolean; database: string; error?: string | null;
  total?: number; found?: number; db_objects?: number;
  missing?: string[]; missing_count?: number;
  no_select?: string[]; no_select_count?: number;
  renamed?: [string, string][]; renamed_count?: number;
}

/** Sözlük kaynağının (tablo / sayfa) sütunlarının sözlük alanlarına eşlenmesi. */
export interface SourceInfo {
  role: DictRole;
  headers: string[];
  mapping: Record<string, string | null>;
  how: Record<string, "header" | "content" | "manual">;
}
