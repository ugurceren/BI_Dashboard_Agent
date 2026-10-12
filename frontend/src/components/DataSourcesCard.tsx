// Bağlantı Ayarları → Kayıtlı veri kaynakları: raporlar ilk veri kümeleri kaydedildiğinde etkin veri kaynağına
// sabitlenir. Bağlantı başka sunucu / veritabanına çevrilince eski kaynak burada saklanır; ona bağlı raporlar verisini
// oradan okumaya devam eder. Şifre değiştiyse kimlik bilgisi güncellenir; hiçbir rapor kullanmıyorsa silinebilir.
// Yayın hesabı: tanımlanırsa Vitrin'deki raporların verisi geniş okuma yetkili bu hesapla okunur (tasarım, sohbet ve
// Sorgu Çalıştır normal bağlantıyla kalır); izleyicinin ne göreceğini yine kendi veri rolü belirler.
import { useState } from "react";
import type { Api } from "../api/client";
import type { DataSourceInfo, PublishAccount, PublishAccountTest } from "../types";
import { SettingsCardHead, type CardLayout } from "./SettingsCardHead";

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const KEY = "bi.settings.sources";
const readLayout = (): CardLayout => {
  try { return { collapsed: true, wide: false, ...JSON.parse(localStorage.getItem(KEY) ?? "{}") }; } catch { return { collapsed: true, wide: false }; }
};
const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("tr-TR") : "");

