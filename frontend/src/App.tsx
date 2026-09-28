import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import type { CellValue, CrossSelection, DashboardData, FiltersResponse, Health, Phase, Selection, SessionState, SessionSummary, StreamEvent, TranscriptItem } from "./types";
import { httpApi, type Api } from "./api/client";
import { mockApi } from "./api/mock";
import { TopBar } from "./components/TopBar";
import { Composer, EmptyChat, PHASES, PhaseStepper, Transcript } from "./components/Chat";
import { RightPanel, type Tab } from "./components/RightPanel";

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
  const [chatWidth, setChatWidth] = useState(() => Math.min(640, Math.max(320, Number(lsGet(LS_WIDTH)) || 400)));
  const chatRef = useRef<HTMLElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const showToast = useCallback((m: string) => {
    setToast(m);
    window.setTimeout(() => setToast((t) => (t === m ? null : t)), 6000);
  }, []);

  // ---- sağlık ----
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const h = await api.health();
        if (alive) { setHealth(h); setHealthError(null); }
      } catch (e) {
        if (alive) { setHealth(null); setHealthError(errMsg(e)); }
      }
    };
    void tick();
    const t = window.setInterval(tick, 30000);
    return () => { alive = false; window.clearInterval(t); };
  }, [api]);

  // ---- oturumlar ----
  const refreshSessions = useCallback(async () => {
    try {
      const list = await api.listSessions();
      setSessions([...list].sort((a, b) => (b.updatedAt ?? "").localeCompare(a.updatedAt ?? "")));
      return list;
    } catch (e) {
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
      void refreshSessions();
    } catch (e) {
      showToast(`Oturum oluşturulamadı: ${errMsg(e)}`);
    }
  }, [api, refreshSessions, showToast]);

  useEffect(() => {
    (async () => {
      const list = await refreshSessions();
      if (list === null) return;
      const saved = lsGet(LS_SESSION);
      const pick = list.find((s) => s.id === saved) ?? [...list].sort((a, b) => (b.updatedAt ?? "").localeCompare(a.updatedAt ?? ""))[0];
      if (pick) await openSession(pick.id);
      else await newSession();
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const deleteSession = async (id: string) => {
    try {
      await api.deleteSession(id);
      const list = await refreshSessions();
      if (id === sessionId) {
        const next = list?.find((s) => s.id !== id);
        if (next) await openSession(next.id);
        else await newSession();
      }
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
      const ids = new Set(fi.filters.map((f) => f.id));
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
    if (!window.confirm(`"${label}" fazına geri dönülsün mü? Agent bu fazdan devam edecek.`)) return;
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
    <div className="app">
      <TopBar
        mock={MOCK}
        health={health}
        healthError={healthError}
        sessions={sessions}
        currentId={sessionId}
        currentTitle={state?.title}
        onSelect={(id) => id !== sessionId && void openSession(id)}
        onNew={() => void newSession()}
        onDelete={(id) => void deleteSession(id)}
        busy={streaming}
      />
      <main className="main">
        <aside className="chat" ref={chatRef} style={{ width: chatWidth }}>
          <div className="chat-head">
            <PhaseStepper phase={state?.phase ?? "requirements"} disabled={busy || !state} onBack={backToPhase} />
          </div>
          <Transcript
            items={items}
            status={status}
            busy={busy}
            empty={<EmptyChat onPick={(s) => void send(s, [])} onDemo={() => void loadDemo()} disabled={!state || busy} />}
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
            selections, onSelections: setSelections, cross, onCross: setCross,
          } : undefined}
        />
      </main>
      {toast ? (
        <div className="toast" role="alert" onClick={() => setToast(null)}>
          {toast}
        </div>
      ) : null}
    </div>
  );
}
