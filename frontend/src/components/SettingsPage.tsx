// Bağlantı Ayarları: veri kaynağı (SQL Server instance + veritabanı) ve veri sözlüğü (varsayılan: aynı sunucuda BI_Meta).
// Kaydedince backend yeniden başlatılmadan yeni bağlantılarla servisleri kurar.
import { useEffect, useRef, useState } from "react";
import type { Api } from "../api/client";
import type { ConnectionSettings, ConnFields, ConnTestResult, DictCandidates, DictKind, DictRole } from "../types";
import "./settings.css";

type Target = "data" | "dictionary";
type Busy = null | "load" | "instances" | "dict-tables" | `db-${Target}` | `test-${Target}` | "save" | "reset";

const DICT_ROLES: { id: DictRole; label: string; hint: string; must: boolean }[] = [
  { id: "tables", label: "Tablolar", hint: "table_name (+ business_name, description, subject_area, grain, row_count, table_type)", must: true },
  { id: "columns", label: "Kolonlar", hint: "table_name, column_name (+ business_name, description, data_type, column_role, default_aggregation, synonyms, is_pii, sample_values)", must: true },
  { id: "relationships", label: "İlişkiler", hint: "from_table, from_column, to_table, to_column (+ relationship_id, cardinality, role, is_active)", must: false },
  { id: "metrics", label: "Metrikler", hint: "metric_name, expression_sql (+ business_name, description, base_table, value_format, synonyms)", must: false },
];
const EMPTY_SOURCES: Record<DictRole, string[]> = { tables: [], columns: [], relationships: [], metrics: [] };
const DEFAULTS_BY_KIND: Record<DictKind, Record<DictRole, string[]>> = {
  sqlserver: { tables: ["meta.dd_tables"], columns: ["meta.dd_columns"], relationships: ["meta.dd_relationships"], metrics: ["meta.dd_metrics"] },
  mysql: { tables: ["dd_tables"], columns: ["dd_columns"], relationships: ["dd_relationships"], metrics: ["dd_metrics"] },
  excel: { tables: ["Tablolar"], columns: ["Kolonlar"], relationships: ["İlişkiler"], metrics: ["Metrikler"] },
};
const KIND_LABEL: Record<DictKind, string> = { sqlserver: "SQL Server", excel: "Excel dosyası", mysql: "MySQL / MariaDB" };

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
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [cands, setCands] = useState<DictCandidates | null>(null);
  const [addDraft, setAddDraft] = useState<Record<DictRole, string>>({ tables: "", columns: "", relationships: "", metrics: "" });
  const fileRef = useRef<HTMLInputElement>(null);

  const load = async () => {
    setBusy("load");
    try {
      const c = await api.getConnections();
      setCfg(c);
      setData({ ...c.data, password: null });
      setDict({ ...c.dictionary, password: null, same_as_data: c.dictionary.same_as_data ?? true });
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
    setMsg(null);
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
    try { setInstances(await api.findInstances()); } catch (e) { setMsg({ ok: false, text: errText(e) }); }
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
  const save = async () => {
    setBusy("save");
    try {
      const r = await api.saveConnections(data, dictEff);
      setMsg(r.ok ? { ok: true, text: `Kaydedildi ve uygulandı. Veri sözlüğünde ${r.tables} tablo yüklendi.` }
        : { ok: false, text: `Kaydedildi ama bağlantıda sorun var: ${r.error}` });
      await load();
      onSaved?.();
    } catch (e) { setMsg({ ok: false, text: errText(e) }); }
    setBusy(null);
  };
  const reset = async () => {
    if (!window.confirm("Arayüzden kaydedilen bağlantı ayarları silinsin ve .env / dictionary.toml ayarlarına dönülsün mü?")) return;
    setBusy("reset");
    try {
      const r = await api.resetConnections();
      setMsg(r.ok ? { ok: true, text: ".env / dictionary.toml ayarlarına dönüldü." } : { ok: false, text: `Dönüldü ama bağlantıda sorun var: ${r.error}` });
      await load();
      onSaved?.();
    } catch (e) { setMsg({ ok: false, text: errText(e) }); }
    setBusy(null);
  };

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
                <span className="muted"> · SQL Server {r.version}{r.login ? ` · ${r.login}` : ""}{r.driver ? ` · ${r.driver}` : ""}</span>
                {r.dictionary_counts ? <div>Sözlükte <b>{r.dictionary_counts.tables ?? 0}</b> tablo, <b>{r.dictionary_counts.columns ?? 0}</b> kolon, <b>{r.dictionary_counts.relationships ?? 0}</b> ilişki, <b>{r.dictionary_counts.metrics ?? 0}</b> metrik tanımı bulundu.</div>
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
      <div className="st-meta muted small">
        {cfg.source === "ui" ? <>Ayarlar arayüzden kaydedildi (<code>{cfg.file}</code>).</> : <>Şu an <code>backend/.env</code> ve <code>dictionary.toml</code> ayarları kullanılıyor; kaydedince buradaki ayarlar geçerli olur.</>}
        {" "}ODBC sürücüsü: <b>{cfg.driver ?? "bulunamadı"}</b> (otomatik seçilir{cfg.drivers.length > 1 ? `; kurulu: ${cfg.drivers.join(", ")}` : ""}).
      </div>

      <div className="st-cards">
        <section className="st-card">
          <header className="st-card-head">
            <span className="st-card-icon"><Ico d={P.db} size={18} /></span>
            <div><h2>Veri kaynağı</h2><p className="muted">Raporların verisinin okunduğu SQL Server veritabanı (salt-okunur hesap önerilir).</p></div>
          </header>
          {connFields("data", data)}
          {dbField("data", data, "ör. AdventureWorksDW2025")}
          {testBox("data")}
        </section>

        <section className="st-card">
          <header className="st-card-head">
            <span className="st-card-icon is-dict"><Ico d={P.book} size={18} /></span>
            <div><h2>Veri sözlüğü</h2><p className="muted">Tablo / kolon açıklamaları ve ilişkilerin tutulduğu kaynak. Varsayılan: veri sunucusunda <b>{cfg.default_dictionary_db}</b>.</p></div>
          </header>
          <div className="st-field">
            <label>Kaynak türü</label>
            <div className="st-seg" role="group" aria-label="Sözlük kaynak türü">
              {(Object.keys(KIND_LABEL) as DictKind[]).map((k) => (
                <button key={k} type="button" className={kind === k ? "is-on" : undefined} onClick={() => setKind(k)}>{KIND_LABEL[k]}</button>
              ))}
            </div>
          </div>
          {kind === "excel" ? (
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
                <a href={api.dictionaryTemplateUrl()} download>Mevcut sözlüğü Excel şablonu olarak indir</a> — düzenleyip buradan geri yükleyebilirsiniz.
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
          <div className="st-field st-sources">
            <div className="st-sources-head">
              <label>{kind === "excel" ? "Sözlük sayfaları" : "Sözlük tabloları"}</label>
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => void loadCands()} disabled={!!busy}
                title={kind === "excel" ? "Excel dosyasındaki sayfaları getir; kolonlarına göre uygun olanlar önerilir" : "Sözlük veritabanındaki tabloları getir; kolonlarına göre uygun olanlar önerilir"}>
                {busy === "dict-tables" ? <span className="spinner" /> : <Ico d={P.list} />} {kind === "excel" ? "Sayfaları getir" : "Tabloları getir"}
              </button>
            </div>
            <p className="muted small">Her bölüm bir ya da birden çok {kind === "excel" ? "sayfadan" : "tablodan"} okunabilir; birden çok seçilirse birleştirilir. Olmayan isteğe bağlı kolonlar boş sayılır. Tablo adları (table_name) rapor verisinin bulunduğu SQL Server'daki gibi yazılmalı (ör. dbo.FactInternetSales).</p>
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
                    {!picked.length ? <span className="muted small">{r.must ? "En az bir tablo seçin" : "Kullanılmıyor"}</span> : null}
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
          </div>
          {testBox("dictionary")}
        </section>
      </div>

      <footer className="st-foot">
        {msg ? <div className={`st-result ${msg.ok ? "is-ok" : "is-bad"}`} role="status">{msg.text}</div> : <span />}
        <div className="st-row">
          {cfg.source === "ui" ? <button type="button" className="btn btn-ghost" onClick={() => void reset()} disabled={!!busy}>.env ayarlarına dön</button> : null}
          <button type="button" className="btn btn-primary" onClick={() => void save()} disabled={!!busy || !data.database.trim()}>
            {busy === "save" ? <span className="spinner" /> : null} Kaydet ve uygula
          </button>
        </div>
      </footer>
    </div>
  );
}
