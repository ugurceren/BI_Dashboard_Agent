// İlişkisel model sekmesi: veri sözlüğündeki tablolar ve ilişkiler (kardinalite, bileşik anahtar, rol).
// Düzen dagre ile soldan sağa: "çok" taraf (fact) solda, "tek" taraf (boyut) sağda — ilişki yönü N → 1.
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background, BaseEdge, Controls, EdgeLabelRenderer, Handle, MiniMap, Position, ReactFlow, ReactFlowProvider,
  getSmoothStepPath, useEdgesState, useNodesInitialized, useNodesState, useReactFlow,
  type Edge, type EdgeProps, type Node, type NodeProps,
} from "@xyflow/react";
import dagre from "@dagrejs/dagre";
import "@xyflow/react/dist/style.css";
import "./model.css";
import type { DataModel, ModelRelationship, ModelTable, SessionState, TableKind } from "../types";
import type { Api } from "../api/client";

const KIND_LABEL: Record<TableKind, string> = { fact: "Fact", dimension: "Boyut", bridge: "Köprü", view: "View" };
const NODE_W = 250;
const HEAD_H = 58;
const ROW_H = 24;
const MORE_H = 24;

type ColRole = "pk" | "fk" | "pkfk" | "";

interface TableNodeData extends Record<string, unknown> {
  table: ModelTable;
  visibleCols: { id: string; name: string; business_name: string; is_pii: boolean; keyRole: ColRole }[];
  hiddenCount: number;
  used: string[] | undefined;
  dimmed: boolean;
  selected: boolean;
}

interface CardEdgeData extends Record<string, unknown> {
  rel: ModelRelationship;
  highlighted: boolean;
  dimmed: boolean;
}

