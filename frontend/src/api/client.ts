// Backend API istemcisi (docs/CONTRACT.md) + SSE ayrıştırıcı.
import type { AccessInfo, AdminOverview, AuditEvent, Grant, PlatformRole, PlatformSettings, Publication, VitrinCard, VitrinReport, DashboardData, DataModel, DictionaryHit, FiltersResponse, Health, Me, Selection, UseViewResult, ViewScriptResult, Phase, ReportSpec, SessionState, SessionSummary, StreamEvent, QuerySchema, QueryRunResult, QueryDraft, ConnectionSettings, ConnFields, ConnTestResult, DictCandidates, LlmSettings, LlmTestResult, SuggestionsResponse } from "../types";

export class ApiError extends Error {
  status: number;
  detail: string[];
  constructor(status: number, message: string, detail: string[] = []) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

export interface MessageBody {
  content: string;
  images?: string[];
}

export interface Api {
  mock: boolean;
  health(): Promise<Health>;
  suggestions(offset: number): Promise<SuggestionsResponse>;
  polishSuggestions(offset: number): Promise<SuggestionsResponse>;
  me(): Promise<Me>;
  myAccess(): Promise<AccessInfo>;
  querySchema(): Promise<QuerySchema>;
  runQuery(sql: string): Promise<QueryRunResult>;
  getConnections(): Promise<ConnectionSettings>;
  testConnection(target: "data" | "dictionary", data: ConnFields, dictionary: ConnFields): Promise<ConnTestResult>;
  findInstances(): Promise<{ local: string[]; network: string[] }>;
  listDatabases(target: "data" | "dictionary", data: ConnFields, dictionary: ConnFields): Promise<{ ok: boolean; databases: string[]; error?: string }>;
  saveConnections(data: ConnFields, dictionary: ConnFields): Promise<{ ok: boolean; error: string | null; tables: number }>;
  resetConnections(): Promise<{ ok: boolean; error: string | null }>;
  saveDataConnection(data: ConnFields): Promise<{ ok: boolean; error: string | null; tables: number }>;
  saveDictionaryConnection(dictionary: ConnFields, data: ConnFields): Promise<{ ok: boolean; error: string | null; tables: number }>;
  resetConnectionSection(section: "data" | "dictionary"): Promise<{ ok: boolean; error: string | null }>;
  dictionaryTables(data: ConnFields, dictionary: ConnFields): Promise<DictCandidates>;
  uploadDictionaryExcel(file: File): Promise<{ ok: boolean; path: string; tables: DictCandidates["tables"] }>;
  dictionaryTemplateUrl(layout?: "multi" | "single"): string;
  connectionsExportUrl(): string;
  getLlm(): Promise<LlmSettings>;
  saveLlm(llm: LlmSettings): Promise<{ ok: boolean; reachable?: boolean; error?: string }>;
  resetLlm(): Promise<{ ok: boolean }>;
  llmModels(target: "main" | "vision", llm: LlmSettings): Promise<{ ok: boolean; models: string[]; error?: string }>;
  testLlm(target: "main" | "vision", llm: LlmSettings): Promise<LlmTestResult>;
  listSessions(): Promise<SessionSummary[]>;
  createSession(): Promise<SessionState>;
  getSession(id: string): Promise<SessionState>;
  deleteSession(id: string): Promise<void>;
  renameSession(id: string, title: string, overwrite?: boolean): Promise<SessionState>;
  setStatus(id: string, status: string): Promise<SessionState>;
  sendMessage(id: string, body: MessageBody, onEvent: (ev: StreamEvent) => void, signal?: AbortSignal): Promise<void>;
  setPhase(id: string, phase: Phase): Promise<SessionState>;
  /** sorgu modu: mod ve taslak sorgular (dataset değil) */
  saveQueryDrafts(id: string, body: { mode?: "chat" | "query"; drafts?: QueryDraft[] }): Promise<SessionState>;
  /** sorgu modu önizlemesi: dataset kaydı ve dashboard ile aynı doğrulama */
  queryPreview(id: string, sql: string): Promise<QueryRunResult>;
  /** taslak sorguları dataset olarak kaydeder; İhtiyaç / Veri fazındaysa tasarıma geçer (hatalar ApiError.detail) */
  datasetsFromQuery(id: string, drafts: QueryDraft[]): Promise<SessionState>;
  putSpec(id: string, spec: ReportSpec): Promise<SessionState>;
  loadDemo(id: string): Promise<SessionState>;
  dashboardData(id: string, selections?: Selection[]): Promise<DashboardData>;
  filters(id: string): Promise<FiltersResponse>;
  viewScript(id: string, datasetId: string, name: string): Promise<ViewScriptResult>;
  useView(id: string, datasetId: string, name: string): Promise<UseViewResult>;
  searchDictionary(q: string): Promise<DictionaryHit[]>;
  dataModel(sessionId?: string): Promise<DataModel>;
  exportUrl(id: string): string;
  // ---- Vitrin ----
  publication(sessionId: string): Promise<Publication>;
  publish(sessionId: string, body: { description?: string | null; notes?: string | null; grants?: Grant[] | null }): Promise<{ ok: boolean; report: VitrinCard; session: SessionState }>;
  vitrin(): Promise<VitrinCard[]>;
  vitrinReport(id: string): Promise<VitrinReport>;
  vitrinData(id: string, selections?: Selection[]): Promise<DashboardData>;
  vitrinExportUrl(id: string): string;
  retireReport(id: string): Promise<{ ok: boolean }>;
  deleteRelationship(id: string): Promise<{ ok: boolean }>;
  getGrants(id: string): Promise<Grant[]>;
  setGrants(id: string, grants: Grant[]): Promise<Grant[]>;
  transferOwner(id: string, owner: string): Promise<{ ok: boolean }>;
  // ---- yönetim ----
  adminOverview(): Promise<AdminOverview>;
  setAssignment(a: { principal_type: "user" | "group"; principal: string; platform_role: PlatformRole | null; data_role: string | null }): Promise<{ ok: boolean }>;
  deleteAssignment(principal_type: string, principal: string): Promise<{ ok: boolean }>;
  audit(q?: { limit?: number; user?: string; event?: string; report?: string }): Promise<AuditEvent[]>;
  getPlatformSettings(): Promise<PlatformSettings>;
  savePlatformSettings(meta: ConnFields): Promise<{ ok: boolean; error: string | null }>;
  resetPlatformSettings(): Promise<{ ok: boolean; error: string | null }>;
}

/** FastAPI hata gövdesini okunur satırlara çevirir (string[] veya pydantic {loc,msg}[]). */
export function detailLines(detail: unknown): string[] {
  if (!detail) return [];
  if (typeof detail === "string") return [detail];
  if (Array.isArray(detail))
    return detail.map((d) => {
      if (typeof d === "string") return d;
      if (d && typeof d === "object" && "msg" in d) {
        const loc = Array.isArray((d as { loc?: unknown[] }).loc) ? (d as { loc: unknown[] }).loc.filter((x) => x !== "body").join(".") : "";
        return loc ? `${loc}: ${(d as { msg: string }).msg}` : (d as { msg: string }).msg;
      }
      return JSON.stringify(d);
    });
  if (typeof detail === "object") return [JSON.stringify(detail)];
  return [String(detail)];
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { ...(init?.body ? { "Content-Type": "application/json" } : {}), Accept: "application/json", ...(init?.headers ?? {}) },
    });
  } catch (e) {
    throw new ApiError(0, `Sunucuya ulaşılamadı (${e instanceof Error ? e.message : String(e)})`);
  }
  if (!res.ok) {
    let detail: string[] = [];
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      detail = detailLines(body?.detail ?? body);
      if (detail.length) msg = detail[0];
    } catch {
      /* gövde yok */
    }
    throw new ApiError(res.status, msg, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** "event: x\ndata: {...}\n\n" bloklarını ayrıştırır. */
export async function readSSE(body: ReadableStream<Uint8Array>, onEvent: (ev: StreamEvent) => void) {
  const reader = body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  const flush = (block: string) => {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (!line || line.startsWith(":")) continue;
      const i = line.indexOf(":");
      const field = i < 0 ? line : line.slice(0, i);
      let value = i < 0 ? "" : line.slice(i + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
    if (!data.length && event === "message") return;
    let parsed: unknown = {};
    const text = data.join("\n");
    if (text) {
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = { text };
      }
    }
    onEvent({ event, data: parsed } as StreamEvent);
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true }).replace(/\r\n?/g, "\n");
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      flush(block);
    }
  }
  buf += dec.decode();
  if (buf.trim()) flush(buf.replace(/\r\n?/g, "\n"));
}

