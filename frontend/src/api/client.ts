// Backend API istemcisi (docs/CONTRACT.md) + SSE ayrıştırıcı.
import type {
  DashboardData, DataModel, DictionaryHit, FiltersResponse, Health, Selection, Phase, ReportSpec, SessionState, SessionSummary, StreamEvent,
} from "../types";

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
  listSessions(): Promise<SessionSummary[]>;
  createSession(): Promise<SessionState>;
  getSession(id: string): Promise<SessionState>;
  deleteSession(id: string): Promise<void>;
  sendMessage(id: string, body: MessageBody, onEvent: (ev: StreamEvent) => void, signal?: AbortSignal): Promise<void>;
  setPhase(id: string, phase: Phase): Promise<SessionState>;
  putSpec(id: string, spec: ReportSpec): Promise<SessionState>;
  loadDemo(id: string): Promise<SessionState>;
  dashboardData(id: string, selections?: Selection[]): Promise<DashboardData>;
  filters(id: string): Promise<FiltersResponse>;
  searchDictionary(q: string): Promise<DictionaryHit[]>;
  dataModel(sessionId?: string): Promise<DataModel>;
  exportUrl(id: string): string;
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
  setPhase: (id, phase) => req<SessionState>(`/api/sessions/${encodeURIComponent(id)}/phase`, { method: "POST", body: JSON.stringify({ phase }) }),
  putSpec: (id, spec) => req<SessionState>(`/api/sessions/${encodeURIComponent(id)}/spec`, { method: "PUT", body: JSON.stringify(spec) }),
  loadDemo: (id) => req<SessionState>(`/api/sessions/${encodeURIComponent(id)}/demo`, { method: "POST" }),
  dashboardData: (id, selections) =>
    selections && selections.length
      ? req<DashboardData>(`/api/sessions/${encodeURIComponent(id)}/dashboard-data`, { method: "POST", body: JSON.stringify({ selections }) })
      : req<DashboardData>(`/api/sessions/${encodeURIComponent(id)}/dashboard-data`),
  filters: (id) => req<FiltersResponse>(`/api/sessions/${encodeURIComponent(id)}/filters`),
  searchDictionary: (q) => req<DictionaryHit[]>(`/api/dictionary/search?q=${encodeURIComponent(q)}`),
  dataModel: (sid) => req<DataModel>(`/api/dictionary/model${sid ? `?session=${encodeURIComponent(sid)}` : ""}`),
  exportUrl: (id) => `/api/sessions/${encodeURIComponent(id)}/export/html`,
};