function fmtRows(n: number | null) {
  if (n == null) return "";
  return new Intl.NumberFormat("tr-TR", { notation: n >= 10000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(n);
}

/** outgoing=false: ilişki seçili tablonun "tek" tarafından okunur (N:1 → 1:N) */
function cardText(r: ModelRelationship, outgoing = true) {
  switch (r.cardinality) {
    case "N:1": return outgoing ? "Çoktan bire (N:1)" : "Birden çoğa (1:N)";
    case "1:1": return "Bire bir (1:1)";
    case "N:N": return "Çoktan çoka (N:N)";
    default: return "Kardinalite bilinmiyor";
  }
}

// ---------------------------------------------------------------- düğüm
const TableNode = memo(function TableNode({ data }: NodeProps<Node<TableNodeData>>) {
  const { table, visibleCols, hiddenCount, used, dimmed, selected } = data;
  return (
    <div className={`er-node kind-${table.kind}${dimmed ? " is-dim" : ""}${selected ? " is-sel" : ""}${used ? " is-used" : ""}`}>
      <div className="er-head">
        <div className="er-title-row">
          <span className="er-title" title={table.name}>{table.short_name}</span>
          <span className={`er-kind kind-${table.kind}`} lang="en">{KIND_LABEL[table.kind]}</span>
        </div>
        <div className="er-sub">
          <span className="er-bn" title={table.business_name}>{table.business_name}</span>
          {table.row_count != null ? <span className="er-rows">{fmtRows(table.row_count)} satır</span> : null}
        </div>
        {used ? <span className="er-used" title={`Bu rapordaki dataset'ler: ${used.join(", ")}`}>raporda</span> : null}
      </div>
      <Handle type="target" position={Position.Top} id="top" className="er-handle" isConnectable={false} />
      <Handle type="source" position={Position.Bottom} id="bottom" className="er-handle" isConnectable={false} />
      <div className="er-cols">
        {visibleCols.map((c) => (
          <div className="er-col" key={c.id} title={c.business_name}>
            <Handle type="target" position={Position.Left} id={`t-${c.id}`} className="er-handle" isConnectable={false} />
            <span className={`er-ico ${c.keyRole}`} aria-hidden="true">
              {c.keyRole === "pk" ? "🔑" : c.keyRole === "fk" ? "↗" : c.keyRole === "pkfk" ? "🔑" : ""}
            </span>
            <span className="er-cname">{c.name}</span>
            {c.is_pii ? <span className="er-pii" title="Kişisel veri — agent sorgulayamaz">PII</span> : null}
            <Handle type="source" position={Position.Right} id={`s-${c.id}`} className="er-handle" isConnectable={false} />
          </div>
        ))}
        {hiddenCount > 0 ? <div className="er-more">+{hiddenCount} kolon</div> : null}
      </div>
    </div>
  );
});

// ---------------------------------------------------------------- kenar: iki uçta 1 / * (Power BI tarzı)
function CardEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd }: EdgeProps<Edge<CardEdgeData>>) {
  const rel = data!.rel;
  const [path, lx, ly] = getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, borderRadius: 10, offset: 18 });
  const fromSym = rel.cardinality === "1:1" ? "1" : rel.cardinality ? "*" : "?";
  const toSym = rel.cardinality === "N:N" ? "*" : rel.cardinality ? "1" : "?";
  const off = (p: Position, end: "s" | "t") =>
    p === Position.Right ? [12, -2] : p === Position.Left ? [-12, -2] : p === Position.Bottom ? [10, 16] : [10, end === "t" ? -4 : -4];
  const [sdx, sdy] = off(sourcePosition, "s");
  const [tdx, tdy] = off(targetPosition, "t");
  const cls = `er-edge${data!.highlighted ? " is-hl" : ""}${data!.dimmed ? " is-dim" : ""}${rel.cardinality === "N:N" ? " is-nn" : ""}${!rel.cardinality ? " is-unknown" : ""}`;
  return (
    <>
      <BaseEdge id={id} path={path} className={cls} markerEnd={markerEnd} />
      <EdgeLabelRenderer>
        <div className={`er-card-sym${data!.dimmed ? " is-dim" : ""}`} style={{ transform: `translate(-50%, -100%) translate(${sourceX + sdx}px, ${sourceY + sdy}px)` }}>{fromSym}</div>
        <div className={`er-card-sym${data!.dimmed ? " is-dim" : ""}`} style={{ transform: `translate(-50%, -100%) translate(${targetX + tdx}px, ${targetY + tdy}px)` }}>{toSym}</div>
        {rel.role || rel.pairs.length > 1 ? (
          <div className={`er-edge-label${data!.highlighted ? " is-hl" : ""}${data!.dimmed ? " is-dim" : ""}`} style={{ transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)` }}>
            {rel.role || `${rel.pairs.length} kolonlu anahtar`}
          </div>
        ) : null}
      </EdgeLabelRenderer>
    </>
  );
}

const nodeTypes = { table: TableNode };
const edgeTypes = { card: CardEdge };

// ---------------------------------------------------------------- düzen
function buildGraph(model: DataModel, opts: { tables: ModelTable[]; showAll: boolean; selected: string | null; dir: "LR" | "TB" }) {
  const ids = new Set(opts.tables.map((t) => t.id));
  const rels = model.relationships.filter((r) => ids.has(r.from_table) && ids.has(r.to_table) && r.from_table !== r.to_table);
  const neighbors = new Set<string>();
  if (opts.selected) {
    neighbors.add(opts.selected);
    for (const r of rels) {
      if (r.from_table === opts.selected) neighbors.add(r.to_table);
      if (r.to_table === opts.selected) neighbors.add(r.from_table);
    }
  }
  const role = new Map<string, ColRole>(); // `${table}|${col}`
  const mark = (t: string, c: string, v: "pk" | "fk") => {
    const k = `${t}|${c}`;
    const cur = role.get(k);
    role.set(k, cur && cur !== v ? "pkfk" : v);
  };
  for (const r of model.relationships) {
    for (const [a, b] of r.pairs) {
      mark(r.from_table, a, r.cardinality === "1:1" ? "pk" : "fk");
      mark(r.to_table, b, r.cardinality === "N:N" ? "fk" : "pk");
    }
  }

  const g = new dagre.graphlib.Graph();
  g.setGraph(opts.dir === "LR"
    ? { rankdir: "LR", nodesep: 28, ranksep: 110, marginx: 20, marginy: 20 }
    : { rankdir: "TB", nodesep: 36, ranksep: 90, marginx: 20, marginy: 20 });
  g.setDefaultEdgeLabel(() => ({}));
  const nodes: Node<TableNodeData>[] = [];
  for (const t of opts.tables) {
    const keyCols = t.columns.filter((c) => role.has(`${t.id}|${c.id}`));
    const cols = opts.showAll ? t.columns : keyCols;
    const hidden = t.columns.length - cols.length;
    const h = HEAD_H + cols.length * ROW_H + (hidden > 0 ? MORE_H : 0) + 8;
    g.setNode(t.id, { width: NODE_W, height: h });
    nodes.push({
      id: t.id, type: "table", position: { x: 0, y: 0 }, width: NODE_W, height: h,
      data: {
        table: t, hiddenCount: hidden, used: model.used_tables[t.id],
        visibleCols: cols.map((c) => ({ id: c.id, name: c.name, business_name: c.business_name, is_pii: c.is_pii, keyRole: role.get(`${t.id}|${c.id}`) ?? "" })),
        dimmed: !!opts.selected && !neighbors.has(t.id), selected: opts.selected === t.id,
      },
    });
  }
  const edges: Edge<CardEdgeData>[] = [];
  for (const r of rels) {
    g.setEdge(r.from_table, r.to_table);
    const hl = !!opts.selected && (r.from_table === opts.selected || r.to_table === opts.selected);
    edges.push({
      id: r.id, source: r.from_table, target: r.to_table, type: "card",
      sourceHandle: opts.dir === "LR" ? `s-${r.pairs[0][0]}` : "bottom",
      targetHandle: opts.dir === "LR" ? `t-${r.pairs[0][1]}` : "top",
      data: { rel: r, highlighted: hl, dimmed: !!opts.selected && !hl }, zIndex: hl ? 10 : 0,
    });
  }
  dagre.layout(g);
  for (const n of nodes) {
    const p = g.node(n.id);
    n.position = { x: p.x - p.width / 2, y: p.y - p.height / 2 };
  }
  return { nodes, edges };
}

// ---------------------------------------------------------------- sekme
export function ModelTab({ api, state }: { api: Api; state: SessionState | null }) {
  return (
    <ReactFlowProvider>
      <ModelInner api={api} state={state} />
    </ReactFlowProvider>
  );
}

function ModelInner({ api, state }: { api: Api; state: SessionState | null }) {
  const [model, setModel] = useState<DataModel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [onlyUsed, setOnlyUsed] = useState(false);
  const [areas, setAreas] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [dirPref, setDirPref] = useState<"auto" | "LR" | "TB">("auto");
  const initialized = useNodesInitialized();
  const didInit = useRef(false);
  const flow = useReactFlow();
  const sid = state?.id;
  const datasetsKey = `${state?.spec_version ?? 0}|${state?.datasets?.length ?? 0}`;

  useEffect(() => {
    let alive = true;
    setError(null);
    api.dataModel(sid).then((m) => {
      if (!alive) return;
      setModel(m);
      // İlk açılışta rapor varsa önce raporun tabloları görünsün
      if (!didInit.current) {
        didInit.current = true;
        if (Object.keys(m.used_tables).length) setOnlyUsed(true);
      }
    }).catch((e) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => { alive = false; };
  }, [api, sid, datasetsKey]);

  const usedCount = model ? Object.keys(model.used_tables).length : 0;
  const allAreas = useMemo(() => [...new Set((model?.tables ?? []).map((t) => t.subject_area || "Diğer"))].sort((a, b) => a.localeCompare(b, "tr")), [model]);

  const visibleTables = useMemo(() => {
    if (!model) return [];
    return model.tables.filter((t) =>
      (!onlyUsed || model.used_tables[t.id]) && (areas.size === 0 || areas.has(t.subject_area || "Diğer")));
  }, [model, onlyUsed, areas]);

  // Az tabloda soldan sağa (kolon düzeyinde bağlantı), çok tabloda yukarıdan aşağı (geniş panele sığar)
  const dir: "LR" | "TB" = dirPref === "auto" ? (visibleTables.length > 10 ? "TB" : "LR") : dirPref;
  const graph = useMemo(() => (model ? buildGraph(model, { tables: visibleTables, showAll, selected, dir }) : { nodes: [], edges: [] }),
    [model, visibleTables, showAll, selected, dir]);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<TableNodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge<CardEdgeData>>([]);

  // Seçim değişince konumları koru (kullanıcı sürüklemiş olabilir), sadece görünümü güncelle
  const layoutKey = `${visibleTables.map((t) => t.id).join(",")}|${showAll}|${dir}`;
  const [fitPending, setFitPending] = useState(true);
  useEffect(() => {
    setNodes(graph.nodes);
    setEdges(graph.edges);
    setFitPending(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutKey]);
  // Düğümler ölçüldükten sonra ekrana sığdır (rAF gizli sekmede çalışmayabilir; ölçüm olayına bağlıyız)
  useEffect(() => {
    if (initialized && fitPending && nodes.length) {
      flow.fitView({ padding: 0.1, maxZoom: 1, duration: 200 });
      setFitPending(false);
    }
  }, [initialized, fitPending, nodes.length, flow]);
  useEffect(() => {
    setNodes((cur) => {
      const pos = new Map(cur.map((n) => [n.id, n.position]));
      return graph.nodes.map((n) => ({ ...n, position: pos.get(n.id) ?? n.position }));
    });
    setEdges(graph.edges);
  }, [graph, setNodes, setEdges]);

  const focus = useCallback((id: string) => {
    setSelected(id);
    const n = flow.getNode(id);
    if (n) flow.setCenter(n.position.x + NODE_W / 2, n.position.y + (n.height ?? 200) / 2, { zoom: 1, duration: 350 });
  }, [flow]);

  const matches = useMemo(() => {
    const q = query.trim().toLocaleLowerCase("tr");
    if (!q || !model) return [];
    return model.tables.filter((t) => `${t.name} ${t.business_name}`.toLocaleLowerCase("tr").includes(q)).slice(0, 8);
  }, [query, model]);

  if (error) return <div className="panel-empty"><h3>Model alınamadı</h3><p>{error}</p></div>;
  if (!model) return <div className="panel-empty"><span className="spinner" /><p>Model yükleniyor…</p></div>;

  const sel = selected ? model.tables.find((t) => t.id === selected) ?? null : null;
  const toggleArea = (a: string) => setAreas((s) => { const n = new Set(s); if (n.has(a)) n.delete(a); else n.add(a); return n; });

  return (
    <div className="er-wrap">
      <div className="er-toolbar">
        <div className="er-search">
          <input className="dict-input" placeholder="Tablo ara…" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Tablo ara" />
          {matches.length ? (
            <div className="er-search-hits">
              {matches.map((t) => (
                <button type="button" key={t.id} className="menu-item" onClick={() => { setQuery(""); if (!visibleTables.includes(t)) { setAreas(new Set()); setOnlyUsed(false); } setTimeout(() => focus(t.id), 60); }}>
                  <span>{t.short_name}</span> <span className="muted small">{t.business_name}</span>
                </button>
              ))}
            </div>
          ) : null}
        </div>
        <div className="er-areas" role="group" aria-label="Konu alanı">
          <button type="button" className={`er-chip${areas.size === 0 ? " is-on" : ""}`} onClick={() => setAreas(new Set())}>Tümü</button>
          {allAreas.map((a) => (
            <button type="button" key={a} className={`er-chip${areas.has(a) ? " is-on" : ""}`} onClick={() => toggleArea(a)}>{a}</button>
          ))}
        </div>
        <label className="er-toggle" title={usedCount ? "" : "Bu oturumda henüz dataset yok"}>
          <input type="checkbox" checked={onlyUsed} disabled={!usedCount} onChange={(e) => setOnlyUsed(e.target.checked)} />
          Sadece rapordakiler{usedCount ? ` (${usedCount})` : ""}
        </label>
        <label className="er-toggle">
          <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Tüm kolonlar
        </label>
        <div className="er-seg" role="group" aria-label="Yerleşim">
          <button type="button" className={dir === "LR" ? "is-on" : ""} onClick={() => setDirPref("LR")} title="Soldan sağa: kolonlar arası bağlantı">Yatay</button>
          <button type="button" className={dir === "TB" ? "is-on" : ""} onClick={() => setDirPref("TB")} title="Yukarıdan aşağı: çok tablo için">Dikey</button>
        </div>
        <div className="er-legend" aria-label="Açıklama">
          <span><i className="sw kind-fact" />Fact</span>
          <span><i className="sw kind-dimension" />Boyut</span>
          <span><i className="sw kind-bridge" />Köprü</span>
          <span><i className="sw kind-view" />Onaylı view</span>
          <span className="muted">* çok · 1 tek</span>
        </div>
      </div>
      <div className="er-body">
        <div className="er-canvas">
          {visibleTables.length === 0 ? (
            <div className="panel-empty"><p>Filtreye uyan tablo yok.</p></div>
          ) : (
            <ReactFlow
              nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
              onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
              onNodeClick={(_, n) => setSelected((s) => (s === n.id ? null : n.id))}
              onPaneClick={() => setSelected(null)}
              nodesConnectable={false} edgesFocusable={false} elementsSelectable={false}
              minZoom={0.15} maxZoom={1.75} fitView proOptions={{ hideAttribution: true }} colorMode="system"
            >
              <Background gap={18} size={1} />
              <Controls showInteractive={false} position="bottom-left" />
              <MiniMap pannable zoomable position="bottom-right" nodeColor={(n) => `var(--er-${(n.data as TableNodeData).table.kind})`} maskColor="var(--er-mask)" />
            </ReactFlow>
          )}
        </div>
        {sel ? <Details model={model} table={sel} onClose={() => setSelected(null)} onGo={focus} /> : null}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- ayrıntı paneli
function Details({ model, table, onClose, onGo }: { model: DataModel; table: ModelTable; onClose: () => void; onGo: (id: string) => void }) {
  const rels = model.relationships.filter((r) => r.from_table === table.id || r.to_table === table.id);
  const byId = new Map(model.tables.map((t) => [t.id, t]));
  const used = model.used_tables[table.id];
  return (
    <aside className="er-details" aria-label="Tablo ayrıntıları">
      <div className="er-d-head">
        <div>
          <div className="er-d-title">{table.name}</div>
          <div className="muted">{table.business_name}</div>
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Kapat">✕</button>
      </div>
      <div className="er-d-meta">
        <span className={`er-kind kind-${table.kind}`} lang="en">{KIND_LABEL[table.kind]}</span>
        {table.subject_area ? <span className="pill pill-muted">{table.subject_area}</span> : null}
        {table.row_count != null ? <span className="pill pill-muted">{new Intl.NumberFormat("tr-TR").format(table.row_count)} satır</span> : null}
      </div>
      {table.description ? <p className="er-d-desc">{table.description}</p> : null}
      {table.grain ? <p className="small muted">Granülarite: {table.grain}</p> : null}
      {used ? <p className="small">Bu rapordaki dataset'ler: {used.map((u) => <code key={u}>{u}</code>)}</p> : null}

      <h4>İlişkiler ({rels.length})</h4>
      <ul className="er-d-rels">
        {rels.map((r) => {
          const outgoing = r.from_table === table.id;
          const other = byId.get(outgoing ? r.to_table : r.from_table);
          const here = outgoing ? (r.cardinality === "1:1" ? "1" : "*") : (r.cardinality === "N:N" ? "*" : "1");
          const there = outgoing ? (r.cardinality === "N:N" ? "*" : "1") : (r.cardinality === "1:1" ? "1" : "*");
          return (
            <li key={r.id}>
              <button type="button" className="link-btn" onClick={() => other && onGo(other.id)}>
                {other?.short_name ?? (outgoing ? r.to_table : r.from_table)}
              </button>
              <span className={`er-d-card${r.cardinality === "N:N" ? " is-nn" : ""}`}>{here} → {there}</span>
              <div className="small muted">{cardText(r, outgoing)}{r.role ? ` · rol: ${r.role}` : ""}</div>
              <div className="er-d-join">
                {r.pairs_display.map(([a, b], i) => (
                  <code key={i}>{outgoing ? `${a} = ${other?.short_name}.${b}` : `${b} = ${other?.short_name}.${a}`}</code>
                ))}
              </div>
            </li>
          );
        })}
        {rels.length === 0 ? <li className="muted small">Sözlükte ilişki tanımlı değil.</li> : null}
      </ul>

      <h4>Kolonlar ({table.columns.length})</h4>
      <table className="er-d-cols">
        <tbody>
          {table.columns.map((c) => (
            <tr key={c.id}>
              <td>{c.is_key ? "🔑 " : ""}<code>{c.name}</code>{c.is_pii ? <span className="er-pii">PII</span> : null}</td>
              <td className="muted">{c.business_name}</td>
              <td><span className="pill pill-muted">{c.role}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </aside>
  );
}