export const httpApi: Api = {
  mock: false,
  health: () => req<Health>("/api/health"),
  suggestions: (offset) => req<SuggestionsResponse>(`/api/suggestions?offset=${offset}`),
  polishSuggestions: (offset) => req<SuggestionsResponse>(`/api/suggestions/polish?offset=${offset}`, { method: "POST" }),
  me: () => req<Me>("/api/me"),
  myAccess: () => req<AccessInfo>("/api/me/access"),
  querySchema: () => req<QuerySchema>("/api/query/schema"),
  runQuery: (sql) => req<QueryRunResult>("/api/query", { method: "POST", body: JSON.stringify({ sql }) }),
  getConnections: () => req<ConnectionSettings>("/api/settings/connections"),
  testConnection: (target, data, dictionary) => req<ConnTestResult>("/api/settings/connections/test", { method: "POST", body: JSON.stringify({ target, data, dictionary }) }),
  findInstances: () => req<{ local: string[]; network: string[] }>("/api/settings/instances", { method: "POST" }),
  listDatabases: (target, data, dictionary) => req<{ ok: boolean; databases: string[]; error?: string }>("/api/settings/databases", { method: "POST", body: JSON.stringify({ target, data, dictionary }) }),
  saveConnections: (data, dictionary) => req<{ ok: boolean; error: string | null; tables: number }>("/api/settings/connections", { method: "PUT", body: JSON.stringify({ data, dictionary }) }),
  resetConnections: () => req<{ ok: boolean; error: string | null }>("/api/settings/connections", { method: "DELETE" }),
  saveDataConnection: (data) => req<{ ok: boolean; error: string | null; tables: number }>("/api/settings/connections/data", { method: "PUT", body: JSON.stringify({ data }) }),
  saveDictionaryConnection: (dictionary, data) => req<{ ok: boolean; error: string | null; tables: number }>("/api/settings/connections/dictionary", { method: "PUT", body: JSON.stringify({ dictionary, data }) }),
  resetConnectionSection: (section) => req<{ ok: boolean; error: string | null }>(`/api/settings/connections/${section}`, { method: "DELETE" }),
  uploadDictionaryExcel: async (file) => {
    const res = await fetch(`/api/settings/dictionary/upload?filename=${encodeURIComponent(file.name)}`, {
      method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" },
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new ApiError(res.status, detailLines(body?.detail ?? body)[0] ?? `${res.status}`);
    return body;
  },
  dictionaryTemplateUrl: (layout = "multi") => `/api/settings/dictionary/template.xlsx?layout=${layout}`,
  connectionsExportUrl: () => "/api/settings/connections/export",
  getLlm: () => req<LlmSettings>("/api/settings/llm"),
  saveLlm: (llm) => req<{ ok: boolean; reachable?: boolean; error?: string }>("/api/settings/llm", { method: "PUT", body: JSON.stringify(llm) }),
  resetLlm: () => req<{ ok: boolean }>("/api/settings/llm", { method: "DELETE" }),
  llmModels: (target, llm) => req<{ ok: boolean; models: string[]; error?: string }>("/api/settings/llm/models", { method: "POST", body: JSON.stringify({ target, llm }) }),
  testLlm: (target, llm) => req<LlmTestResult>("/api/settings/llm/test", { method: "POST", body: JSON.stringify({ target, llm }) }),
  dictionaryTables: (data, dictionary) => req<DictCandidates>("/api/settings/dictionary/tables", { method: "POST", body: JSON.stringify({ target: "dictionary", data, dictionary }) }),
  listSessions: () => req<SessionSummary[]>("/api/sessions"),
  createSession: () => req<SessionState>("/api/sessions", { method: "POST", body: "{}" }),
  getSession: (id) => req<SessionState>(`/api/sessions/${encodeURIComponent(id)}`),
  deleteSession: async (id) => {
    await req(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
  },
  async sendMessage(id, body, onEvent, signal) {
    let res: Response;
    try {
      res = await fetch(`/api/sessions/${encodeURIComponent(id)}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify(body),
        signal,
      });
    } catch (e) {
      if (signal?.aborted) return;
      throw new ApiError(0, `Sunucuya ulaşılamadı (${e instanceof Error ? e.message : String(e)})`);
    }
    if (!res.ok || !res.body) {
      let detail: string[] = [];
      try {
        detail = detailLines((await res.json())?.detail);
      } catch {
        /* */
      }
      throw new ApiError(res.status, detail[0] ?? `${res.status} ${res.statusText}`, detail);
    }
    await readSSE(res.body, onEvent);
  },
  setStatus: (id, status) => req<SessionState>(`/api/sessions/${encodeURIComponent(id)}/status`, { method: "PUT", body: JSON.stringify({ status }) }),
  renameSession: (id, title, overwrite = false) =>
    req<SessionState>(`/api/sessions/${encodeURIComponent(id)}/title`, { method: "PUT", body: JSON.stringify({ title, overwrite }) }),
  setPhase: (id, phase) => req<SessionState>(`/api/sessions/${encodeURIComponent(id)}/phase`, { method: "POST", body: JSON.stringify({ phase }) }),
  saveQueryDrafts: (id, body) => req<SessionState>(`/api/sessions/${encodeURIComponent(id)}/query-drafts`, { method: "PUT", body: JSON.stringify(body) }),
  queryPreview: (id, sql) => req<QueryRunResult>(`/api/sessions/${encodeURIComponent(id)}/query-preview`, { method: "POST", body: JSON.stringify({ sql }) }),
  datasetsFromQuery: (id, drafts) => req<SessionState>(`/api/sessions/${encodeURIComponent(id)}/datasets/from-query`, { method: "POST", body: JSON.stringify({ datasets: drafts }) }),
  putSpec: (id, spec) => req<SessionState>(`/api/sessions/${encodeURIComponent(id)}/spec`, { method: "PUT", body: JSON.stringify(spec) }),
  loadDemo: (id) => req<SessionState>(`/api/sessions/${encodeURIComponent(id)}/demo`, { method: "POST" }),
  dashboardData: (id, selections) =>
    selections && selections.length
      ? req<DashboardData>(`/api/sessions/${encodeURIComponent(id)}/dashboard-data`, { method: "POST", body: JSON.stringify({ selections }) })
      : req<DashboardData>(`/api/sessions/${encodeURIComponent(id)}/dashboard-data`),
  filters: (id) => req<FiltersResponse>(`/api/sessions/${encodeURIComponent(id)}/filters`),
  viewScript: (id, did, name) => req<ViewScriptResult>(`/api/sessions/${encodeURIComponent(id)}/datasets/${encodeURIComponent(did)}/view-script`, { method: "POST", body: JSON.stringify({ name }) }),
  useView: (id, did, name) => req<UseViewResult>(`/api/sessions/${encodeURIComponent(id)}/datasets/${encodeURIComponent(did)}/use-view`, { method: "POST", body: JSON.stringify({ name }) }),
  searchDictionary: (q) => req<DictionaryHit[]>(`/api/dictionary/search?q=${encodeURIComponent(q)}`),
  dataModel: (sid) => req<DataModel>(`/api/dictionary/model${sid ? `?session=${encodeURIComponent(sid)}` : ""}`),
  exportUrl: (id) => `/api/sessions/${encodeURIComponent(id)}/export/html`,
  publication: (sid) => req<Publication>(`/api/sessions/${encodeURIComponent(sid)}/publication`),
  publish: (sid, body) => req(`/api/sessions/${encodeURIComponent(sid)}/publish`, { method: "POST", body: JSON.stringify(body) }),
  vitrin: () => req<VitrinCard[]>("/api/vitrin"),
  vitrinReport: (id) => req<VitrinReport>(`/api/vitrin/${encodeURIComponent(id)}`),
  vitrinData: (id, selections) => req<DashboardData>(`/api/vitrin/${encodeURIComponent(id)}/data`, { method: "POST", body: JSON.stringify({ selections: selections ?? [] }) }),
  vitrinExportUrl: (id) => `/api/vitrin/${encodeURIComponent(id)}/export/html`,
  retireReport: (id) => req(`/api/vitrin/${encodeURIComponent(id)}/retire`, { method: "POST" }),
  deleteRelationship: (id) => req(`/api/dictionary/relationships/${encodeURIComponent(id)}`, { method: "DELETE" }),
  getGrants: (id) => req<Grant[]>(`/api/vitrin/${encodeURIComponent(id)}/grants`),
  setGrants: (id, grants) => req<Grant[]>(`/api/vitrin/${encodeURIComponent(id)}/grants`, { method: "PUT", body: JSON.stringify(grants) }),
  transferOwner: (id, owner) => req(`/api/vitrin/${encodeURIComponent(id)}/owner`, { method: "PUT", body: JSON.stringify({ owner }) }),
  adminOverview: () => req<AdminOverview>("/api/admin/overview"),
  setAssignment: (a) => req("/api/admin/assignments", { method: "PUT", body: JSON.stringify(a) }),
  deleteAssignment: (t, p) => req(`/api/admin/assignments/${encodeURIComponent(t)}/${encodeURIComponent(p)}`, { method: "DELETE" }),
  audit: (q = {}) => {
    const qs = new URLSearchParams(Object.entries(q).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => [k, String(v)]));
    return req<AuditEvent[]>(`/api/admin/audit${qs.toString() ? `?${qs}` : ""}`);
  },
  getPlatformSettings: () => req<PlatformSettings>("/api/settings/platform"),
  savePlatformSettings: (meta) => req("/api/settings/platform", { method: "PUT", body: JSON.stringify({ meta }) }),
  resetPlatformSettings: () => req("/api/settings/platform", { method: "DELETE" }),
};
