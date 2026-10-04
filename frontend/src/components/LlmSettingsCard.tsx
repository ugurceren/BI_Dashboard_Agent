// Dil modeli (LLM) bağlantısı: ana model (sohbet + araç çağırma) ve görsel model (dashboard görseli okuma).
// Kaydedince backend yeniden başlatılmadan yeni bağlantıyla devam eder; API anahtarları DPAPI ile şifreli saklanır.
import { useEffect, useState } from "react";
import type { Api } from "../api/client";
import type { LlmSettings, LlmTestResult, ToolMode } from "../types";
import { SettingsCardHead } from "./SettingsCardHead";
import type { CardLayout } from "./SettingsCardHead";

type Target = "main" | "vision";
type Busy = null | "load" | `models-${Target}` | `test-${Target}` | "save" | "reset";

const Ico = ({ d, size = 14 }: { d: string; size?: number }) => (
  <svg viewBox="0 0 16 16" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
);
const P = {
  brain: "M6 2.5a2.5 2.5 0 0 0-2.5 2.5v.3A2.5 2.5 0 0 0 2 7.5v1a2.5 2.5 0 0 0 1.5 2.3v.2A2.5 2.5 0 0 0 6 13.5h.5v-11H6ZM10 2.5A2.5 2.5 0 0 1 12.5 5v.3A2.5 2.5 0 0 1 14 7.5v1a2.5 2.5 0 0 1-1.5 2.3v.2A2.5 2.5 0 0 1 10 13.5h-.5v-11h.5Z",
  eye: "M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8ZM8 10a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z",
  list: "M5.5 4h8M5.5 8h8M5.5 12h8M2.5 4h.01M2.5 8h.01M2.5 12h.01",
  plug: "M6 2.5v3M10 2.5v3M4.5 5.5h7v2.5a3.5 3.5 0 0 1-7 0zM8 11.5v2.5",
};
const TOOL_MODES: { id: ToolMode; label: string; hint: string }[] = [
  { id: "auto", label: "Otomatik", hint: "Önce native araç çağırma; sunucu desteklemiyorsa prompt moduna geçer" },
  { id: "native", label: "Native", hint: "OpenAI uyumlu tools / tool_calls (vLLM: --enable-auto-tool-choice)" },
  { id: "prompt", label: "Prompt", hint: "Araçlar talimatla anlatılır, model JSON yazar (araç çağırmayı desteklemeyen sunucular)" },
];
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function LlmSettingsCard({ api, imported, onSaved, layout = { collapsed: false, wide: true }, onLayout }: {
  api: Api; imported?: Partial<LlmSettings> | null; onSaved?: () => void; layout?: CardLayout; onLayout?: (patch: Partial<CardLayout>) => void;
}) {
  const [llm, setLlm] = useState<LlmSettings | null>(null);
  const [source, setSource] = useState<"ui" | "env">("env");
  const [models, setModels] = useState<Record<Target, string[] | null>>({ main: null, vision: null });
  const [tests, setTests] = useState<Record<Target, LlmTestResult | null>>({ main: null, vision: null });
  const [extraText, setExtraText] = useState("");
  const [busy, setBusy] = useState<Busy>("load");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = async () => {
    setBusy("load");
    try {
      const l = await api.getLlm();
      setSource(l.source ?? "env");
      setLlm({ ...l, api_key: null, vision: { ...l.vision, api_key: null } });
      setExtraText(l.extra_body ? JSON.stringify(l.extra_body, null, 2) : "");
    } catch (e) { setMsg({ ok: false, text: `LLM ayarları alınamadı: ${errText(e)}` }); }
    setBusy(null);
  };
  useEffect(() => { void load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // dışa aktarılmış ayar dosyasından gelen değerler (anahtarsız) forma yüklenir
  useEffect(() => {
    if (!imported || !llm) return;
    setLlm((cur) => cur && ({ ...cur, ...imported, api_key: null, has_api_key: false,
      vision: { ...cur.vision, ...(imported.vision ?? {}), api_key: null, has_api_key: false } }));
    if (imported.extra_body !== undefined) setExtraText(imported.extra_body ? JSON.stringify(imported.extra_body, null, 2) : "");
    setTests({ main: null, vision: null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [imported]);

  const cls = `st-card${layout.wide ? " st-card-wide" : ""}${layout.collapsed ? " is-collapsed" : ""}`;
  if (!llm) return <section className={cls}><div className="panel-empty"><span className="spinner" /><p>Yükleniyor…</p></div></section>;

  const upd = (patch: Partial<LlmSettings>) => { setLlm((c) => (c ? { ...c, ...patch } : c)); setTests((t) => ({ ...t, main: null })); setMsg(null); };
  const updV = (patch: Partial<LlmSettings["vision"]>) => {
    setLlm((c) => (c ? { ...c, vision: { ...c.vision, ...patch } } : c)); setTests((t) => ({ ...t, vision: null })); setMsg(null);
  };
  const body = (): LlmSettings | null => {
    let extra: Record<string, unknown> | null = null;
    if (extraText.trim()) {
      try { extra = JSON.parse(extraText); } catch { setMsg({ ok: false, text: "Ek istek parametreleri geçerli bir JSON olmalı." }); return null; }
    }
    return { ...llm, extra_body: extra };
  };
  const fetchModels = async (t: Target) => {
    const b = body(); if (!b) return;
    setBusy(`models-${t}`);
    try {
      const r = await api.llmModels(t, b);
      setModels((m) => ({ ...m, [t]: r.models }));
      if (!r.ok) setTests((x) => ({ ...x, [t]: { ok: false, error: r.error } }));
    } catch (e) { setTests((x) => ({ ...x, [t]: { ok: false, error: errText(e) } })); }
    setBusy(null);
  };
  const test = async (t: Target) => {
    const b = body(); if (!b) return;
    setBusy(`test-${t}`);
    try { const r = await api.testLlm(t, b); setTests((x) => ({ ...x, [t]: r })); }
    catch (e) { setTests((x) => ({ ...x, [t]: { ok: false, error: errText(e) } })); }
    setBusy(null);
  };
  const save = async () => {
    const b = body(); if (!b) return;
    setBusy("save");
    try {
      const r = await api.saveLlm(b);
      setMsg(r.reachable === false ? { ok: false, text: `Kaydedildi ama sunucuya ulaşılamadı: ${r.error ?? ""}` }
        : { ok: true, text: "LLM bağlantısı kaydedildi ve uygulandı (yeniden başlatma gerekmez)." });
      await load();
      onSaved?.();
    } catch (e) { setMsg({ ok: false, text: errText(e) }); }
    setBusy(null);
  };
  const reset = async () => {
    if (!window.confirm("Arayüzden kaydedilen LLM bağlantısı silinsin ve backend/.env'deki LLM ayarlarına dönülsün mü?")) return;
    setBusy("reset");
    try { await api.resetLlm(); setMsg({ ok: true, text: ".env'deki LLM ayarlarına dönüldü." }); await load(); onSaved?.(); }
    catch (e) { setMsg({ ok: false, text: errText(e) }); }
    setBusy(null);
  };

  const keyField = (id: string, value: string | null | undefined, has: boolean | undefined, onChange: (v: string) => void) => (
    <div className="st-field">
      <label htmlFor={id}>API Anahtarı</label>
      <input id={id} className="st-input" type="password" autoComplete="new-password" value={value ?? ""}
        placeholder={has ? "•••••• (kayıtlı; değiştirmek için yazın)" : "Gerekmiyorsa boş bırakın"} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
  const modelField = (t: Target, id: string, value: string, onChange: (v: string) => void, placeholder: string) => (
    <div className="st-field">
      <label htmlFor={id}>Model</label>
      <div className="st-row">
        <input id={id} className="st-input" list={`${id}-list`} value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} spellCheck={false} />
        <datalist id={`${id}-list`}>{(models[t] ?? []).map((m) => <option key={m} value={m} />)}</datalist>
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => void fetchModels(t)} disabled={!!busy} title="Sunucudaki modelleri listele">
          {busy === `models-${t}` ? <span className="spinner" /> : <Ico d={P.list} />} Modelleri getir
        </button>
      </div>
      {models[t]?.length ? (
        <div className="st-chips">
          {models[t]!.slice(0, 24).map((m) => (
            <button key={m} type="button" className={`st-chip-btn${m === value ? " is-on" : ""}`} onClick={() => onChange(m)}>{m}</button>
          ))}
        </div>
      ) : null}
    </div>
  );
  const testBox = (t: Target) => {
    const r = tests[t];
    return (
      <div className="st-test">
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => void test(t)} disabled={!!busy || (t === "vision" && !llm.vision.model)}>
          {busy === `test-${t}` ? <span className="spinner" /> : <Ico d={P.plug} />} Bağlantıyı test et
        </button>
        {r ? (
          <div className={`st-result ${r.ok ? "is-ok" : "is-bad"}`} role="status">
            {r.ok ? (
              <>
                <b>✓ Yanıt alındı</b> — {r.model} · {((r.elapsed_ms ?? 0) / 1000).toFixed(1)} sn
                {r.reply ? <span className="muted"> · “{r.reply}”</span> : null}
                {r.tools ? (
                  <div>Araç çağırma: {r.tools === "native"
                    ? <b>native destekleniyor</b>
                    : <><b>native desteklenmiyor</b> — "Prompt" ya da "Otomatik" mod kullanın (vLLM'de --enable-auto-tool-choice gerekir).</>}</div>
                ) : null}
              </>
            ) : <><b>✕ Bağlanılamadı</b><div>{r.error}</div></>}
          </div>
        ) : null}
      </div>
    );
  };

  const v = llm.vision;
  return (
    <section className={cls}>
      <SettingsCardHead icon={<Ico d={P.brain} size={18} />} iconClass="is-llm" title="Dil Modeli (LLM)" source={source}
        desc={<>OpenAI uyumlu API (vLLM, Ollama, kurum LLM geçidi …). {source === "ui"
          ? "Ayarlar arayüzden kaydedildi." : <>Şu an <code>backend/.env</code> (LLM_* / VISION_*) ayarları kullanılıyor; kaydedince buradakiler geçerli olur.</>}</>}
        summary={<><b>{llm.model || "model seçilmedi"}</b> @ {llm.base_url.replace(/^https?:\/\//, "") || "—"} · görsel: {v.enabled ? <b>{v.model || "—"}</b> : "kapalı"}</>}
        layout={layout} onChange={(x) => onLayout?.(x)} />
      {layout.collapsed ? null : <>

      <div className="st-llm-grid">
        <div className="st-llm-col">
          <div className="st-sub-head"><Ico d={P.brain} /> Ana Model <span className="muted small">— sohbet, SQL ve dashboard tasarımı</span></div>
          <div className="st-field">
            <label htmlFor="llm-url">API Adresi</label>
            <input id="llm-url" className="st-input" value={llm.base_url} placeholder="https://llm.kurum/v1" onChange={(e) => upd({ base_url: e.target.value })} spellCheck={false} />
          </div>
          {keyField("llm-key", llm.api_key, llm.has_api_key, (x) => upd({ api_key: x }))}
          {modelField("main", "llm-model", llm.model, (x) => upd({ model: x }), "ör. qwen3-32b")}
          <div className="st-field">
            <label>Araç Çağırma</label>
            <div className="st-seg" role="group">
              {TOOL_MODES.map((m) => (
                <button key={m.id} type="button" className={llm.tool_mode === m.id ? "is-on" : undefined} title={m.hint} onClick={() => upd({ tool_mode: m.id })}>{m.label}</button>
              ))}
            </div>
          </div>
          <details className="st-adv">
            <summary>Gelişmiş</summary>
            <div className="st-field">
              <label htmlFor="llm-extra">Ek İstek Parametreleri (JSON)</label>
              <textarea id="llm-extra" className="st-input st-textarea" rows={3} value={extraText} spellCheck={false}
                placeholder='ör. {"chat_template_kwargs": {"enable_thinking": false}}' onChange={(e) => { setExtraText(e.target.value); setMsg(null); }} />
            </div>
          </details>
          {testBox("main")}
        </div>

        <div className="st-llm-col">
          <div className="st-sub-head"><Ico d={P.eye} /> Görsel Model <span className="muted small">— örnek dashboard görselini okur</span></div>
          <label className="st-check st-same">
            <input type="checkbox" checked={v.enabled} onChange={(e) => updV({ enabled: e.target.checked })} /> Görsel analiz kullan
          </label>
          {v.enabled ? (
            <>
              <label className="st-check">
                <input type="checkbox" checked={v.same_as_main} onChange={(e) => updV({ same_as_main: e.target.checked })} />
                Ana modelle aynı adres ve anahtar
              </label>
              {v.same_as_main ? null : (
                <>
                  <div className="st-field">
                    <label htmlFor="vl-url">API Adresi</label>
                    <input id="vl-url" className="st-input" value={v.base_url} placeholder="https://vl.kurum/v1" onChange={(e) => updV({ base_url: e.target.value })} spellCheck={false} />
                  </div>
                  {keyField("vl-key", v.api_key, v.has_api_key, (x) => updV({ api_key: x }))}
                </>
              )}
              {modelField("vision", "vl-model", v.model, (x) => updV({ model: x }), "ör. qwen3-vl-30b")}
              <p className="muted small">Görseli okuyabilen (VL) bir model olmalı. Ana model zaten görsel okuyabiliyorsa aynı modeli seçebilirsiniz.</p>
              {testBox("vision")}
            </>
          ) : <p className="muted small">Kapalıyken örnek görsel yapıştırıldığında yalnızca renk paleti çıkarılır; yerleşim analizi yapılmaz.</p>}
        </div>
      </div>

      <div className="st-llm-foot">
        {msg ? <div className={`st-result ${msg.ok ? "is-ok" : "is-bad"}`} role="status">{msg.text}</div> : <span />}
        <div className="st-row">
          {source === "ui" ? <button type="button" className="btn btn-ghost btn-sm" onClick={() => void reset()} disabled={!!busy}>.env ayarlarına dön</button> : null}
          <button type="button" className="btn btn-primary btn-sm" onClick={() => void save()} disabled={!!busy || !llm.base_url.trim() || !llm.model.trim()}>
            {busy === "save" ? <span className="spinner" /> : null} LLM ayarını kaydet ve uygula
          </button>
        </div>
      </div>
      </>}
    </section>
  );
}
