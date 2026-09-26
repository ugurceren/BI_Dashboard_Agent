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
  field: string;
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
