// Bağlantı Ayarları: veri kaynağı (SQL Server instance + veritabanı) ve veri sözlüğü (varsayılan: aynı sunucuda BI_Meta).
// Her kart (veri kaynağı / sözlük / LLM) ayrı kaydedilir; kaydedince backend yeniden başlatılmadan servisler kurulur.
// Kartlar küçültülüp açılabilir, tam / yarım genişliğe alınabilir.
import { useEffect, useRef, useState } from "react";
import type { Api } from "../api/client";
import type { ConnectionSettings, ConnFields, ConnTestResult, DictCandidates, DictKind, DictRole, LlmSettings } from "../types";
import { LlmSettingsCard } from "./LlmSettingsCard";
import { SettingsCardHead, useCardLayout } from "./SettingsCardHead";
import "./settings.css";

type Target = "data" | "dictionary";
type Busy = null | "load" | "instances" | "dict-tables" | `db-${Target}` | `test-${Target}` | `save-${Target}` | `reset-${Target}`;
type Msg = { ok: boolean; text: string } | null;

const DICT_ROLES: { id: DictRole; label: string; hint: string; must: boolean; empty: string }[] = [
  { id: "tables", label: "Tablolar", must: false, empty: "Seçilmedi — tablolar Kolonlar'dan çıkarılır",
    hint: "table_name (+ business_name, description, subject_area, grain, row_count, table_type). Seçilmezse tablo listesi Kolonlar'daki table_name'den çıkarılır." },
  { id: "columns", label: "Kolonlar", must: true, empty: "En az bir tablo seçin",
    hint: "table_name, column_name (+ business_name, description, data_type, column_role, default_aggregation, synonyms, is_pii, sample_values). Tek tablolu sözlükte tablo bilgileri de burada olabilir: table_business_name, table_description, subject_area, grain, row_count, table_type. Türkçe başlıklar da tanınır (Tablo Adı, Kolon Adı, Açıklama, Kişisel Veri …)." },
  { id: "relationships", label: "İlişkiler", must: false, empty: "Seçilmedi — SQL Server foreign key'lerinden / anahtar kolon adlarından otomatik bulunur",
    hint: "from_table, from_column, to_table, to_column (+ relationship_id, cardinality, role, is_active). Seçilmezse ilişkiler foreign key'lerden, yoksa ProductKey gibi anahtar kolon adlarından çıkarılır." },
  { id: "metrics", label: "Metrikler", must: false, empty: "Kullanılmıyor",
    hint: "metric_name, expression_sql (+ business_name, description, base_table, value_format, synonyms)" },
];
const REL_SRC: Record<string, string> = { foreign_keys: "foreign key'lerden", name_match: "anahtar kolon adlarından", none: "bulunamadı" };
const EMPTY_SOURCES: Record<DictRole, string[]> = { tables: [], columns: [], relationships: [], metrics: [] };
const DEFAULTS_BY_KIND: Record<DictKind, Record<DictRole, string[]>> = {
  sqlserver: { tables: ["meta.dd_tables"], columns: ["meta.dd_columns"], relationships: ["meta.dd_relationships"], metrics: ["meta.dd_metrics"] },
  mysql: { tables: ["dd_tables"], columns: ["dd_columns"], relationships: ["dd_relationships"], metrics: ["dd_metrics"] },
  excel: { tables: ["Tablolar"], columns: ["Kolonlar"], relationships: ["İlişkiler"], metrics: ["Metrikler"] },
  none: { tables: [], columns: [], relationships: [], metrics: [] },
};
const KIND_LABEL: Record<DictKind, string> = { sqlserver: "SQL Server", excel: "Excel dosyası", mysql: "MySQL / MariaDB", none: "Sözlük yok" };

const Ico = ({ d, size = 14 }: { d: string; size?: number }) => (
  <svg viewBox="0 0 16 16" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
);
const P = {
  server: "M2.5 3h11v4h-11zM2.5 9h11v4h-11zM5 5h.01M5 11h.01",
  db: "M3 4c0-1.1 2.2-2 5-2s5 .9 5 2-2.2 2-5 2-5-.9-5-2ZM3 4v8c0 1.1 2.2 2 5 2s5-.9 5-2V4M3 8c0 1.1 2.2 2 5 2s5-.9 5-2",
  book: "M3 2.5h7.5a2 2 0 0 1 2 2v9H5a2 2 0 0 1-2-2zM3 11.5a2 2 0 0 1 2-2h7.5",
  search: "M7 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10ZM10.7 10.7 14 14",
  plug: "M6 2.5v3M10 2.5v3M4.5 5.5h7v2.5a3.5 3.5 0 0 1-7 0zM8 11.5v2.5",
  list: "M5.5 4h8M5.5 8h8M5.5 12h8M2.5 4h.01M2.5 8h.01M2.5 12h.01",
};

