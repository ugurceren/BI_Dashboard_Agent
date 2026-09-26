// ?mock=1 — backend olmadan geliştirme için bellek içi sahte API.
import demoSpecJson from "../mocks/demo_spec.json";
import demoDataJson from "../mocks/demo_data.json";
import type {
  DashboardData, DictionaryHit, Phase, ReportSpec, SessionState, SessionSummary, StreamEvent, TranscriptItem,
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
    item("user", "Kredi kartı satış performansı için yönetim dashboard'u istiyorum.", "requirements"),
    item(
      "assistant",
      "Harika. Netleştirmek için birkaç soru:\n\n1. **Hedef kitle** kim? (Genel müdürlük, bölge müdürleri…)\n2. Hangi **KPI**'lar öncelikli: satış tutarı, işlem adedi, ortalama sepet, aktif müşteri?\n3. **Zaman aralığı**: yılbaşından bugüne mi, son 12 ay mı?",
      "requirements",
    ),
    item("user", "Genel müdürlük. Dört KPI da olsun, 2026 YTD ve geçen yıl karşılaştırması.", "requirements"),
    item("tool", "Gereksinimler kaydedildi", "requirements", {
      tool: { name: "save_requirements", arguments: { report_title: "Kredi Kartı Satış Performansı", kpis: ["satış tutarı", "işlem adedi", "ortalama sepet", "aktif müşteri"], time_range: "2026 YTD" }, ok: true, summary: "Gereksinimler kaydedildi", durationMs: 12 },
    }),
    item("tool", "4 tablo bulundu", "data", {
      tool: { name: "search_dictionary", arguments: { query: "kredi kartı işlem tutarı" }, ok: true, summary: "4 tablo bulundu: fact_card_transaction, dim_date, dim_branch, dim_channel", durationMs: 184 },
    }),
    item("tool", "SQL hatası: kolon bulunamadı", "data", {
      tool: { name: "run_sql", arguments: { sql: "SELECT region, SUM(amount) FROM dwh.fact_card_transaction GROUP BY region" }, ok: false, summary: "Hata: \"amount\" kolonu yok (amount_try olabilir)", durationMs: 96 },
    }),
    item("tool", "6 dataset kaydedildi", "data", {
      tool: { name: "save_dataset", arguments: { id: "region_sales", sql: demoSpec.datasets[2].sql }, ok: true, summary: "region_sales kaydedildi · 7 satır", durationMs: 241 },
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
    report_title: demoSpec.title, business_goal: "Kart satış performansını izlemek", audience: "Genel müdürlük",
    kpis: ["Satış tutarı", "İşlem adedi", "Ortalama sepet", "Aktif müşteri"], dimensions: ["Bölge", "Kategori", "Kanal", "Şube"],
    time_range: "2026 YTD", filters: ["Bölge"],
  };
}

const state = (s: MockSession): SessionState => {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const { updatedAt, data, ...rest } = s;
  return clone(rest);
};

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
      data: { ok: true, dialect: "duckdb" },
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
  async dashboardData(id) {
    await sleep(250);
    return clone(get(id).data);
  },
  async searchDictionary(q): Promise<DictionaryHit[]> {
    await sleep(150);
    const all: DictionaryHit[] = [
      { table: "dwh.fact_card_transaction", business_name: "Kart İşlemleri", description: "Kredi kartı işlem bazında satış tutarları", score: 0.92, columns: [
        { name: "amount_try", business_name: "İşlem Tutarı (TL)", role: "measure" },
        { name: "customer_id", business_name: "Müşteri", role: "key" },
        { name: "date_key", business_name: "Tarih", role: "key" },
      ] },
      { table: "dwh.dim_branch", business_name: "Şube", description: "Şube, il ve bölge bilgileri", score: 0.71, columns: [
        { name: "branch_name", business_name: "Şube Adı", role: "dimension" },
        { name: "region", business_name: "Bölge", role: "dimension" },
      ] },
      { table: "dwh.dim_date", business_name: "Tarih", description: "Takvim boyutu", score: 0.55, columns: [
        { name: "year_month", business_name: "Yıl-Ay", role: "dimension" },
      ] },
    ];
    const ql = q.toLocaleLowerCase("tr");
    return all.filter((h) => JSON.stringify(h).toLocaleLowerCase("tr").includes(ql) || ql.length < 3);
  },
  exportUrl: () => "/viewer.html",
};
