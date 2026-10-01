// ?mock=1 — backend olmadan geliştirme için bellek içi sahte API.
import demoSpecJson from "../mocks/demo_spec.json";
import demoDataJson from "../mocks/demo_data.json";
import demoModelJson from "../mocks/demo_model.json";
import type {
  DashboardData, DataModel, DictionaryHit, Phase, ReportSpec, SessionState, SessionSummary, StreamEvent, TranscriptItem,
} from "../types";
import { ApiError, type Api } from "./client";

const demoSpec = demoSpecJson as unknown as ReportSpec;
const demoData = demoDataJson as unknown as DashboardData;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
let seq = 0;
const uid = (p: string) => `${p}_${Date.now().toString(36)}_${(seq++).toString(36)}`;
const now = () => new Date().toISOString();
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));

function item(role: TranscriptItem["role"], content: string, phase: Phase, extra: Partial<TranscriptItem> = {}): TranscriptItem {
  return { id: uid("t"), role, content, phase, createdAt: now(), ...extra };
}

function demoTranscript(): TranscriptItem[] {
  return [
    item("user", "İnternet satış performansı için yönetim dashboard'u istiyorum.", "requirements"),
    item(
      "assistant",
      "Harika. Netleştirmek için birkaç soru:\n\n1. **Hedef kitle** kim? (Genel müdürlük, bölge müdürleri…)\n2. Hangi **KPI**'lar öncelikli: satış tutarı, işlem adedi, ortalama sepet, aktif müşteri?\n3. **Zaman aralığı**: yılbaşından bugüne mi, son 12 ay mı?",
      "requirements",
    ),
    item("user", "Satış yönetimi için. Dört KPI olsun, 2013 ve 2012 karşılaştırmalı.", "requirements"),
    item("tool", "Gereksinimler kaydedildi", "requirements", {
      tool: { name: "save_requirements", arguments: { report_title: "AdventureWorks Satış Performansı", kpis: ["satış tutarı", "sipariş sayısı", "brüt kâr marjı", "aktif müşteri"], time_range: "2013" }, ok: true, summary: "Gereksinimler kaydedildi", durationMs: 12 },
    }),
    item("tool", "4 tablo bulundu", "data", {
      tool: { name: "search_dictionary", arguments: { query: "internet satış tutarı ülke" }, ok: true, summary: "4 tablo bulundu: FactInternetSales, DimDate, DimSalesTerritory, DimProduct", durationMs: 184 },
    }),
    item("tool", "SQL hatası: kolon bulunamadı", "data", {
      tool: { name: "run_sql", arguments: { sql: "SELECT SalesTerritoryCountry, SUM(Amount) FROM dbo.FactInternetSales GROUP BY SalesTerritoryCountry" }, ok: false, summary: "Hata: \"Amount\" kolonu yok (SalesAmount olabilir)", durationMs: 96 },
    }),
    item("tool", "6 dataset kaydedildi", "data", {
      tool: { name: "save_dataset", arguments: { id: "country_sales", sql: demoSpec.datasets[2].sql }, ok: true, summary: "country_sales kaydedildi · 6 satır", durationMs: 241 },
    }),
    item(
      "assistant",
      "Veri kümeleri hazır:\n\n| Dataset | Satır |\n|---|---|\n| `kpi_summary` | 1 |\n| `monthly_trend` | 20 |\n| `region_sales` | 7 |\n\nŞimdi tasarım aşamasına geçebiliriz. İsterseniz örnek bir dashboard görseli yükleyin.",
      "data",
    ),
  ];
}

interface MockSession extends SessionState {
  updatedAt: string;
  data: DashboardData;
}

const sessions = new Map<string, MockSession>();

function newSession(title = "Yeni rapor"): MockSession {
  const s: MockSession = {
    id: uid("s"),
    title,
    phase: "requirements",
    transcript: [],
    requirements: null,
    datasets: [],
    design_brief: null,
    spec: null,
    spec_version: 0,
    busy: false,
    updatedAt: now(),
    data: { datasets: {} },
  };
  sessions.set(s.id, s);
  return s;
}

