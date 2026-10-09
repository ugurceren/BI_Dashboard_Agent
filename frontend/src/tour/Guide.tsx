// Kullanım kılavuzu: rol filtresi, arama, bölüm menüsü; içerik content.ts'ten. Açık / koyu tema, yazdırılabilir.
import { useEffect, useMemo, useState } from "react";
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

export function Guide({ me, section, onSection, onExit, onPresent }: {
  me: Me | null;
  section: string | null;
  onSection: (id: string) => void;
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
  const current = GUIDE.find((s) => s.id === section) ?? visible[0] ?? GUIDE[0];

  useEffect(() => { document.querySelector(".tr-guide-main")?.scrollTo({ top: 0 }); }, [current.id]);

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
              aria-current={s.id === current.id ? "page" : undefined} onClick={() => onSection(s.id)}>
              <span>{s.title}</span>
              <small>{s.roles.map((r) => ROLE_LABEL[r]).join(" · ")}</small>
            </button>
          ))}
          {!visible.length ? <p className="tr-muted">Aramaya uyan bölüm yok.</p> : null}
        </nav>
      </aside>
      <main className="tr-guide-main">
        <Section s={current} />
        <Pager current={current} list={visible.length ? visible : GUIDE} onSection={onSection} />
      </main>
    </div>
  );
}

function Section({ s }: { s: GuideSection }) {
  return (
    <article className="tr-article" aria-labelledby={`g-${s.id}`}>
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

function Pager({ current, list, onSection }: { current: GuideSection; list: GuideSection[]; onSection: (id: string) => void }) {
  const k = list.findIndex((s) => s.id === current.id);
  const prev = k > 0 ? list[k - 1] : null;
  const next = k >= 0 && k < list.length - 1 ? list[k + 1] : null;
  return (
    <div className="tr-pager">
      {prev ? <button type="button" className="tr-page-btn" onClick={() => onSection(prev.id)}>← {prev.title}</button> : <span />}
      {next ? <button type="button" className="tr-page-btn is-next" onClick={() => onSection(next.id)}>{next.title} →</button> : null}
    </div>
  );
}
