// Sol panel: faz adımları, konuşma dökümü, durum satırı, mesaj yazma alanı.
import { useEffect, useLayoutEffect, useRef, useState, type ClipboardEvent, type DragEvent, type KeyboardEvent, type ReactNode, type RefObject } from "react";
import type { Phase, ReportSuggestion, TranscriptItem } from "../types";
import type { Api } from "../api/client";
import { Markdown } from "./Markdown";
import { downscaleImage } from "../lib/images";

export const PHASES: { id: Phase; label: string; hint: string }[] = [
  { id: "requirements", label: "İhtiyaç", hint: "Rapor ihtiyacı ve netleştirme soruları" },
  { id: "data", label: "Veri", hint: "Veri sözlüğü, SQL ve veri kümeleri" },
  { id: "design", label: "Tasarım", hint: "Dashboard tasarımı ve iterasyon" },
];

// ---------- faz adımları ----------

/** Geri: her zaman; ileri: o fazın ön koşulu hazırsa (veri → gereksinimler kayıtlı, tasarım → dataset'ler kayıtlı). */
export function PhaseStepper({ phase, disabled, onBack, reachable = {} }: {
  phase: Phase; disabled?: boolean; onBack: (p: Phase) => void; reachable?: Partial<Record<Phase, boolean>>;
}) {
  const cur = PHASES.findIndex((p) => p.id === phase);
  return (
    <ol className="stepper" aria-label="Fazlar">
      {PHASES.map((p, i) => {
        const st = i < cur ? "done" : i === cur ? "current" : "todo";
        const forward = i > cur && !!reachable[p.id];
        const clickable = (i < cur || forward) && !disabled;
        return (
          <li key={p.id} className={`step is-${st}${forward ? " is-reachable" : ""}`}>
            <button
              type="button"
              disabled={!clickable}
              onClick={() => clickable && onBack(p.id)}
              title={clickable ? (forward ? `${p.label} fazına geç (kayıtlı içerikle)` : `${p.label} fazına geri dön`) : p.hint}
              aria-current={st === "current" ? "step" : undefined}
            >
              <span className="step-dot">
                {st === "done" ? (
                  <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true"><path d="M2.5 6.2 5 8.5l4.5-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
                ) : (
                  i + 1
                )}
              </span>
              <span className="step-label">{p.label}</span>
            </button>
            {i < PHASES.length - 1 ? <span className="step-line" aria-hidden="true" /> : null}
          </li>
        );
      })}
    </ol>
  );
}

// ---------- döküm ----------

const TOOL_ICONS: Record<string, string> = {
  search: "M7 12.5a5.5 5.5 0 1 1 0-11 5.5 5.5 0 0 1 0 11Zm4-1.5 3.5 3.5",
  sql: "M2 4c0-1.1 2.7-2 6-2s6 .9 6 2-2.7 2-6 2-6-.9-6-2Zm0 0v8c0 1.1 2.7 2 6 2s6-.9 6-2V4M2 8c0 1.1 2.7 2 6 2s6-.9 6-2",
  save: "M3 2h8l3 3v9H2V2h1Zm2 0v4h6V2M4.5 14V9.5h7V14",
  spec: "M2.5 3.5h11M2.5 8h11M2.5 12.5h6",
  image: "M2 3h12v10H2zM2 11l3.5-3.5L9 11l2-2 3 3M10.5 6.5h.01",
  default: "M9.5 2.5 6 9h4l-3.5 6.5M3 8h.01M13 8h.01",
};
function toolIcon(name: string): string {
  const n = name.toLowerCase();
  if (n.includes("search") || n.includes("dictionary")) return TOOL_ICONS.search;
  if (n.includes("sql") || n.includes("query") || n.includes("validate")) return TOOL_ICONS.sql;
  if (n.includes("save") || n.includes("dataset")) return TOOL_ICONS.save;
  if (n.includes("spec") || n.includes("design") || n.includes("visual")) return TOOL_ICONS.spec;
  if (n.includes("image") || n.includes("vision")) return TOOL_ICONS.image;
  return TOOL_ICONS.default;
}