export function DataSourcesCard({ api, sources, onChanged }: { api: Api; sources: DataSourceInfo[] | null; onChanged: () => void }) {
  const [layout, setLayoutState] = useState<CardLayout>(readLayout);
  const [edit, setEdit] = useState<{ id: string; username: string; password: string } | null>(null);
  const [pubEdit, setPubEdit] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const setLayout = (p: Partial<CardLayout>) => {
    const next = { ...layout, ...p };
    setLayoutState(next);
    try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* yok say */ }
  };
  const archived = (sources ?? []).filter((x) => !x.active);
  const summary = sources === null ? "…" : archived.length
    ? `${sources.length} kaynak · ${archived.length} eski kaynağa bağlı ${archived.reduce((n, x) => n + x.reports, 0)} rapor`
    : "Tüm raporlar etkin veri kaynağında";

  const saveCred = async () => {
    if (!edit) return;
    setBusy(`cred-${edit.id}`); setMsg(null);
    try {
      const r = await api.updateSourceCredentials(edit.id, edit.username, edit.password || null);
      setMsg(r.ok ? { ok: true, text: `Kaydedildi; kaynağa bağlanıldı (${r.tables} tablo).` } : { ok: false, text: r.error ?? "Bağlanılamadı" });
      if (r.ok) setEdit(null);
      onChanged();
    } catch (e) { setMsg({ ok: false, text: errText(e) }); }
    setBusy(null);
  };
  const remove = async (s: DataSourceInfo) => {
    setBusy(`del-${s.id}`); setMsg(null);
    try { await api.deleteSource(s.id); setMsg({ ok: true, text: `${s.label} silindi.` }); onChanged(); }
    catch (e) { setMsg({ ok: false, text: errText(e) }); }
    setBusy(null);
  };

  return (
    <section className={`st-card${layout.wide ? " st-card-wide" : ""}${layout.collapsed ? " is-collapsed" : ""}`}>
      <SettingsCardHead icon={<svg viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><ellipse cx="6" cy="4" rx="4" ry="1.6" /><path d="M2 4v6c0 .9 1.8 1.6 4 1.6M10 4v2.5" /><path d="M9.5 9.5l1.6 1.6 3-3.2" /></svg>}
        title="Kayıtlı veri kaynakları"
        desc={<>Her rapor, veri kümeleri kaydedildiğinde etkin veri kaynağına <b>sabitlenir</b>. Veri kaynağını değiştirseniz de mevcut
          raporlar verisini kendi kaynağından (güncel olarak) okumaya devam eder; erişim her açılışta izleyicinin veri rolüyle denetlenir.
          Her kaynağa bir <b>yayın hesabı</b> tanımlanabilir: Vitrin'deki raporlar bu hesapla okunur.</>}
        summary={summary} layout={layout} onChange={setLayout} />
      {layout.collapsed ? null : <>
        {sources === null ? <p className="muted small"><span className="spinner" /> Yükleniyor…</p> : (
          <ul className="st-sources">
            {sources.map((s) => {
              const used = s.reports + s.published;
              return (
                <li key={s.id} className={`st-source${s.active ? " is-active" : ""}`}>
                  <div className="st-source-main">
                    <b>{s.label}</b>
                    {s.active ? <span className="st-badge is-ok">Etkin</span> : <span className="st-badge">Arşiv · {day(s.archived_at)}</span>}
                    <span className="muted small">{s.reports} rapor{s.published ? ` · ${s.published} yayında` : ""}
                      {" · "}{s.auth === "env" ? ".env bağlantısı" : s.auth === "sql" ? `SQL: ${s.username || "—"}` : "Windows oturumu"}</span>
                    <span className={`st-badge${s.publish.auth !== "none" ? " is-pub" : ""}`} title="Vitrin'deki raporların verisini okuyan hesap">
                      Yayın hesabı: {pubText(s.publish, s.process_account)}</span>
                  </div>
                  <div className="st-row">
                    <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy}
                      onClick={() => setPubEdit(pubEdit === s.id ? null : s.id)}>Yayın hesabı</button>
                  </div>
                  {s.active ? null : (
                    <div className="st-row">
                      {s.auth !== "env" ? (
                        <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy}
                          onClick={() => setEdit(edit?.id === s.id ? null : { id: s.id, username: s.username, password: "" })}>Kimlik bilgisi</button>
                      ) : null}
                      <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy || used > 0}
                        title={used > 0 ? "Bu kaynağa bağlı raporlar var; önce onları silin ya da verilerini yeniden oluşturun." : "Kaynağı sil"}
                        onClick={() => void remove(s)}>
                        {busy === `del-${s.id}` ? <span className="spinner" /> : null} Sil
                      </button>
                    </div>
                  )}
                  {pubEdit === s.id ? (
                    <PublishEditor api={api} source={s} onDone={() => { setPubEdit(null); onChanged(); }} />
                  ) : null}
                  {edit?.id === s.id ? (
                    <div className="st-grid2 st-source-cred">
                      <div className="st-field">
                        <label htmlFor={`src-user-${s.id}`}>SQL kullanıcı adı (boş: Windows oturumu)</label>
                        <input id={`src-user-${s.id}`} className="st-input" value={edit.username} autoComplete="off"
                          onChange={(e) => setEdit({ ...edit, username: e.target.value })} />
                      </div>
                      <div className="st-field">
                        <label htmlFor={`src-pwd-${s.id}`}>Şifre</label>
                        <input id={`src-pwd-${s.id}`} className="st-input" type="password" autoComplete="new-password" value={edit.password}
                          placeholder={s.has_password ? "•••••• (kayıtlı; değiştirmek için yazın)" : ""}
                          onChange={(e) => setEdit({ ...edit, password: e.target.value })} />
                      </div>
                      <div className="st-row">
                        <button type="button" className="btn btn-primary btn-sm" disabled={!!busy} onClick={() => void saveCred()}>
                          {busy === `cred-${s.id}` ? <span className="spinner" /> : null} Kaydet ve bağlan
                        </button>
                      </div>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
        {msg ? <div className={`st-result ${msg.ok ? "is-ok" : "is-bad"}`} role="status">{msg.text}</div> : null}
      </>}
    </section>
  );
}

function pubText(p: PublishAccount, proc?: string) {
  if (p.auth === "sql") return `SQL: ${p.username || "—"}`;
  if (p.auth === "windows") return `Windows: ${proc || "hizmet hesabı"}`;
  return "yok (normal bağlantı)";
}

/** Kaynağın yayın (Vitrin) hesabı: seç, test et, kaydet. */
function PublishEditor({ api, source, onDone }: { api: Api; source: DataSourceInfo; onDone: () => void }) {
  const p = source.publish;
  const [auth, setAuth] = useState<PublishAccount["auth"]>(p.auth);
  const [username, setUsername] = useState(p.username ?? "");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState<"test" | "save" | null>(null);
  const [test, setTest] = useState<PublishAccountTest | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const body = () => ({ auth, username: username.trim(), password: password || null });
  const pwdSaved = p.auth === "sql" && !!p.has_password && p.username === username.trim();
  const canRun = auth === "windows" || (auth === "sql" && !!username.trim() && (!!password || pwdSaved));
  const pick = (a: PublishAccount["auth"]) => { setAuth(a); setTest(null); setErr(null); };
  const run = async (what: "test" | "save") => {
    setBusy(what); setErr(null);
    try {
      if (what === "test") setTest(await api.testPublishAccount(source.id, body()));
      else { await api.savePublishAccount(source.id, body()); onDone(); }
    } catch (e) { setErr(errText(e)); }
    setBusy(null);
  };
  return (
    <div className="st-source-cred st-pub">
      <div className="st-seg" role="group" aria-label="Yayın hesabı">
        <button type="button" className={auth === "none" ? "is-on" : undefined} onClick={() => pick("none")}>Yok — normal bağlantı</button>
        <button type="button" className={auth === "sql" ? "is-on" : undefined} onClick={() => pick("sql")}>SQL kullanıcısı</button>
        <button type="button" className={auth === "windows" ? "is-on" : undefined} onClick={() => pick("windows")}>Windows hizmet hesabı</button>
      </div>
      {auth === "sql" ? (
        <div className="st-grid2">
          <div className="st-field">
            <label htmlFor={`pub-user-${source.id}`}>SQL kullanıcı adı</label>
            <input id={`pub-user-${source.id}`} className="st-input" value={username} autoComplete="off"
              onChange={(e) => { setUsername(e.target.value); setTest(null); }} />
          </div>
          <div className="st-field">
            <label htmlFor={`pub-pwd-${source.id}`}>Şifre</label>
            <input id={`pub-pwd-${source.id}`} className="st-input" type="password" autoComplete="new-password" value={password}
              placeholder={pwdSaved ? "•••••• (kayıtlı; değiştirmek için yazın)" : ""} onChange={(e) => { setPassword(e.target.value); setTest(null); }} />
          </div>
        </div>
      ) : null}
      <p className="muted small">
        {auth === "none" ? "Vitrin'deki raporlar da tasarımdaki normal bağlantıyla okunur."
          : auth === "windows" ? <>SQL Server'a backend'in çalıştığı hesap bağlanır: <b>{source.process_account || "?"}</b>. Sunucuda bu hesap için
            gMSA (şifresiz yönetilen hizmet hesabı) önerilir.</>
          : "Şifre bu bilgisayarda Windows DPAPI ile şifreli saklanır, arayüze geri gönderilmez."}
        {auth !== "none" ? <> Hesaba gereken veritabanlarında yalnız <b>okuma</b> (db_datareader) yetkisi verin. Bu hesap yalnız yayınlanmış
          raporların sorgularını çalıştırır; izleyicinin ne göreceğini yine kendi veri rolü belirler.</> : null}
      </p>
      {test ? (
        <div className={`st-result ${test.ok && !test.warnings.length ? "is-ok" : "is-bad"}`} role="status">
          {test.login ? <div>Bağlanan hesap: <b>{test.login}</b></div> : null}
          {test.databases.map((d) => (
            <div key={d.database}>{d.database}: {d.ok ? `${d.readable} / ${d.total} tablo ve view okunabiliyor` : <>bağlanılamadı — {d.error}</>}</div>
          ))}
          {test.warnings.map((w) => <div key={w}>⚠ {w}</div>)}
        </div>
      ) : null}
      {err ? <div className="st-result is-bad" role="status">{err}</div> : null}
      <div className="st-row">
        {auth !== "none" ? (
          <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy || !canRun} onClick={() => void run("test")}>
            {busy === "test" ? <span className="spinner" /> : null} Bağlantıyı test et
          </button>
        ) : null}
        <button type="button" className="btn btn-primary btn-sm" disabled={!!busy || (auth !== "none" && !canRun)} onClick={() => void run("save")}>
          {busy === "save" ? <span className="spinner" /> : null} {auth === "none" ? "Kaydet (yayın hesabı yok)" : "Kaydet"}
        </button>
      </div>
    </div>
  );
}
