import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import type { CellValue, CrossSelection, DashboardData, FiltersResponse, Health, Phase, Selection, SessionState, SessionSummary, StreamEvent, TranscriptItem } from "./types";
import { httpApi, type Api } from "./api/client";
import { mockApi } from "./api/mock";
import { TopBar } from "./components/TopBar";
import { QueryPage } from "./components/QueryPage";
import { SettingsPage } from "./components/SettingsPage";
import { DashboardRenderer } from "./dashboard/DashboardRenderer";
import { Composer, EmptyChat, PHASES, PhaseStepper, Transcript } from "./components/Chat";
import { RightPanel, type Tab } from "./components/RightPanel";
import { Home } from "./components/Home";
import { Sidebar, type Page } from "./components/Sidebar";
import { AccessPage } from "./components/AccessPage";
import { ModelTab } from "./components/ModelTab";
import { Vitrin } from "./components/Vitrin";
import { ReportView } from "./components/ReportView";
import { AdminPage } from "./components/AdminPage";
import { PublishDialog } from "./components/PublishDialog";
import type { Me } from "./types";
import type { ThemePref } from "./components/TopBar";
import { ApiError } from "./api/client";

/** #/ envanter · #/access veri erişimi · #/model veri modeli · #/query sorgu · #/r/<id> rapor tasarımı · #/v/<id> canlı görünüm
 *  #/vitrin yayınlanmış raporlar · #/vitrin/<id> yayın görüntüleme · #/admin yönetim */
function parseRoute(): { view: Page; id: string | null } {
  const h = window.location.hash;
  const vr = /^#\/vitrin\/([A-Za-z0-9]+)/.exec(h);
  if (vr) return { view: "vitrin-report", id: vr[1] };
  if (h.startsWith("#/vitrin")) return { view: "vitrin", id: null };
  if (h.startsWith("#/admin")) return { view: "admin", id: null };
  const m = /^#\/r\/([A-Za-z0-9]+)/.exec(h);
  if (m) return { view: "designer", id: m[1] };
  const v = /^#\/v\/([A-Za-z0-9]+)/.exec(h);
  if (v) return { view: "viewer", id: v[1] };
  if (h.startsWith("#/access")) return { view: "access", id: null };
  if (h.startsWith("#/model")) return { view: "model", id: null };
  if (h.startsWith("#/query")) return { view: "query", id: null };
  if (h.startsWith("#/settings")) return { view: "settings", id: null };
  return { view: "home", id: null };
}
const LS_SIDEBAR = "bi.sidebarCollapsed";
const LS_THEME = "bi.theme";

const MOCK = new URLSearchParams(window.location.search).has("mock");
const LS_SESSION = MOCK ? "bi.mock.session" : "bi.session";
const LS_WIDTH = "bi.chatWidth";

const lsGet = (k: string) => {
  try { return localStorage.getItem(k); } catch { return null; }
};
const lsSet = (k: string, v: string) => {
  try { localStorage.setItem(k, v); } catch { /* yoksay */ }
};

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