function formatDuration(ms: number | undefined): string {
  if (ms === undefined || ms === null || !Number.isFinite(ms)) return "";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toLocaleString("tr-TR", { maximumFractionDigits: 1 })} sn`;
}

function ToolRow({ item }: { item: TranscriptItem }) {
  const [open, setOpen] = useState(false);
  const t = item.tool;
  const ok = t ? t.ok : true;
  const skipped = !!t?.skipped;   // faza kapalı araç: sistem çalıştırmadı, model yönlendirildi — hata gibi gösterme
  const args = t?.arguments;
  const argsText = args === undefined ? "" : typeof args === "string" ? args : JSON.stringify(args, null, 2);
  return (
    <div className={`tool-row${ok ? "" : skipped ? " is-skipped" : " is-fail"}${open ? " is-open" : ""}`} data-tool={t?.name} data-ok={ok ? "1" : "0"}
      data-skipped={skipped ? "1" : undefined}>
      <button type="button" className="tool-head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <svg className="tool-icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
          <path d={toolIcon(t?.name ?? "")} fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span className="tool-name">{t?.name ?? "araç"}</span>
        <span className="tool-summary">{t?.summary || item.content}</span>
        <span className="tool-dur">{formatDuration(t?.durationMs)}</span>
        <span className={`tool-status ${ok ? "ok" : skipped ? "skip" : "fail"}`} title={ok ? "Başarılı" : skipped ? "Bu fazda kullanılmaz — atlandı" : "Başarısız"}>
          {skipped ? (
            <svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true"><path d="M2.5 6h7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
          ) : ok ? (
            <svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true"><path d="M2.5 6.2 5 8.5l4.5-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
          ) : (
            <svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true"><path d="M3 3l6 6M9 3 3 9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
          )}
        </span>
        <svg className="tool-caret" viewBox="0 0 12 12" width="10" height="10" aria-hidden="true"><path d="M4.5 3 7.5 6l-3 3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </button>
      {open ? (
        <div className="tool-body">
          {t?.summary && t.summary !== item.content && item.content ? <div className="tool-content">{item.content}</div> : null}
          {argsText ? <pre className="code-block">{argsText}</pre> : <div className="muted small">Argüman yok</div>}
        </div>
      ) : null}
    </div>
  );
}

function Bubble({ item, onImage }: { item: TranscriptItem; onImage: (src: string) => void }) {
  if (item.role === "tool") return <ToolRow item={item} />;
  if (item.role === "system") {
    const isErr = item.id.startsWith("local_err");
    return <div className={`sys-row${isErr ? " is-error" : ""}`}>{item.content}</div>;
  }
  const isUser = item.role === "user";
  return (
    <div className={`msg ${isUser ? "msg-user" : "msg-assistant"}${item.id.startsWith("pending_") ? " is-pending" : ""}`} data-role={item.role}>
      {!isUser ? (
        <div className="msg-avatar" aria-hidden="true">
          <svg viewBox="0 0 16 16" width="12" height="12"><path d="M3 12V8M6.5 12V4.5M10 12V7M13.5 12V3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
        </div>
      ) : null}
      <div className="msg-bubble">
        {item.images?.length ? (
          <div className="msg-images">
            {item.images.map((src, i) => (
              <button type="button" key={i} className="msg-thumb" onClick={() => onImage(src)} title="Büyüt">
                <img src={src} alt={`Ek ${i + 1}`} />
              </button>
            ))}
          </div>
        ) : null}
        {item.content ? isUser ? <div className="msg-text">{item.content}</div> : <Markdown text={item.content} /> : null}
      </div>
    </div>
  );
}

export function Transcript({ items, status, busy, empty }: {
  items: TranscriptItem[]; status: string | null; busy: boolean; empty: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [lightbox, setLightbox] = useState<string | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [items, status, busy]);
  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };
  return (
    <div className="transcript" ref={ref} onScroll={onScroll}>
      {items.length === 0 && !busy ? empty : null}
      {items.map((it) => (
        <Bubble key={it.id} item={it} onImage={setLightbox} />
      ))}
      {busy ? (
        <div className="status-line" role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          <span>{status || "Agent çalışıyor…"}</span>
        </div>
      ) : null}
      {lightbox ? (
        <div className="lightbox" onClick={() => setLightbox(null)} role="dialog" aria-label="Görsel önizleme">
          <img src={lightbox} alt="Önizleme" />
        </div>
      ) : null}
    </div>
  );
}

// ---------- boş durum ----------

// veri önerileri alınamazsa (backend / sözlük yok) gösterilen genel örnekler
export const SUGGESTIONS = [
  "Satış performansı için aylık trend ve bölge kırılımında yönetim dashboard'u istiyorum",
  "En çok kullanılan ürün / hizmetleri dönemsel olarak karşılaştırmak istiyorum",
  "Ana göstergeleri önceki yılla karşılaştıran bir özet panosu istiyorum",
];

/** Kullanıcının yetkili olduğu veriden öneriler: önce kural metni (anında), sonra LLM ile düzenlenmiş hali. */
function useSuggestions(api: Api | undefined, offset: number) {
  const [items, setItems] = useState<ReportSuggestion[] | null>(null);
  const [polishing, setPolishing] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!api) return;
    let live = true;
    setFailed(false);
    (async () => {
      try {
        const r = await api.suggestions(offset);
        if (!live) return;
        setItems(r.items);
        if (!r.polished && r.items.length) {
          setPolishing(true);
          try {
            const p = await api.polishSuggestions(offset);
            if (live && p.polished) setItems(p.items);
          } catch { /* LLM yoksa kural önerileri kalır */ }
          if (live) setPolishing(false);
        }
      } catch {
        if (live) { setItems([]); setFailed(true); }
      }
    })();
    return () => { live = false; };
  }, [api, offset]);
  return { items, polishing, failed };
}

export function EmptyChat({ api, onPick, onDemo, onOpenReport, disabled }: {
  api?: Api; onPick: (s: string) => void; onDemo: () => void; onOpenReport?: (id: string) => void; disabled?: boolean;
}) {
  const [offset, setOffset] = useState(0);
  const { items, polishing, failed } = useSuggestions(api, offset);
  const fromData = !!items && items.length > 0;
  return (
    <div className="empty-chat">
      <div className="empty-mark" aria-hidden="true">
        <svg viewBox="0 0 32 32" width="30" height="30"><rect width="32" height="32" rx="9" fill="currentColor" opacity=".12" /><path d="M9 22v-5M14 22V11M19 22v-8M24 22V8" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" /></svg>
      </div>
      <h2>Nasıl bir rapor istiyorsunuz?</h2>
      <p>İhtiyacınızı anlatın; agent sorular sorup veriyi bulacak, SQL yazacak ve dashboard'u tasarlayacak.</p>
      <div className="sugg-head">
        <span>{fromData ? "Erişebildiğiniz veriye göre öneriler" : items === null ? "Öneriler hazırlanıyor…" : "Örnek istekler"}</span>
        {polishing ? <span className="sugg-polish" title="Öneriler dil modeliyle düzenleniyor">düzenleniyor…</span> : null}
        {fromData ? (
          <button type="button" className="sugg-refresh" onClick={() => setOffset((o) => o + 1)} disabled={disabled || polishing} title="Başka öneriler göster">
            <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 2.5v3h-3" /></svg>
            Yenile
          </button>
        ) : null}
      </div>
      <div className="chips">
        {items === null ? [0, 1, 2].map((i) => <div key={i} className="chip chip-skeleton" aria-hidden="true" />)
          : fromData ? items.map((s) => (
            <div key={s.id} className="sugg">
              <button type="button" className={`chip${polishing ? " is-polishing" : ""}`} onClick={() => onPick(s.text)} disabled={disabled}
                title={[`Veri: ${s.table}`, s.measures.length ? `Ölçüler: ${s.measures.join(", ")}` : "", s.dims.length ? `Kırılımlar: ${s.dims.join(", ")}` : "",
                  s.time ? "Zaman trendi var" : ""].filter(Boolean).join("\n")}>
                <span className="sugg-domain">{s.domain}</span>
                {s.text}
              </button>
              {s.similar_report ? (
                <button type="button" className="sugg-similar" onClick={() => onOpenReport?.(s.similar_report!.id)} disabled={!onOpenReport}
                  title="Bu veriyi kullanan bir rapor envanterde zaten var">
                  Benzer rapor var: <b>{s.similar_report.title}</b> →
                </button>
              ) : null}
            </div>
          ))
          : SUGGESTIONS.map((s) => (
            <button key={s} type="button" className="chip" onClick={() => onPick(s)} disabled={disabled}>{s}</button>
          ))}
      </div>
      {failed ? <p className="sugg-note">Veriye göre öneri alınamadı; genel örnekler gösteriliyor.</p> : null}
      <div className="empty-or"><span>veya</span></div>
      <button type="button" className="btn btn-secondary" onClick={onDemo} disabled={disabled}>
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M2 2.5h5v5H2zM9 2.5h5v3H9zM9 7.5h5v6H9zM2 9.5h5v4H2z" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /></svg>
        Demo dashboard yükle
      </button>
    </div>
  );
}

// ---------- yazma alanı ----------

export function Composer({ disabled, busy, onSend, visionReady, dropTarget }: {
  disabled: boolean;
  busy: boolean;
  onSend: (text: string, images: string[]) => void;
  visionReady: boolean | null;
  dropTarget: RefObject<HTMLElement | null>;
}) {
  const [text, setText] = useState("");
  const [images, setImages] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const [processing, setProcessing] = useState(0);
  const ta = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useLayoutEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(200, el.scrollHeight)}px`;
    el.style.overflowY = el.scrollHeight > 200 ? "auto" : "hidden";
  }, [text, busy]);

  const addFiles = async (files: File[]) => {
    const imgs = files.filter((f) => f.type.startsWith("image/"));
    if (!imgs.length) {
      if (files.length) setErr("Yalnızca görsel dosyaları eklenebilir.");
      return;
    }
    setErr(null);
    setProcessing((n) => n + imgs.length);
    for (const f of imgs.slice(0, 6)) {
      try {
        const url = await downscaleImage(f);
        setImages((cur) => (cur.length >= 6 ? cur : [...cur, url]));
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
      } finally {
        setProcessing((n) => n - 1);
      }
    }
  };

  // sürükle-bırak: tüm sohbet paneli hedef
  useEffect(() => {
    const el = dropTarget.current;
    if (!el) return;
    let depth = 0;
    const has = (e: globalThis.DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");
    const enter = (e: globalThis.DragEvent) => { if (!has(e)) return; e.preventDefault(); depth++; setDrag(true); };
    const over = (e: globalThis.DragEvent) => { if (has(e)) e.preventDefault(); };
    const leave = () => { depth = Math.max(0, depth - 1); if (!depth) setDrag(false); };
    const drop = (e: globalThis.DragEvent) => {
      if (!has(e)) return;
      e.preventDefault();
      depth = 0;
      setDrag(false);
      if (!disabled) void addFiles(Array.from(e.dataTransfer?.files ?? []));
    };
    el.addEventListener("dragenter", enter);
    el.addEventListener("dragover", over);
    el.addEventListener("dragleave", leave);
    el.addEventListener("drop", drop);
    return () => {
      el.removeEventListener("dragenter", enter);
      el.removeEventListener("dragover", over);
      el.removeEventListener("dragleave", leave);
      el.removeEventListener("drop", drop);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dropTarget, disabled]);

  const send = () => {
    const t = text.trim();
    if (disabled || processing > 0 || (!t && !images.length)) return;
    onSend(t, images);
    setText("");
    setImages([]);
    setErr(null);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };
  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.clipboardData?.files ?? []).filter((f) => f.type.startsWith("image/"));
    if (files.length) {
      e.preventDefault();
      void addFiles(files);
    }
  };
  const canSend = !disabled && processing === 0 && (text.trim().length > 0 || images.length > 0);

  return (
    <div className={`composer${disabled ? " is-disabled" : ""}`} onDragOver={(e: DragEvent) => e.preventDefault()}>
      {drag ? <div className="drop-overlay">Görseli bırakın — örnek dashboard olarak eklenecek</div> : null}
      {images.length || processing ? (
        <div className="thumbs">
          {images.map((src, i) => (
            <div className="thumb" key={i}>
              <img src={src} alt={`Ek ${i + 1}`} />
              <button type="button" className="thumb-x" onClick={() => setImages((cur) => cur.filter((_, j) => j !== i))} aria-label="Görseli kaldır">
                <svg viewBox="0 0 12 12" width="9" height="9"><path d="M3 3l6 6M9 3 3 9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
              </button>
            </div>
          ))}
          {processing > 0 ? <div className="thumb thumb-loading"><span className="spinner" /></div> : null}
        </div>
      ) : null}
      {images.length && visionReady === false ? (
        <div className="composer-warn">Görsel modeli yapılandırılmamış; görsel yorumlanamayabilir.</div>
      ) : null}
      {err ? <div className="composer-warn is-error">{err}</div> : null}
      <div className="composer-box">
        <button type="button" className="icon-btn" onClick={() => fileRef.current?.click()} disabled={disabled} title="Görsel ekle (yapıştır veya sürükle-bırak da olur)" aria-label="Görsel ekle">
          <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><path d="M14.5 9.5 9.3 14.7a3.5 3.5 0 0 1-5-5l6-6a2.3 2.3 0 0 1 3.3 3.3l-6 6a1.1 1.1 0 0 1-1.6-1.6L11.5 5.9" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          multiple
          hidden
          onChange={(e) => {
            void addFiles(Array.from(e.target.files ?? []));
            e.target.value = "";
          }}
        />
        <textarea
          ref={ta}
          rows={1}
          value={text}
          placeholder={busy ? "Agent yanıtlıyor…" : "Mesaj yazın veya görsel yapıştırın…"}
          title="Enter gönderir · Shift+Enter yeni satır"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          onPaste={onPaste}
          disabled={disabled}
        />
        <button type="button" className="send-btn" onClick={send} disabled={!canSend} aria-label="Gönder" title="Gönder">
          <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true"><path d="M10 16V4M5 9l5-5 5 5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>
      </div>
      <div className="composer-hint">
        <span>Enter gönder · Shift+Enter yeni satır</span>
        <span>Görsel: yapıştır / sürükle</span>
      </div>
    </div>
  );
}