function applyDemo(s: MockSession) {
  s.spec = clone(demoSpec);
  s.datasets = clone(demoSpec.datasets);
  s.data = clone(demoData);
  s.phase = "design";
  s.spec_version++;
  s.title = demoSpec.title;
  s.updatedAt = now();
}

// Başlangıç: demo yüklü bir oturum
{
  const s = newSession();
  applyDemo(s);
  s.transcript = demoTranscript();
  s.requirements = {
    report_title: demoSpec.title, business_goal: "İnternet ve bayi satış performansını izlemek", audience: "Satış yönetimi",
    kpis: ["Satış tutarı", "Sipariş sayısı", "Brüt kâr marjı", "Aktif müşteri"], dimensions: ["Ay", "Ülke", "Kategori", "Kanal", "Ürün"],
    time_range: "2013, 2012 ile karşılaştırmalı", filters: ["Bölge grubu", "Ülke", "Ürün kategorisi"],
  };
}

const state = (s: MockSession): SessionState => {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const { updatedAt, data, ...rest } = s;
  return clone(rest);
};

/** mock: filtre anahtarı → dataset alan adı (gerçek backend bunu ilişkilerden çözer) */
function mockFilterFields(s: MockSession): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of s.spec?.filters ?? []) {
    const key = f.table && f.column ? `${f.table}.${f.column}`.toLowerCase() : f.field;
    if (key) out[key] = f.field ?? f.column ?? "";
  }
  return out;
}

function get(id: string): MockSession {
  const s = sessions.get(id);
  if (!s) throw new ApiError(404, "Oturum bulunamadı");
  return s;
}

function validateSpec(spec: ReportSpec): string[] {
  const errs: string[] = [];
  if (!spec || typeof spec !== "object") return ["Spec bir JSON nesnesi olmalı."];
  if (spec.version !== 1) errs.push("version: 1 olmalı");
  if (!spec.title) errs.push("title: zorunlu");
  if (!spec.theme) errs.push("theme: zorunlu");
  if (!Array.isArray(spec.visuals)) errs.push("visuals: dizi olmalı");
  const ids = new Set((spec.datasets ?? []).map((d) => d.id));
  (spec.visuals ?? []).forEach((v, i) => {
    if (v.type !== "text" && v.datasetId && !ids.has(v.datasetId)) errs.push(`visuals[${i}] (${v.id}): datasetId "${v.datasetId}" tanımlı değil`);
    if (!v.position) errs.push(`visuals[${i}] (${v.id}): position zorunlu`);
  });
  return errs;
}