export default function App() {
  const api: Api = MOCK ? mockApi : httpApi;
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [route, setRoute] = useState(parseRoute);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [me, setMe] = useState<Me | null>(null);
  const [theme, setTheme] = useState<ThemePref>(() => {
    const v = lsGet(LS_THEME);
    if (v === "light" || v === "dark") return v;
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });
  // gündüz / gece modu: data-theme kök özniteliği (ilk açılışta Windows ayarından başlar)
  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme;
    lsSet(LS_THEME, theme);
  }, [theme]);
  const [sidebarPref, setSidebarPref] = useState<boolean | null>(() => { const v = lsGet(LS_SIDEBAR); return v === null ? null : v === "1"; });
  // tercih yoksa: tasarım sayfasında daralt (sohbet + dashboard'a yer), diğer sayfalarda aç
  const sidebarCollapsed = sidebarPref ?? (route.view === "designer" || route.view === "viewer");
  const [state, setState] = useState<SessionState | null>(null);
  const [pending, setPending] = useState<TranscriptItem | null>(null);
  const [localItems, setLocalItems] = useState<TranscriptItem[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [data, setData] = useState<DashboardData | null>(null);
  const [dataLoading, setDataLoading] = useState(false);
  const [dataError, setDataError] = useState<string | null>(null);
  // model filtreleri (dilimleyiciler + görselden çapraz filtre)
  const [filterInfo, setFilterInfo] = useState<FiltersResponse | null>(null);
  const [selections, setSelections] = useState<Record<string, CellValue[]>>({});
  const [cross, setCross] = useState<CrossSelection | null>(null);
  const [tab, setTab] = useState<Tab>("dashboard");
  const [publishing, setPublishing] = useState(false);
  const canDesign = me?.capabilities?.design ?? true;
  const [chatWidth, setChatWidth] = useState(() => Math.min(640, Math.max(320, Number(lsGet(LS_WIDTH)) || 400)));
  const chatRef = useRef<HTMLElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const showToast = useCallback((m: string) => {
    setToast(m);
    window.setTimeout(() => setToast((t) => (t === m ? null : t)), 6000);
  }, []);

  // ---- sağlık ----
  const refreshHealth = useCallback(async () => {
    try {
      const h = await api.health();
      setHealth(h); setHealthError(null);
    } catch (e) {
      setHealth(null); setHealthError(errMsg(e));
    }
  }, [api]);
  useEffect(() => {
    void refreshHealth();
    const t = window.setInterval(() => void refreshHealth(), 30000);
    return () => window.clearInterval(t);
  }, [refreshHealth]);

  // kullanıcı: backend geç açılırsa bulunana kadar tekrar dene
  useEffect(() => {
    let alive = true;
    let timer = 0;
    const load = () => api.me().then((m) => alive && setMe(m)).catch(() => { if (alive) timer = window.setTimeout(load, 5000); });
    load();
    return () => { alive = false; window.clearTimeout(timer); };
  }, [api]);

  // ---- oturumlar ----
  const refreshSessions = useCallback(async () => {
    try {
      const list = await api.listSessions();
      setSessionsLoading(false);
      setSessions([...list].sort((a, b) => (b.updatedAt ?? "").localeCompare(a.updatedAt ?? "")));
      return list;
    } catch (e) {
      setSessionsLoading(false);
      showToast(`Oturumlar alınamadı: ${errMsg(e)}`);
      return null;
    }
  }, [api, showToast]);

  const openSession = useCallback(async (id: string) => {
    abortRef.current?.abort();
    setStreaming(false);
    setStatus(null);
    setPending(null);
    setLocalItems([]);
    setData(null);
    setDataError(null);
    try {
      const s = await api.getSession(id);
      setState(s);
      setSessionId(s.id);
      lsSet(LS_SESSION, s.id);
    } catch (e) {
      showToast(`Oturum açılamadı: ${errMsg(e)}`);
    }
  }, [api, showToast]);

  const newSession = useCallback(async () => {
    try {
      const s = await api.createSession();
      setState(s);
      setSessionId(s.id);
      setPending(null);
      setLocalItems([]);
      setData(null);
      setTab("dashboard");
      lsSet(LS_SESSION, s.id);
      window.location.hash = `#/r/${s.id}`;
      void refreshSessions();
    } catch (e) {
      showToast(`Oturum oluşturulamadı: ${errMsg(e)}`);
    }
  }, [api, refreshSessions, showToast]);

  // izleyici (viewer): tasarım sayfaları yok, Vitrin'e yönlendir; tasarımcı: oturum listesini yükle
  useEffect(() => {
    if (!me) return;
    if (me.capabilities && !me.capabilities.design) {
      setSessionsLoading(false);
      if (!["vitrin", "vitrin-report"].includes(parseRoute().view)) window.location.hash = "#/vitrin";
    } else {
      void refreshSessions();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me]);

  useEffect(() => {
    const onHash = () => setRoute(parseRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // rota değişince: rapor sayfasıysa o oturumu aç, anasayfaysa listeyi tazele
  useEffect(() => {
    if ((route.view === "designer" || route.view === "viewer") && route.id && route.id !== sessionId) void openSession(route.id);
    if (route.view === "home" && me && canDesign) void refreshSessions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route]);

  const goHome = () => { window.location.hash = "#/"; };
  const goReport = (id: string) => { window.location.hash = `#/r/${id}`; };
  /** envanterden açış: canlıdaki (dashboard'u olan) rapor tam sayfa görünümde, diğerleri tasarım modunda açılır */
  const openFromInventory = (id: string) => {
    const r = sessions.find((x) => x.id === id);
    window.location.hash = r?.status === "live" && r.has_spec ? `#/v/${id}` : `#/r/${id}`;
  };
  const setMode = (m: "designer" | "viewer") => { if (sessionId) window.location.hash = `#/${m === "viewer" ? "v" : "r"}/${sessionId}`; };

  const setReportStatus = async (id: string, status: string) => {
    // canlıya alma = Vitrin'e yayınlama (sürüm + paylaşım)
    if (status === "live" && me?.capabilities?.vitrin) {
      if (id !== sessionId) { window.location.hash = `#/r/${id}`; }
      setPublishing(true);
      return;
    }
    try {
      const s = await api.setStatus(id, status);
      if (id === sessionId) setState(s);
      await refreshSessions();
    } catch (e) {
      showToast(`Statü değiştirilemedi: ${errMsg(e)}`);
    }
  };

  /** Yeniden adlandır; aynı isimde rapor varsa üstüne yazmak için onay ister. */
  const renameReport = async (id: string, title: string): Promise<boolean> => {
    try {
      const s = await api.renameSession(id, title);
      if (id === sessionId) setState(s);
      await refreshSessions();
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        if (!window.confirm(`"${title}" adında başka bir rapor var. Üstüne yazılsın mı? (Diğer rapor silinir.)`)) return false;
        try {
          const s = await api.renameSession(id, title, true);
          if (id === sessionId) setState(s);
          await refreshSessions();
          return true;
        } catch (e2) {
          showToast(`Yeniden adlandırılamadı: ${errMsg(e2)}`);
          return false;
        }
      }
      showToast(`Yeniden adlandırılamadı: ${errMsg(e)}`);
      return false;
    }
  };

  const deleteSession = async (id: string) => {
    try {
      await api.deleteSession(id);
      const list = await refreshSessions();
      if (id === sessionId) {
        setState(null);
        setSessionId(null);
        goHome();
      }
      void list;
    } catch (e) {
      showToast(`Silinemedi: ${errMsg(e)}`);
    }
  };

  // ---- dashboard verisi (spec_version / dataset değişince) ----
  const dataKey = useMemo(() => {
    if (!state) return null;
    const ds = [...(state.datasets ?? []), ...(state.spec?.datasets ?? [])].map((d) => `${d.id}:${d.sql?.length ?? 0}`).join("|");
    if (!ds && !state.spec) return null;
    return `${state.id}#${state.spec_version}#${ds}`;
  }, [state]);

  // seçimler → backend'e gidecek liste: dilimleyiciler + çapraz filtre (kaynak görselin dataset'i hariç)
  const selectionList = useMemo<Selection[]>(() => {
    const out: Selection[] = [];
    for (const f of filterInfo?.filters ?? []) {
      const vals = selections[f.id];
      if (f.key && vals?.length) out.push({ key: f.key, values: vals });
    }
    const dd = filterInfo?.data_date;
    if (dd && selections[dd.key]?.length) out.push({ key: dd.key, values: selections[dd.key] });   // veri tarihi (itibarıyla)
    if (cross) out.push({ key: cross.key, values: [cross.value], exclude: [cross.datasetId] });
    return out;
  }, [filterInfo, selections, cross]);
  const selectionKey = JSON.stringify(selectionList);

  const loadData = useCallback(async (id: string, sel: Selection[] = []) => {
    setDataLoading(true);
    try {
      const d = await api.dashboardData(id, sel);
      setData(d && d.datasets ? d : { datasets: {} });
      setDataError(null);
    } catch (e) {
      setDataError(errMsg(e));
      setData((cur) => cur ?? { datasets: {} });
    } finally {
      setDataLoading(false);
    }
  }, [api]);

  // filtre tanımları + seçenekler (spec/dataset değişince)
  useEffect(() => {
    if (!dataKey || !state?.spec) {
      setFilterInfo(null);
      return;
    }
    let alive = true;
    api.filters(state.id).then((fi) => {
      if (!alive) return;
      setFilterInfo(fi);
      const ids = new Set([...fi.filters.map((f) => f.id), ...(fi.data_date ? [fi.data_date.key] : [])]);
      setSelections((s) => Object.fromEntries(Object.entries(s).filter(([k]) => ids.has(k))));
      setCross((c) => (c && fi.bindings[c.datasetId] ? c : null));
    }).catch(() => alive && setFilterInfo(null));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataKey]);

  // oturum değişince seçimleri sıfırla
  useEffect(() => {
    setSelections({});
    setCross(null);
  }, [sessionId]);

  useEffect(() => {
    if (!dataKey || !state) {
      setData(null);
      return;
    }
    void loadData(state.id, selectionList);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataKey, selectionKey]);

  // Başka bir yerde meşgulse (ör. başka sekme) durumu yokla
  useEffect(() => {
    if (!state?.busy || streaming) return;
    const t = window.setInterval(async () => {
      try {
        const s = await api.getSession(state.id);
        setState(s);
      } catch { /* yoksay */ }
    }, 2500);
    return () => window.clearInterval(t);
  }, [state?.busy, state?.id, streaming, api]);

  // ---- mesaj gönderme (SSE) ----
  const send = useCallback(async (content: string, images: string[]) => {
    if (!state || streaming) return;
    const id = state.id;
    const optimistic: TranscriptItem = {
      id: `pending_${Date.now()}`,
      role: "user",
      content,
      images: images.length ? images : undefined,
      phase: state.phase,
      createdAt: new Date().toISOString(),
    };
    setPending(optimistic);
    setStreaming(true);
    setStatus("Gönderiliyor…");
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const onEvent = (ev: StreamEvent) => {
      switch (ev.event) {
        case "transcript": {
          const item = ev.data;
          if (!item || !item.id) return;
          if (item.role === "user") setPending(null);
          setState((s) => {
            if (!s || s.id !== id) return s;
            const idx = s.transcript.findIndex((t) => t.id === item.id);
            const transcript = idx >= 0 ? s.transcript.map((t, i) => (i === idx ? item : t)) : [...s.transcript, item];
            return { ...s, transcript };
          });
          break;
        }
        case "status":
          setStatus(ev.data?.text ?? null);
          break;
        case "state":
          if (ev.data && ev.data.id === id) {
            setState(ev.data);
            if (ev.data.transcript?.some((t) => t.role === "user" && t.content === content)) setPending(null);
          }
          break;
        case "error": {
          const m = ev.data?.message ?? "Bilinmeyen hata";
          setLocalItems((l) => [...l, { id: `local_err_${Date.now()}`, role: "system", content: `Hata: ${m}`, phase: state.phase, createdAt: new Date().toISOString() }]);
          break;
        }
        case "done":
          setStatus(null);
          break;
      }
    };
    try {
      await api.sendMessage(id, { content, images: images.length ? images : undefined }, onEvent, ctrl.signal);
    } catch (e) {
      setLocalItems((l) => [...l, { id: `local_err_${Date.now()}`, role: "system", content: `Mesaj gönderilemedi: ${errMsg(e)}`, phase: state.phase, createdAt: new Date().toISOString() }]);
    } finally {
      if (abortRef.current === ctrl) abortRef.current = null;
      setStreaming(false);
      setStatus(null);
      setPending(null);
      try {
        const fresh = await api.getSession(id);
        setState((s) => (s && s.id === id ? fresh : s));
      } catch { /* yoksay */ }
      void refreshSessions();
    }
  }, [api, state, streaming, refreshSessions]);

  const backToPhase = async (p: Phase) => {
    if (!state) return;
    const label = PHASES.find((x) => x.id === p)?.label ?? p;
    const fwd = PHASES.findIndex((x) => x.id === p) > PHASES.findIndex((x) => x.id === state.phase);
    if (!window.confirm(fwd ? `"${label}" fazına geçilsin mi? Kayıtlı içerikle devam edilecek.` : `"${label}" fazına geri dönülsün mü? Agent bu fazdan devam edecek.`)) return;
    try {
      const s = await api.setPhase(state.id, p);
      setState(s);
      void refreshSessions();
    } catch (e) {
      showToast(`Faz değiştirilemedi: ${errMsg(e)}`);
    }
  };

  const loadDemo = async () => {
    if (!state) return;
    try {
      const s = await api.loadDemo(state.id);
      setState(s);
      setTab("dashboard");
      void refreshSessions();
    } catch (e) {
      showToast(`Demo yüklenemedi: ${errMsg(e)}`);
    }
  };

  // ---- panel genişliği ----
  const startResize = (e: ReactPointerEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = chatWidth;
    const move = (ev: PointerEvent) => setChatWidth(Math.min(640, Math.max(320, startW + ev.clientX - startX)));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      document.body.classList.remove("is-resizing");
    };
    document.body.classList.add("is-resizing");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  useEffect(() => { lsSet(LS_WIDTH, String(chatWidth)); }, [chatWidth]);

  const busy = streaming || !!state?.busy;
  const items = useMemo(() => {
    const base = state?.transcript ?? [];
    const all = [...base, ...localItems];
    if (pending) all.push(pending);
    return all;
  }, [state?.transcript, localItems, pending]);

  return (
    <div className="shell">
    <Sidebar
      page={route.view}
      collapsed={sidebarCollapsed}
      onToggle={() => { const v = !sidebarCollapsed; setSidebarPref(v); lsSet(LS_SIDEBAR, v ? "1" : "0"); }}
      onNavigate={(p) => { window.location.hash = p === "home" ? "#/" : `#/${p}`; }}
      onNew={() => void newSession()}
      me={me}
      currentReport={state && sessionId ? { id: sessionId, title: state.title } : null}
      busy={streaming}
    />
    <div className="app">
      <TopBar
        page={route.view}
        view={route.view === "designer" || route.view === "viewer" ? route.view : "home"}
        onMode={setMode}
        canView={!!state?.spec}
        onHome={goHome}
        onRename={(t) => (sessionId ? renameReport(sessionId, t) : Promise.resolve(false))}
        status={state?.status ?? (state?.spec ? "design" : "idea")}
        onStatus={(st) => sessionId && void setReportStatus(sessionId, st)}
        theme={theme}
        onTheme={setTheme}
        mock={MOCK}
        sessions={sessions}
        currentId={sessionId}
        currentTitle={state?.title}
        onSelect={(id) => { if (id !== sessionId) window.location.hash = `#/${route.view === "viewer" ? "v" : "r"}/${id}`; }}
        onDelete={(id) => void deleteSession(id)}
        busy={streaming}
        health={health}
        healthError={healthError}
        onHealthClick={() => { if (me?.capabilities?.admin ?? true) window.location.hash = "#/settings"; }}
        onPublish={me?.capabilities?.vitrin && canDesign ? () => setPublishing(true) : undefined}
        published={sessions.find((x) => x.id === sessionId)?.published}
      />
      {publishing && sessionId ? (
        <PublishDialog api={api} sessionId={sessionId} onClose={() => { setPublishing(false); void refreshSessions(); }}
          onPublished={(s, rid) => { setPublishing(false); setState(s); void refreshSessions(); showToast(`Vitrin'de yayınlandı. Açmak için: Vitrin → ${s.title}`); void rid; }} />
      ) : null}
      {route.view === "vitrin" ? (
        <main className="main main-home"><Vitrin api={api} me={me} onOpen={(id) => { window.location.hash = `#/vitrin/${id}`; }} /></main>
      ) : route.view === "vitrin-report" && route.id ? (
        <main className="main main-viewer"><ReportView api={api} id={route.id} onBack={() => { window.location.hash = "#/vitrin"; }} /></main>
      ) : route.view === "admin" ? (
        <main className="main main-home"><AdminPage api={api} onOpenReport={(id) => { window.location.hash = `#/vitrin/${id}`; }} /></main>
      ) : route.view === "access" ? (
        <main className="main"><AccessPage api={api} onOpenReport={goReport} /></main>
      ) : route.view === "settings" ? (
        <main className="main"><SettingsPage api={api} onSaved={() => { void refreshHealth(); void refreshSessions(); }} /></main>
      ) : route.view === "query" ? (
        <main className="main"><QueryPage api={api} theme={theme} /></main>
      ) : route.view === "model" ? (
        <main className="main"><div className="page-model"><ModelTab api={api} state={null} /></div></main>
      ) : route.view === "viewer" ? (
        <main className="main main-viewer">
          <div className="viewer-page">
            {!state || state.id !== route.id ? (
              <div className="panel-empty"><span className="spinner" /><p>Rapor yükleniyor…</p></div>
            ) : !state.spec ? (
              <div className="panel-empty">
                <h3>Bu raporun henüz dashboard'u yok</h3>
                <p>Canlı görünüm için önce tasarım modunda dashboard oluşturun.</p>
                <button type="button" className="btn btn-primary" onClick={() => setMode("designer")}>Tasarım moduna geç</button>
              </div>
            ) : (
              <>
                {dataError ? <div className="banner-error">Veri alınamadı: {dataError}</div> : null}
                {data ? (
                  <DashboardRenderer spec={state.spec} data={data} loading={dataLoading} model={filterInfo ? {
                    filters: filterInfo.filters, bindings: filterInfo.bindings, applied: data?.applied ?? {},
                    selections, onSelections: setSelections, cross, onCross: setCross, dataDate: filterInfo.data_date,
                  } : undefined} />
                ) : <div className="panel-empty"><span className="spinner" /><p>Veri yükleniyor…</p></div>}
              </>
            )}
          </div>
        </main>
      ) : route.view === "home" ? (
        <main className="main main-home">
          <Home reports={sessions} loading={sessionsLoading} onOpen={openFromInventory} onNew={() => void newSession()}
            onRename={renameReport} onDelete={(id) => void deleteSession(id)} onStatus={(id, st) => void setReportStatus(id, st)}
            exportUrl={(id) => api.exportUrl(id)} />
        </main>
      ) : (
      <main className="main">
        <aside className="chat" ref={chatRef} style={{ width: chatWidth }}>
          <div className="chat-head">
            <PhaseStepper phase={state?.phase ?? "requirements"} disabled={busy || !state} onBack={backToPhase}
              reachable={{ data: !!state?.requirements, design: !!state?.datasets?.length }} />
          </div>
          <Transcript
            items={items}
            status={status}
            busy={busy}
            empty={<EmptyChat api={api} onPick={(s) => void send(s, [])} onDemo={() => void loadDemo()} onOpenReport={goReport} disabled={!state || busy} />}
          />
          <Composer disabled={!state || busy} busy={busy} onSend={(t, im) => void send(t, im)} visionReady={health ? health.vision.configured : null} dropTarget={chatRef} />
        </aside>
        <div className="resizer" onPointerDown={startResize} role="separator" aria-orientation="vertical" aria-label="Panel genişliği" />
        <RightPanel
          api={api}
          state={state}
          data={data}
          dataLoading={dataLoading}
          dataError={dataError}
          tab={tab}
          setTab={setTab}
          onSpecApplied={(s) => setState(s)}
          onReloadData={() => state && void loadData(state.id, selectionList)}
          model={filterInfo ? {
            filters: filterInfo.filters, bindings: filterInfo.bindings, applied: data?.applied ?? {},
            selections, onSelections: setSelections, cross, onCross: setCross, dataDate: filterInfo.data_date,
          } : undefined}
        />
      </main>
      )}
      {toast ? (
        <div className="toast" role="alert" onClick={() => setToast(null)}>
          {toast}
        </div>
      ) : null}
    </div>
    </div>
  );
}
