// Kullanım kılavuzu: rol filtresi, arama, bölüm menüsü; içerik content.ts'ten. Bölümler alt alta tek sayfada akar,
// menü kaydırmayla güncellenir. Açık / koyu tema, yazdırılabilir.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Me } from "../types";
import { GUIDE, ROLE_LABEL, type Block, type GuideSection, type Role } from "./content";
import { Rich, Shot } from "./ui";
import "./tour.css";

type Filter = Role | "tumu";

const defaultFilter = (me: Me | null): Filter => {
  const r = me?.platform_mode === "server" ? me?.platform_role : null;
  return r === "viewer" ? "herkes" : r === "builder" ? "tasarimci" : "tumu";
};

const blockText = (b: Block): string =>
  "text" in b ? b.text
    : b.kind === "shot" ? b.caption
    : b.kind === "table" ? [...b.head, ...b.rows.flat()].join(" ")
    : b.kind === "examples" ? [b.title ?? "", ...b.items].join(" ")
    : b.items.join(" ");

export function Guide({ me, section, onExit, onPresent }: {
  me: Me | null;
  section: string | null;
  onExit: () => void;
  onPresent: () => void;
}) {
  const [filter, setFilter] = useState<Filter>(() => defaultFilter(me));
  const [q, setQ] = useState("");
  useEffect(() => { setFilter(defaultFilter(me)); }, [me]);

  const visible = useMemo(() => {
    const ql = q.trim().toLocaleLowerCase("tr");
    return GUIDE.filter((s) => (filter === "tumu" || s.roles.includes(filter) || s.roles.includes("herkes"))
      && (!ql || `${s.title} ${s.summary} ${s.blocks.map(blockText).join(" ")}`.toLocaleLowerCase("tr").includes(ql)));
  }, [filter, q]);
  const list = visible.length ? visible : GUIDE;
  const mainRef = useRef<HTMLElement>(null);
  const [active, setActive] = useState<string | null>(section);
  const [showTop, setShowTop] = useState(false);

  // bölüm adresi: kaydırırken URL güncellenir (geçmişe kayıt eklemeden, hashchange tetiklemeden)
  const mark = useCallback((id: string) => {
    setActive(id);
    const h = `#/kilavuz/${id}`;
    if (window.location.hash !== h) window.history.replaceState(null, "", h);
  }, []);

  const goTo = useCallback((id: string, smooth = true) => {
    const main = mainRef.current;
    const el = main?.querySelector<HTMLElement>(`#g-sec-${id}`);
    if (!main || !el) return;
    const top = el.offsetTop - main.offsetTop - 16;
    main.scrollTo({ top, behavior: smooth ? "smooth" : "auto" });
    setShowTop(top > 400);
    mark(id);
  }, [mark]);

  // dışarıdan gelen bölüm adresi (bağlantı, yeniden yükleme) o bölüme götürür; kaydırırken URL replaceState ile
  // değiştiği için uygulamanın rotası eskide kalabilir, bu yüzden hashchange ayrıca dinlenir
  useEffect(() => {
    if (section && list.some((s) => s.id === section)) goTo(section, false);
    else mainRef.current?.scrollTo({ top: 0 });
    const onHash = () => {
      const id = /^#\/kilavuz\/([a-z0-9-]+)/.exec(window.location.hash)?.[1];
      if (id) goTo(id, false);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [section]); // eslint-disable-line react-hooks/exhaustive-deps

  // arama / rol filtresi değişince (oturum bilgisi gelince varsayılan rol filtresi de değişir): adresteki bölüm
  // listede kaldıysa ona, aramada ya da bölüm elendiyse başa
  useEffect(() => {
    const id = /^#\/kilavuz\/([a-z0-9-]+)/.exec(window.location.hash)?.[1];
    if (!q.trim() && id && list.some((s) => s.id === id)) goTo(id, false);
    else mainRef.current?.scrollTo({ top: 0 });
  }, [filter, q]); // eslint-disable-line react-hooks/exhaustive-deps

  const onScroll = () => {
    const main = mainRef.current;
    if (!main) return;
    setShowTop(main.scrollTop > 400);
    const atEnd = main.scrollTop + main.clientHeight >= main.scrollHeight - 4;
    let id = list[0]?.id;
    for (const s of list) {
      const el = main.querySelector<HTMLElement>(`#g-sec-${s.id}`);
      if (el && el.offsetTop - main.offsetTop - main.scrollTop <= 120) id = s.id;
    }
    if (atEnd) id = list[list.length - 1]?.id;
    if (id && id !== active) mark(id);
  };
  const current = list.find((s) => s.id === active) ?? list[0];

  return (
    <div className="tr-guide" data-testid="guide">
      <aside className="tr-guide-nav" aria-label="Kılavuz bölümleri">
        <div className="tr-guide-head">
          <button type="button" className="tr-link" onClick={onExit}>← Geri</button>
          <h1>Kullanım kılavuzu</h1>
          <button type="button" className="tr-link tr-present-link" onClick={onPresent}>▶ Tanıtımı izle</button>
        </div>
        <input className="tr-search" type="search" placeholder="Kılavuzda ara…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Kılavuzda ara" />
        <div className="tr-roles-filter" role="group" aria-label="Role göre">
          {(["tumu", "herkes", "tasarimci", "yonetici"] as Filter[]).map((f) => (
            <button key={f} type="button" className={`tr-chip${filter === f ? " is-on" : ""}`} aria-pressed={filter === f} onClick={() => setFilter(f)}>
              {f === "tumu" ? "Tümü" : ROLE_LABEL[f]}
            </button>
          ))}
        </div>
        <nav>
          {visible.map((s) => (
            <button key={s.id} type="button" className={`tr-nav-item${s.id === current.id ? " is-on" : ""}`}
              aria-current={s.id === current.id ? "page" : undefined} onClick={() => goTo(s.id)}>
              <span>{s.title}</span>
              <small>{s.roles.map((r) => ROLE_LABEL[r]).join(" · ")}</small>
            </button>
          ))}
          {!visible.length ? <p className="tr-muted">Aramaya uyan bölüm yok.</p> : null}
        </nav>
      </aside>
      <main className="tr-guide-main" ref={mainRef} onScroll={onScroll}>
        {list.map((s) => <Section key={s.id} s={s} />)}
        <button type="button" className={`tr-to-top${showTop ? " is-on" : ""}`} aria-label="Başa dön" title="Başa dön"
          tabIndex={showTop ? 0 : -1} onClick={() => mainRef.current?.scrollTo({ top: 0, behavior: "smooth" })}>↑ Başa dön</button>
      </main>
    </div>
  );
}

function Section({ s }: { s: GuideSection }) {
  return (
    <article className="tr-article" id={`g-sec-${s.id}`} aria-labelledby={`g-${s.id}`}>
      <div className="tr-badges">{s.roles.map((r) => <span key={r} className={`tr-badge r-${r}`}>{ROLE_LABEL[r]}</span>)}</div>
      <h2 id={`g-${s.id}`}>{s.title}</h2>
      <p className="tr-summary">{s.summary}</p>
      {s.blocks.map((b, i) => <BlockView key={i} b={b} />)}
    </article>
  );
}

function BlockView({ b }: { b: Block }) {
  switch (b.kind) {
    case "p": return <p><Rich text={b.text} /></p>;
    case "steps": return <ol className="tr-steps">{b.items.map((t) => <li key={t}><Rich text={t} /></li>)}</ol>;
    case "list": return <ul className="tr-list">{b.items.map((t) => <li key={t}><Rich text={t} /></li>)}</ul>;
    case "examples": return (
      <div className="tr-examples">
        {b.title ? <h3>{b.title}</h3> : null}
        <ul>{b.items.map((t) => <li key={t}>“{t}”</li>)}</ul>
      </div>
    );
    case "shot": return <Shot src={b.src} caption={b.caption} />;
    case "tip": return <div className="tr-callout is-tip" role="note"><b>İpucu</b><span><Rich text={b.text} /></span></div>;
    case "warn": return <div className="tr-callout is-warn" role="note"><b>Dikkat</b><span><Rich text={b.text} /></span></div>;
    case "table": return (
      <div className="tr-table-wrap">
        <table className="tr-table">
          <thead><tr>{b.head.map((h) => <th key={h}>{h}</th>)}</tr></thead>
          <tbody>{b.rows.map((r) => <tr key={r[0]}>{r.map((c, k) => <td key={k}><Rich text={c} /></td>)}</tr>)}</tbody>
        </table>
      </div>
    );
  }
}