function errText(e: unknown) { return e instanceof Error ? e.message : String(e); }

export function SettingsPage({ api, onSaved }: { api: Api; onSaved?: () => void }) {
  const [cfg, setCfg] = useState<ConnectionSettings | null>(null);
  const [data, setData] = useState<ConnFields | null>(null);
  const [dict, setDict] = useState<ConnFields | null>(null);
  const [instances, setInstances] = useState<{ local: string[]; network: string[] } | null>(null);
  const [dbs, setDbs] = useState<Record<Target, string[] | null>>({ data: null, dictionary: null });
  const [tests, setTests] = useState<Record<Target, ConnTestResult | null>>({ data: null, dictionary: null });
  const [busy, setBusy] = useState<Busy>("load");
  const [msgs, setMsgs] = useState<Record<"page" | Target, Msg>>({ page: null, data: null, dictionary: null });
  const setMsg = (where: "page" | Target, m: Msg) => setMsgs((s) => ({ ...s, [where]: m }));
  const { layout, set: setLayout, all: setAllCollapsed } = useCardLayout();
  const [loadError, setLoadError] = useState<string | null>(null);
  const [cands, setCands] = useState<DictCandidates | null>(null);
  const [addDraft, setAddDraft] = useState<Record<DictRole, string>>({ tables: "", columns: "", relationships: "", metrics: "" });
  const fileRef = useRef<HTMLInputElement>(null);
  const importRef = useRef<HTMLInputElement>(null);
  const [llmImport, setLlmImport] = useState<Partial<LlmSettings> | null>(null);

  const load = async (only?: Target) => {
    setBusy("load");
    try {
      const c = await api.getConnections();
      setCfg(c);
      if (only !== "dictionary") setData({ ...c.data, password: null });
      if (only !== "data") setDict({ ...c.dictionary, password: null, same_as_data: c.dictionary.same_as_data ?? true });
      setLoadError(null);
    } catch (e) { setLoadError(errText(e)); }
    setBusy(null);
  };
  useEffect(() => { void load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (loadError) return <div className="settings"><div className="st-alert is-bad"><b>Ayarlar alınamadı</b><p>{loadError}</p></div></div>;
  if (!cfg || !data || !dict) return <div className="settings"><div className="panel-empty"><span className="spinner" /><p>Yükleniyor…</p></div></div>;

  const upd = (t: Target, patch: Partial<ConnFields>) => {
    (t === "data" ? setData : setDict)((f) => (f ? { ...f, ...patch } : f));
    setTests((s) => ({ ...s, [t]: null }));
    if ("server" in patch || "auth" in patch || "username" in patch || "password" in patch) setDbs((s) => ({ ...s, [t]: null }));
    setMsg(t, null);
  };
  // "aynı sunucu" seçiliyse sözlüğün bağlantı alanları veri kaynağından gelir
  const kind: DictKind = dict.kind ?? "sqlserver";
  const dictEff: ConnFields = kind === "sqlserver" && dict.same_as_data
    ? { ...data, database: dict.database, same_as_data: true, sources: dict.sources, kind } : { ...dict, kind };
  const setKind = (k: DictKind) => {
    if (k === kind) return;
    upd("dictionary", {
      kind: k, sources: DEFAULTS_BY_KIND[k], same_as_data: k === "sqlserver",
      ...(k === "mysql" ? { server: dict.kind === "mysql" ? dict.server : "", port: dict.port ?? 3306, auth: "sql" as const, database: "", encrypt: false } : {}),
      ...(k === "sqlserver" ? { database: cfg.default_dictionary_db, server: data.server, auth: data.auth } : {}),
    });
    setCands(null);
    setDbs((s) => ({ ...s, dictionary: null }));
  };
  const uploadExcel = async (file: File) => {
    setBusy("dict-tables");
    try {
      const r = await api.uploadDictionaryExcel(file);
      upd("dictionary", { excel_path: r.path });
      setCands({ ok: true, tables: r.tables });
      // yüklenen dosyanın sayfaları önerilen rollere göre otomatik seçilir (yoksa varsayılan adlar kalır)
      const next: Record<DictRole, string[]> = { tables: [], columns: [], relationships: [], metrics: [] };
      for (const t of r.tables) if (t.role) next[t.role].push(t.name);
      if (next.tables.length && next.columns.length) upd("dictionary", { excel_path: r.path, sources: next });
    } catch (e) { setCands({ ok: false, error: errText(e), tables: [] }); }
    setBusy(null);
  };

  const sources: Record<DictRole, string[]> = { ...EMPTY_SOURCES, ...(dict.sources ?? {}) };
  const setSources = (role: DictRole, list: string[]) => upd("dictionary", { sources: { ...sources, [role]: list } });
  const addSource = (role: DictRole, name: string) => {
    const n = name.trim();
    if (!n || sources[role].some((x) => x.toLowerCase() === n.toLowerCase())) return;
    setSources(role, [...sources[role], n]);
    setAddDraft((d) => ({ ...d, [role]: "" }));
  };
  const loadCands = async () => {
    setBusy("dict-tables");
    try { setCands(await api.dictionaryTables(data, dictEff)); } catch (e) { setCands({ ok: false, error: errText(e), tables: [] }); }
    setBusy(null);
  };

  const findInstances = async () => {
    setBusy("instances");
    try { setInstances(await api.findInstances()); } catch (e) { setMsg("data", { ok: false, text: errText(e) }); }
    setBusy(null);
  };
  const listDbs = async (t: Target) => {
    setBusy(`db-${t}`);
    try {
      const r = await api.listDatabases(t, data, dictEff);
      setDbs((s) => ({ ...s, [t]: r.databases }));
      if (!r.ok) setTests((s) => ({ ...s, [t]: { ok: false, error: r.error } }));
    } catch (e) { setTests((s) => ({ ...s, [t]: { ok: false, error: errText(e) } })); }
    setBusy(null);
  };
  const test = async (t: Target) => {
    setBusy(`test-${t}`);
    try { const r = await api.testConnection(t, data, dictEff); setTests((s) => ({ ...s, [t]: r })); }
    catch (e) { setTests((s) => ({ ...s, [t]: { ok: false, error: errText(e) } })); }
    setBusy(null);
  };
  const save = async (t: Target) => {
    setBusy(`save-${t}`);
    try {
      const r = t === "data" ? await api.saveDataConnection(data) : await api.saveDictionaryConnection(dictEff, data);
      setMsg(t, r.ok ? { ok: true, text: `Kaydedildi ve uygulandı. Veri sözlüğünde ${r.tables} tablo yüklendi.` }
        : { ok: false, text: `Kaydedildi ama bağlantıda sorun var: ${r.error}` });
      await load(t);
      onSaved?.();
    } catch (e) { setMsg(t, { ok: false, text: errText(e) }); }
    setBusy(null);
  };
  /** Dışa aktarılmış ayar dosyasını forma yükler (kaydetmez): şifre girilip test edildikten sonra "Kaydet ve uygula". */
  const importSettings = async (file: File) => {
    try {
      const d = JSON.parse(await file.text());
      if (d?.type !== "connection-settings" || !d.data || !d.dictionary) throw new Error("Bu dosya bir BI Lens bağlantı ayarları dosyası değil.");
      const base: ConnFields = { server: "localhost", database: "", auth: "windows", username: "", encrypt: true, trust_server_certificate: true };
      const nd: ConnFields = { ...base, ...d.data, password: null, has_password: false };
      const dk = (["sqlserver", "excel", "mysql", "none"].includes(d.dictionary.kind) ? d.dictionary.kind : "sqlserver") as DictKind;
      const ndict: ConnFields = { ...base, ...d.dictionary, kind: dk, password: null, has_password: false,
        sources: { ...EMPTY_SOURCES, ...(d.dictionary.sources ?? DEFAULTS_BY_KIND[dk]) } };
      setData(nd);
      setDict(ndict);
      if (d.llm && typeof d.llm === "object") setLlmImport({ ...d.llm });
      setTests({ data: null, dictionary: null });
      setDbs({ data: null, dictionary: null });
      setCands(null);
      const needPwd = nd.auth === "sql" || (dk !== "excel" && ndict.auth === "sql") || dk === "mysql";
      setMsg("page", { ok: true, text: `"${file.name}" yüklendi (${d.exported_by ? `${d.exported_by} · ` : ""}${(d.exported_at ?? "").slice(0, 10)}) — henüz KAYDEDİLMEDİ. `
        + (needPwd ? "SQL / MySQL şifresini girin, " : "") + "bağlantıları test edip her kartta Kaydet ve uygula'ya basın."
        + (Array.isArray(d.notes) && d.notes.length > 1 ? " " + d.notes.slice(1).join(" ") : "") });
    } catch (e) {
      setMsg("page", { ok: false, text: `Ayar dosyası okunamadı: ${errText(e)}` });
    }
  };

  const reset = async (t: Target) => {
    const what = t === "data" ? "veri kaynağı ayarı silinsin ve backend/.env (SQLSERVER_ODBC)" : "veri sözlüğü ayarı silinsin ve dictionary.toml";
    if (!window.confirm(`Arayüzden kaydedilen ${what} ayarına dönülsün mü?`)) return;
    setBusy(`reset-${t}`);
    try {
      const r = await api.resetConnectionSection(t);
      setMsg(t, r.ok ? { ok: true, text: t === "data" ? ".env ayarına dönüldü." : "dictionary.toml ayarına dönüldü." }
        : { ok: false, text: `Dönüldü ama bağlantıda sorun var: ${r.error}` });
      await load(t);
      onSaved?.();
    } catch (e) { setMsg(t, { ok: false, text: errText(e) }); }
    setBusy(null);
  };
  const cardFoot = (t: Target, canSave: boolean) => {
    const m = msgs[t];
    const src = t === "data" ? cfg.data_source : cfg.dictionary_source;
    return (
      <div className="st-llm-foot">
        {m ? <div className={`st-result ${m.ok ? "is-ok" : "is-bad"}`} role="status">{m.text}</div> : <span />}
        <div className="st-row">
          {src === "ui" ? <button type="button" className="btn btn-ghost btn-sm" onClick={() => void reset(t)} disabled={!!busy}>
            {busy === `reset-${t}` ? <span className="spinner" /> : null} {t === "data" ? ".env ayarına dön" : "dictionary.toml ayarına dön"}</button> : null}
          <button type="button" className="btn btn-primary btn-sm" onClick={() => void save(t)} disabled={!!busy || !canSave}>
            {busy === `save-${t}` ? <span className="spinner" /> : null} {t === "data" ? "Veri kaynağını kaydet ve uygula" : "Sözlüğü kaydet ve uygula"}
          </button>
        </div>
      </div>
    );
  };
  const authText = (f: ConnFields) => (f.auth === "sql" ? `SQL: ${f.username || "—"}` : "Windows oturumu");
  const dataSummary = <>{data.server || "—"} · <b>{data.database || "veritabanı seçilmedi"}</b> · {authText(data)}</>;
  const dictSummary = kind === "none" ? <>Sözlük yok — yalnız veritabanı kataloğu</>
    : kind === "excel" ? <>Excel · <b>{(dict.excel_path ?? "").split(/[\\/]/).pop() || "dosya seçilmedi"}</b></>
    : kind === "mysql" ? <>MySQL · {dict.server || "—"}:{dict.port ?? 3306} · <b>{dict.database || "—"}</b></>
    : <>SQL Server · {dict.same_as_data ? "veri sunucusu" : dict.server || "—"} · <b>{dict.database || "—"}</b> · {(sources.columns ?? []).length + (sources.tables ?? []).length} kaynak tablo</>;

  const allInstances = [...(instances?.local ?? []), ...(instances?.network ?? [])];

  const connFields = (t: Target, f: ConnFields) => (
    <>
      <div className="st-field">
        <label htmlFor={`${t}-server`}>Sunucu / instance</label>
        <div className="st-row">
          <input id={`${t}-server`} className="st-input" list="st-instances" value={f.server} placeholder="SUNUCU\INSTANCE veya SUNUCU,1433"
            onChange={(e) => upd(t, { server: e.target.value })} spellCheck={false} />
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => void findInstances()} disabled={!!busy}
            title="Bu bilgisayardaki ve ağdaki SQL Server instance'larını bul">
            {busy === "instances" ? <span className="spinner" /> : <Ico d={P.search} />} Bul
          </button>
        </div>
        {instances && t === "data" ? (
          <div className="st-chips">
            {allInstances.length ? allInstances.map((x) => (
              <button key={x} type="button" className={`st-chip-btn${x === f.server ? " is-on" : ""}`} onClick={() => upd(t, { server: x })}>
                <Ico d={P.server} size={12} /> {x}{instances.network.includes(x) ? <span className="muted"> · ağ</span> : null}
              </button>
            )) : <span className="muted small">Instance bulunamadı; adını elle yazın (ör. SUNUCU\INSTANCE).</span>}
          </div>
        ) : null}
      </div>

      <div className="st-field">
        <label>Kimlik doğrulama</label>
        <div className="st-seg" role="group">
          <button type="button" className={f.auth === "windows" ? "is-on" : undefined} onClick={() => upd(t, { auth: "windows" })}>Windows (oturum)</button>
          <button type="button" className={f.auth === "sql" ? "is-on" : undefined} onClick={() => upd(t, { auth: "sql" })}>SQL Server kullanıcısı</button>
        </div>
      </div>
      {f.auth === "sql" ? (
        <div className="st-grid2">
          <div className="st-field">
            <label htmlFor={`${t}-user`}>Kullanıcı adı</label>
            <input id={`${t}-user`} className="st-input" value={f.username} autoComplete="off" onChange={(e) => upd(t, { username: e.target.value })} />
          </div>
          <div className="st-field">
            <label htmlFor={`${t}-pwd`}>Şifre</label>
            <input id={`${t}-pwd`} className="st-input" type="password" autoComplete="new-password" value={f.password ?? ""}
              placeholder={f.has_password ? "•••••• (kayıtlı; değiştirmek için yazın)" : ""} onChange={(e) => upd(t, { password: e.target.value })} />
          </div>
        </div>
      ) : null}
      <details className="st-adv">
        <summary>Gelişmiş</summary>
        <label className="st-check"><input type="checkbox" checked={f.encrypt} onChange={(e) => upd(t, { encrypt: e.target.checked })} /> Şifreli bağlantı (Encrypt)</label>
        <label className="st-check"><input type="checkbox" checked={f.trust_server_certificate} onChange={(e) => upd(t, { trust_server_certificate: e.target.checked })} /> Sunucu sertifikasına güven (kendi imzalı sertifika)</label>
      </details>
    </>
  );

  const dbField = (t: Target, f: ConnFields, placeholder: string) => (
    <div className="st-field">
      <label htmlFor={`${t}-db`}>Veritabanı</label>
      <div className="st-row">
        <input id={`${t}-db`} className="st-input" list={`st-dbs-${t}`} value={f.database} placeholder={placeholder}
          onChange={(e) => upd(t, { database: e.target.value })} spellCheck={false} />
        <datalist id={`st-dbs-${t}`}>{(dbs[t] ?? []).map((d) => <option key={d} value={d} />)}</datalist>
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => void listDbs(t)} disabled={!!busy} title="Sunucudaki erişilebilir veritabanlarını listele">
          {busy === `db-${t}` ? <span className="spinner" /> : <Ico d={P.list} />} Listele
        </button>
      </div>
      {dbs[t]?.length ? (
        <div className="st-chips">
          {dbs[t]!.map((d) => <button key={d} type="button" className={`st-chip-btn${d === f.database ? " is-on" : ""}`} onClick={() => upd(t, { database: d })}><Ico d={P.db} size={12} /> {d}</button>)}
        </div>
      ) : null}
    </div>
  );

  const testBox = (t: Target) => {
    const r = tests[t];
    return (
      <div className="st-test">
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => void test(t)} disabled={!!busy}>
          {busy === `test-${t}` ? <span className="spinner" /> : <Ico d={P.plug} />} Bağlantıyı test et
        </button>
        {r ? (
          <div className={`st-result ${r.ok ? "is-ok" : "is-bad"}`} role="status">
            {r.ok ? (
              <>
                <b>✓ Bağlandı</b> — {r.server_name} / {r.database}
                <span className="muted"> · {r.version}{r.login ? ` · ${r.login}` : ""}{r.driver ? ` · ${r.driver}` : ""}</span>
                {r.dictionary_counts ? <div>Sözlükte <b>{r.dictionary_counts.tables ?? 0}</b> tablo{r.derived_tables ? ` (${r.derived_tables} kolonlardan)` : ""}, <b>{r.dictionary_counts.columns ?? 0}</b> kolon, <b>{r.dictionary_counts.relationships ?? 0}</b> ilişki{r.relationship_source ? ` (otomatik: ${REL_SRC[r.relationship_source]})` : ""}, <b>{r.dictionary_counts.metrics ?? 0}</b> metrik tanımı bulundu.</div>
                  : r.dictionary_tables !== undefined ? <div>Sözlükte <b>{r.dictionary_tables}</b> tablo tanımı bulundu.</div> : null}
              </>
            ) : <><b>{r.server_name ? "✕ Bağlandı ama sözlük tabloları hatalı" : "✕ Bağlanılamadı"}</b><div>{r.error}</div></>}
          </div>
        ) : null}
      </div>
    );
  };

  return (
    <div className="settings">
      <datalist id="st-instances">{allInstances.map((x) => <option key={x} value={x} />)}</datalist>

      {cfg.startup_error ? (
        <div className="st-alert is-bad"><b>Şu anki bağlantıda sorun var</b><p>{cfg.startup_error}</p></div>
      ) : null}
      <div className="st-toolbar">
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setAllCollapsed(true)} title="Tüm kartları küçült">
          <Ico d="M4 10l4-4 4 4" /> Tümünü daralt
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setAllCollapsed(false)} title="Tüm kartları aç">
          <Ico d="M4 6l4 4 4-4" /> Tümünü genişlet
        </button>
        <span className="st-toolbar-sep" />
        <a className="btn btn-secondary btn-sm" href={api.connectionsExportUrl()} download
          title="Geçerli bağlantı ayarlarını JSON dosyası olarak indir (şifreler dahil edilmez) — başka bir bilgisayara taşımak için">
          <Ico d="M8 2.5v8M4.5 7 8 10.5 11.5 7M3 13.5h10" /> Ayarları dışa aktar
        </a>
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => importRef.current?.click()} disabled={!!busy}
          title="Dışa aktarılmış ayar dosyasını forma yükle; test edip kaydedene kadar uygulanmaz">
          <Ico d="M8 13.5v-8M4.5 9 8 5.5 11.5 9M3 2.5h10" /> Ayarları içe aktar
        </button>
        <input ref={importRef} type="file" accept=".json,application/json" hidden
          onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) void importSettings(f); }} />
      </div>
      {msgs.page ? <div className={`st-result st-page-msg ${msgs.page.ok ? "is-ok" : "is-bad"}`} role="status">{msgs.page.text}</div> : null}
      <div className="st-meta muted small">
        Her kart ayrı kaydedilir. Arayüzden kaydedilenler <code>{cfg.file}</code> dosyasında tutulur; kaydedilmemiş bölümde <code>backend/.env</code> / <code>dictionary.toml</code> geçerlidir.
        {" "}ODBC sürücüsü: <b>{cfg.driver ?? "bulunamadı"}</b> (otomatik seçilir{cfg.drivers.length > 1 ? `; kurulu: ${cfg.drivers.join(", ")}` : ""}).
      </div>

      <div className="st-cards">
        <section className={`st-card${layout.data.wide ? " st-card-wide" : ""}${layout.data.collapsed ? " is-collapsed" : ""}`}>
          <SettingsCardHead icon={<Ico d={P.db} size={18} />} title="Veri kaynağı" source={cfg.data_source}
            desc="Raporların verisinin okunduğu SQL Server veritabanı (salt-okunur hesap önerilir)." summary={dataSummary}
            layout={layout.data} onChange={(x) => setLayout("data", x)} />
          {layout.data.collapsed ? null : <>
          {connFields("data", data)}
          {dbField("data", data, "ör. AdventureWorksDW2025")}
          {testBox("data")}
          {cardFoot("data", !!data.database.trim())}
          </>}
        </section>

        <section className={`st-card${layout.dictionary.wide ? " st-card-wide" : ""}${layout.dictionary.collapsed ? " is-collapsed" : ""}`}>
          <SettingsCardHead icon={<Ico d={P.book} size={18} />} iconClass="is-dict" title="Veri sözlüğü" source={cfg.dictionary_source}
            desc={<>Tablo / kolon açıklamaları ve ilişkilerin tutulduğu kaynak. Varsayılan: veri sunucusunda <b>{cfg.default_dictionary_db}</b>.</>}
            summary={dictSummary} layout={layout.dictionary} onChange={(x) => setLayout("dictionary", x)} />
          {layout.dictionary.collapsed ? null : <>
          <div className="st-field">
            <label>Kaynak türü</label>
            <div className="st-seg" role="group" aria-label="Sözlük kaynak türü">
              {(Object.keys(KIND_LABEL) as DictKind[]).map((k) => (
                <button key={k} type="button" className={kind === k ? "is-on" : undefined} onClick={() => setKind(k)}>{KIND_LABEL[k]}</button>
              ))}
            </div>
          </div>
          {kind === "none" ? (
            <p className="muted small st-nodict">
              Ayrı bir veri sözlüğü kullanılmaz: tablo ve view'lar <b>veri kaynağından, yetkinize göre</b> listelenir; açıklamalar veritabanındaki
              <code>MS_Description</code> tanımlarından, ilişkiler <b>foreign key</b>'lerden gelir. Kurum sözlüğü hazır olduğunda SQL Server / Excel / MySQL seçeneğine geçebilirsiniz.
            </p>
          ) : kind === "excel" ? (
            <div className="st-field">
              <label htmlFor="dict-excel">Excel dosyası</label>
              <div className="st-row">
                <input id="dict-excel" className="st-input" value={dict.excel_path ?? ""} placeholder="Dosya yükleyin ya da yol yazın (ör. \\sunucu\paylasim\sozluk.xlsx)"
                  onChange={(e) => upd("dictionary", { excel_path: e.target.value })} spellCheck={false} />
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => fileRef.current?.click()} disabled={!!busy}>
                  {busy === "dict-tables" ? <span className="spinner" /> : <Ico d="M8 13.5v-9M4.5 8 8 4.5 11.5 8M3 2.5h10" />} Dosya yükle
                </button>
                <input ref={fileRef} type="file" accept=".xlsx,.xlsm" hidden onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) void uploadExcel(f); }} />
              </div>
              <p className="muted small">
                Her sayfa bir tablo gibi okunur; ilk satır kolon başlıklarıdır.{" "}
                Mevcut sözlüğü Excel şablonu olarak indir: <a href={api.dictionaryTemplateUrl("multi")} download>4 sayfa</a> ya da <a href={api.dictionaryTemplateUrl("single")} download>tek sayfa</a> — düzenleyip buradan geri yükleyebilirsiniz.
              </p>
            </div>
          ) : kind === "mysql" ? (
            <>
              <div className="st-grid2 st-grid-host">
                <div className="st-field">
                  <label htmlFor="dict-host">Sunucu</label>
                  <input id="dict-host" className="st-input" value={dict.server} placeholder="mysql.kurum.local" onChange={(e) => upd("dictionary", { server: e.target.value })} spellCheck={false} />
                </div>
                <div className="st-field">
                  <label htmlFor="dict-port">Port</label>
                  <input id="dict-port" className="st-input" type="number" value={dict.port ?? 3306} onChange={(e) => upd("dictionary", { port: Number(e.target.value) || 3306 })} />
                </div>
              </div>
              <div className="st-grid2">
                <div className="st-field">
                  <label htmlFor="dict-muser">Kullanıcı adı</label>
                  <input id="dict-muser" className="st-input" value={dict.username} autoComplete="off" onChange={(e) => upd("dictionary", { username: e.target.value })} />
                </div>
                <div className="st-field">
                  <label htmlFor="dict-mpwd">Şifre</label>
                  <input id="dict-mpwd" className="st-input" type="password" autoComplete="new-password" value={dict.password ?? ""}
                    placeholder={dict.has_password ? "•••••• (kayıtlı; değiştirmek için yazın)" : ""} onChange={(e) => upd("dictionary", { password: e.target.value })} />
                </div>
              </div>
              <label className="st-check"><input type="checkbox" checked={dict.encrypt} onChange={(e) => upd("dictionary", { encrypt: e.target.checked })} /> SSL ile bağlan</label>
              {dbField("dictionary", dictEff, "ör. bi_meta")}
            </>
          ) : (<>
          <label className="st-check st-same">
            <input type="checkbox" checked={!!dict.same_as_data} onChange={(e) => upd("dictionary", { same_as_data: e.target.checked, ...(e.target.checked ? {} : { server: data.server, auth: data.auth, username: data.username }) })} />
            Veri kaynağıyla aynı sunucu ve kimlik bilgileri
          </label>
          {dict.same_as_data ? <p className="muted small st-inherit">Sunucu: <b>{data.server || "—"}</b> · {data.auth === "sql" ? `SQL kullanıcısı ${data.username}` : "Windows oturumu"}</p> : connFields("dictionary", dict)}
          {dbField("dictionary", dictEff, cfg.default_dictionary_db)}
          </>)}
          <div className="st-row st-dict-actions">
            {kind !== "sqlserver" || dict.database !== cfg.default_dictionary_db || !dict.same_as_data || JSON.stringify(sources) !== JSON.stringify(DEFAULTS_BY_KIND.sqlserver) ? (
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setCands(null); upd("dictionary", { kind: "sqlserver", same_as_data: true, database: cfg.default_dictionary_db, sources: DEFAULTS_BY_KIND.sqlserver, server: data.server, auth: data.auth }); }}>
                Varsayılana dön (SQL Server · {cfg.default_dictionary_db}, meta.dd_* tabloları)</button>
            ) : null}
          </div>
          {kind === "none" ? null : <div className="st-field st-sources">
            <div className="st-sources-head">
              <label>{kind === "excel" ? "Sözlük sayfaları" : "Sözlük tabloları"}</label>
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => void loadCands()} disabled={!!busy}
                title={kind === "excel" ? "Excel dosyasındaki sayfaları getir; kolonlarına göre uygun olanlar önerilir" : "Sözlük veritabanındaki tabloları getir; kolonlarına göre uygun olanlar önerilir"}>
                {busy === "dict-tables" ? <span className="spinner" /> : <Ico d={P.list} />} {kind === "excel" ? "Sayfaları getir" : "Tabloları getir"}
              </button>
            </div>
            <div className="st-layouts" role="group" aria-label="Sözlük düzeni">
              <span className="muted small">Düzen:</span>
              <button type="button" className="st-chip-btn" onClick={() => {
                const one = sources.columns[0] ?? sources.tables[0] ?? (kind === "excel" ? "Sözlük" : "");
                upd("dictionary", { sources: { tables: [], columns: one ? [one] : [], relationships: [], metrics: [] } });
              }} title="Tek bir tablo / sayfa: her satır bir kolon; tablolar ve ilişkiler otomatik">Tek tablo</button>
              <button type="button" className="st-chip-btn" onClick={() => upd("dictionary", { sources: DEFAULTS_BY_KIND[kind] })}
                title="Tablolar / Kolonlar / İlişkiler / Metrikler ayrı tablolarda">Ayrı tablolar ({kind === "excel" ? "4 sayfa" : "4 tablo"})</button>
            </div>
            <p className="muted small">Sözlük tek bir {kind === "excel" ? "sayfa" : "tablo"} (her satır bir kolon) ya da ayrı ayrı {kind === "excel" ? "sayfalar" : "tablolar"} olabilir; yalnız <b>Kolonlar</b> zorunlu. Her bölüm birden çok kaynaktan okunabilir, birleştirilir; olmayan isteğe bağlı kolonlar boş sayılır. Tablo adları (table_name) rapor verisinin bulunduğu SQL Server'daki gibi yazılmalı (ör. dbo.FactInternetSales).</p>
            {cands && !cands.ok ? <div className="st-result is-bad">{cands.error}</div> : null}
            {DICT_ROLES.map((r) => {
              const picked = sources[r.id] ?? [];
              const count = tests.dictionary?.dictionary_counts?.[r.id];
              const suggestions = (cands?.tables ?? []).filter((t) => t.role === r.id && !picked.some((p) => p.toLowerCase() === t.name.toLowerCase()));
              return (
                <div key={r.id} className="st-src-row">
                  <div className="st-src-label">
                    <b>{r.label}</b>
                    <span className={`st-src-tag${r.must ? " is-must" : ""}`}>{r.must ? "zorunlu" : "isteğe bağlı"}</span>
                    {count !== undefined ? <span className="st-src-count">{count.toLocaleString("tr-TR")} satır</span> : null}
                    <span className="st-src-hint" title={`Beklenen kolonlar: ${r.hint}`}>?</span>
                  </div>
                  <div className="st-chips">
                    {picked.map((n) => (
                      <span key={n} className="st-src-chip">
                        <Ico d={P.db} size={12} /> {n}
                        <button type="button" onClick={() => setSources(r.id, picked.filter((x) => x !== n))} aria-label={`${n} kaldır`} title="Kaldır">×</button>
                      </span>
                    ))}
                    {!picked.length ? <span className={`small ${r.must ? "st-src-missing" : "muted"}`}>{r.empty}</span> : null}
                    {suggestions.map((t) => (
                      <button key={t.name} type="button" className="st-chip-btn st-suggest" onClick={() => addSource(r.id, t.name)} title={`Kolonlar: ${t.columns.join(", ")}`}>
                        + {t.name}
                      </button>
                    ))}
                  </div>
                  <div className="st-row">
                    <input className="st-input st-input-sm" list="st-dict-tables" value={addDraft[r.id]} placeholder={kind === "excel" ? "sayfa adı ekle" : kind === "mysql" ? "tablo ya da veritabanı.tablo ekle" : "şema.tablo ekle"}
                      onChange={(e) => setAddDraft((d) => ({ ...d, [r.id]: e.target.value }))}
                      onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addSource(r.id, addDraft[r.id]); } }} spellCheck={false} />
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => addSource(r.id, addDraft[r.id])} disabled={!addDraft[r.id].trim()}>Ekle</button>
                  </div>
                </div>
              );
            })}
            <datalist id="st-dict-tables">{(cands?.tables ?? []).map((t) => <option key={t.name} value={t.name} />)}</datalist>
            {tests.dictionary?.warnings?.length ? (
              <details className="st-adv"><summary>{tests.dictionary.warnings.length} uyarı</summary>
                <ul className="st-warn-list">{tests.dictionary.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
              </details>
            ) : null}
          </div>}
          {testBox("dictionary")}
          {cardFoot("dictionary", kind === "excel" ? !!dict.excel_path?.trim() : kind === "none" || !!dictEff.database?.trim())}
          </>}
        </section>
        <LlmSettingsCard api={api} imported={llmImport} onSaved={onSaved} layout={layout.llm} onLayout={(x) => setLayout("llm", x)} />
      </div>

    </div>
  );
}