export const mockApi: Api = {
  mock: true,
  async health() {
    await sleep(80);
    return {
      ok: true,
      llm: { reachable: true, model: "mock-llm", base_url: "mock://" },
      vision: { configured: true, model: "mock-vision" },
      data: { ok: true, dialect: "tsql" },
    };
  },
  async listSessions(): Promise<SessionSummary[]> {
    await sleep(60);
    return [...sessions.values()]
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
      .map((s) => ({ id: s.id, title: s.title, phase: s.phase, updatedAt: s.updatedAt }));
  },
  async createSession() {
    await sleep(60);
    return state(newSession());
  },
  async getSession(id) {
    await sleep(40);
    return state(get(id));
  },
  async me() {
    return { username: "KURUM\\demo", display_name: "Demo Kullanıcı", domain: "KURUM", groups: [], role: "standart", source: "windows", domain_joined: true };
  },
  async myAccess() {
    const user = await this.me();
    return { user, role: "standart", policy: { allowed_schemas: ["dbo"], denied_tables: [], allow_pii: false, max_rows: 5000 }, objects: [] };
  },
  async getConnections() {
    const f = { server: "localhost", database: "AdventureWorksDW2025", auth: "windows" as const, username: "", encrypt: true, trust_server_certificate: true };
    return { source: "env" as const, data: f, dictionary: { ...f, database: "BI_Meta", same_as_data: true }, drivers: ["ODBC Driver 18 for SQL Server"],
      driver: "ODBC Driver 18 for SQL Server", default_dictionary_db: "BI_Meta", startup_error: null, file: "backend/config/connections.json" };
  },
  async testConnection() { return { ok: true, server_name: "MOCK", database: "AdventureWorksDW2025", version: "16.0", login: "KURUM\demo" }; },
  async findInstances() { return { local: ["localhost"], network: [] }; },
  async listDatabases() { return { ok: true, databases: ["AdventureWorksDW2025", "BI_Meta"] }; },
  async saveConnections() { return { ok: true, error: null, tables: 26 }; },
  async resetConnections() { return { ok: true, error: null }; },
  async uploadDictionaryExcel(file) { return { ok: true, path: "C:/" + file.name, tables: [] }; },
  dictionaryTemplateUrl() { return "#"; },
  connectionsExportUrl() { return "#"; },
  async getLlm() { return { source: "env" as const, base_url: "http://localhost:8001/v1", model: "qwen", tool_mode: "auto" as const, has_api_key: false,
    vision: { enabled: false, same_as_main: true, base_url: "", model: "" } }; },
  async saveLlm() { return { ok: true, reachable: true }; },
  async resetLlm() { return { ok: true }; },
  async llmModels() { return { ok: true, models: ["qwen"] }; },
  async testLlm() { return { ok: true, model: "qwen", elapsed_ms: 10, reply: "Tamam", tools: "native" as const }; },
  async dictionaryTables() { return { ok: true, tables: [{ name: "meta.dd_tables", columns: ["table_name"], role: "tables" as const }] }; },
  async querySchema() {
    return { role: "standart", max_rows: 1000, allow_pii: false, allowed_schemas: ["dbo"], datasets: [], objects: [
      { id: "dbo.factinternetsales", name: "dbo.FactInternetSales", kind: "fact", subject_area: "Satış", business_name: "İnternet satışları",
        columns: [{ name: "SalesAmount", type: "money", business_name: "Satış tutarı" }, { name: "OrderDateKey", type: "int" }] }] };
  },
  async runQuery() {
    return { ok: true, columns: ["SalesAmount"], types: ["number"], rows: [[123.4], [99]], truncated: false, row_limit: 1000, elapsed_ms: 3, warnings: [] };
  },
  async setStatus(id, status) {
    const s = get(id);
    (s as unknown as { status: string }).status = status;
    return state(s);
  },
  async renameSession(id, title) {
    await sleep(100);
    const s = get(id);
    s.title = title;
    if (s.spec) s.spec.title = title;
    return state(s);
  },
  async deleteSession(id) {
    sessions.delete(id);
  },
  async sendMessage(id, body, onEvent: (ev: StreamEvent) => void) {
    const s = get(id);
    s.busy = true;
    const push = (t: TranscriptItem) => {
      s.transcript.push(t);
      s.updatedAt = now();
      onEvent({ event: "transcript", data: clone(t) });
    };
    await sleep(150);
    push(item("user", body.content, s.phase, body.images?.length ? { images: body.images } : {}));
    if (s.title === "Yeni rapor" && body.content) s.title = body.content.slice(0, 48);
    onEvent({ event: "status", data: { text: "Mesaj değerlendiriliyor…" } });
    await sleep(700);
    onEvent({ event: "status", data: { text: "Veri sözlüğü aranıyor…" } });
    await sleep(600);
    push(
      item("tool", "3 tablo bulundu", s.phase, {
        tool: { name: "search_dictionary", arguments: { query: body.content.slice(0, 60) }, ok: true, summary: "3 tablo bulundu (mock)", durationMs: 412 },
      }),
    );
    if (/demo/i.test(body.content)) {
      applyDemo(s);
      onEvent({ event: "state", data: state(s) });
    }
    await sleep(500);
    push(
      item(
        "assistant",
        `**Mock mod** — backend bağlı değil. Mesajınızı aldım:\n\n> ${body.content.replace(/\n/g, " ") || "(boş)"}\n\n${body.images?.length ? `- ${body.images.length} görsel eklendi\n` : ""}- Gerçek yanıt için backend'i \`http://localhost:8000\` üzerinde çalıştırın.`,
        s.phase,
      ),
    );
    s.busy = false;
    onEvent({ event: "state", data: state(s) });
    onEvent({ event: "done", data: {} });
  },
  async setPhase(id, phase) {
    const s = get(id);
    s.phase = phase;
    s.transcript.push(item("system", `Faz değiştirildi: ${phase}`, phase));
    return state(s);
  },
  async putSpec(id, spec) {
    await sleep(120);
    const s = get(id);
    const errs = validateSpec(spec);
    if (errs.length) throw new ApiError(422, errs[0], errs);
    s.spec = clone(spec);
    s.spec_version++;
    s.updatedAt = now();
    return state(s);
  },
  async loadDemo(id) {
    await sleep(200);
    const s = get(id);
    applyDemo(s);
    s.transcript.push(item("system", "Demo dashboard yüklendi.", "design"));
    return state(s);
  },
  async dashboardData(id, selections) {
    await sleep(250);
    const data = clone(get(id).data);
    // sahte model filtresi: seçimi, filtre alanını (field) taşıyan dataset'lere uygula
    const fields = mockFilterFields(get(id));
    data.applied = {};
    for (const [did, ds] of Object.entries(data.datasets)) {
      for (const sel of selections ?? []) {
        const field = fields[sel.key];
        const idx = field ? ds.columns.indexOf(field) : -1;
        if (idx < 0 || sel.exclude?.includes(did)) continue;
        const set = new Set(sel.values.map(String));
        ds.rows = ds.rows.filter((r) => set.has(String(r[idx])));
        (data.applied[did] ??= []).push(sel.key);
      }
    }
    return data;
  },
  async filters(id) {
    await sleep(120);
    const s = get(id);
    const fields = mockFilterFields(s);
    const bindings: Record<string, Record<string, string>> = {};
    for (const [did, ds] of Object.entries(s.data.datasets))
      for (const [key, field] of Object.entries(fields)) if (ds.columns.includes(field)) (bindings[did] ??= {})[field] = key;
    return {
      bindings,
      filters: (s.spec?.filters ?? []).map((f) => {
        const key = f.table && f.column ? `${f.table}.${f.column}`.toLowerCase() : f.field ?? null;
        const field = key ? fields[key] : undefined;
        const opts = new Set<string>();
        for (const ds of Object.values(s.data.datasets)) {
          const i = field ? ds.columns.indexOf(field) : -1;
          if (i >= 0) for (const r of ds.rows) if (r[i] != null) opts.add(String(r[i]));
        }
        return { id: f.id, label: f.label, type: f.type, field: f.field, key, options: [...opts].sort() };
      }),
    };
  },
  async searchDictionary(q): Promise<DictionaryHit[]> {
    await sleep(150);
    const all: DictionaryHit[] = [
      { table: "dbo.factinternetsales", business_name: "İnternet Satışları", description: "Web sitesi üzerinden bireysel müşterilere yapılan satışlar", score: 12.1, columns: [
        { name: "salesamount", business_name: "Satış Tutarı (USD)", role: "measure" },
        { name: "customerkey", business_name: "Müşteri Anahtarı", role: "key" },
        { name: "orderdatekey", business_name: "Sipariş Tarihi Anahtarı", role: "key" },
      ] },
      { table: "dbo.dimsalesterritory", business_name: "Satış Bölgesi", description: "Satış bölgesi, ülke ve bölge grubu", score: 7.0, columns: [
        { name: "salesterritorycountry", business_name: "Bölge Ülkesi", role: "dimension" },
        { name: "salesterritorygroup", business_name: "Bölge Grubu", role: "dimension" },
      ] },
      { table: "dbo.dimdate", business_name: "Tarih", description: "Takvim ve mali takvim boyutu", score: 5.2, columns: [
        { name: "calendaryear", business_name: "Yıl", role: "dimension" },
      ] },
    ];
    const ql = q.toLocaleLowerCase("tr");
    return all.filter((h) => JSON.stringify(h).toLocaleLowerCase("tr").includes(ql) || ql.length < 3);
  },
  async viewScript(_id, did, name) {
    await sleep(150);
    return { view: `rpt.${name}`, script: `-- mock: CREATE OR ALTER VIEW [rpt].[${name}] AS ... (${did})`, file: `view_scripts/rpt.${name}.sql`, exists: false };
  },
  async useView() {
    throw new ApiError(409, "Mock modunda veritabanı yok; view kullanılamaz.");
  },
  async dataModel(): Promise<DataModel> {
    await sleep(150);
    return clone(demoModelJson as unknown as DataModel);
  },
  exportUrl: () => "/viewer.html",
};
