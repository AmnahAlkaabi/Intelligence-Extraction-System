import { useEffect, useMemo, useRef, useState } from "react";
import type { KnowledgeGraphExport, Relation } from "../api/types";

interface Node {
  id: string;
  type: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  degree: number;
  fixed?: boolean;
}
interface Edge {
  source: string;
  target: string;
  type: string;
}

const TYPE_COLORS: Record<string, string> = {
  PERSON: "#4a9eff", ORG: "#3ecf8e", LOCATION: "#f5c842", DATE: "#c084fc",
  MONEY: "#ff9c2e", ID_NUMBER: "#ff6ba8", PRODUCT: "#38bdf8", EVENT: "#ff4f4f",
  OTHER: "#7ec8a0",
};
const colorFor = (t: string) => TYPE_COLORS[t] ?? "#8fa4bd";

const MIN_R = 5;
const MAX_R = 20;

function distToSegment(px: number, py: number, x1: number, y1: number, x2: number, y2: number): number {
  const dx = x2 - x1, dy = y2 - y1;
  const lenSq = dx * dx + dy * dy;
  let t = lenSq === 0 ? 0 : ((px - x1) * dx + (py - y1) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

export function KnowledgeGraphView({ graph, active = true }: { graph: KnowledgeGraphExport; active?: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState<{ width: number; height: number } | null>(null);
  const nodesRef = useRef<Node[]>([]);
  const namesSigRef = useRef<string>("");
  const dragRef = useRef<Node | null>(null);
  const hoveredNodeRef = useRef<Node | null>(null);
  const hoveredEdgeRef = useRef<Edge | null>(null);
  const alphaRef = useRef(1);
  const runningRef = useRef(false);

  // Search state. `manualSelectedId` lets clicking a result chip or a
  // connected-node link in the description panel override which match is
  // focused; it's reset to null on every keystroke so a fresh query goes
  // back to auto-focusing its top-ranked match.
  const [query, setQuery] = useState("");
  const [manualSelectedId, setManualSelectedId] = useState<string | null>(null);
  const trimmedQuery = query.trim().toLowerCase();

  // Read inside the imperative canvas loop below (same reason hoveredNodeRef
  // / hoveredEdgeRef are refs, not state: state read inside a long-lived
  // requestAnimationFrame closure can go stale mid-animation).
  const activeSelectionRef = useRef<string | null>(null);
  const neighborIdsRef = useRef<Set<string>>(new Set());
  const matchIdsRef = useRef<Set<string>>(new Set());

  // Only the graph tab needs live dims/animation -- while another tab is
  // showing, this component may still be mounted (Dashboard keeps every
  // panel alive to preserve chat state) but sits at display:none, where
  // clientWidth reads 0. Re-measuring only when `active` flips true avoids
  // ever seeding node positions from a stale/zero width, which is what
  // caused the whole layout to visibly jump the instant the tab opened.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || !active) return;
    const measure = () => {
      const w = el.clientWidth;
      if (w > 0) setDims((prev) => (prev && prev.width === w ? prev : { width: w, height: 520 }));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [active]);

  const edges: Edge[] = useMemo(
    () => graph.relations.map((r) => ({ source: r.source_entity, target: r.target_entity, type: r.relation_type })),
    [graph.relations],
  );

  const names = useMemo(() => Array.from(new Set(graph.entities.map((e) => e.name))), [graph.entities]);

  // Contextual search: matches on the entity's own name/type first, but
  // also on its known aliases (mentions) and on the evidence text backing
  // any relationship it's part of -- so searching a phrase that only
  // appears in *why* two entities are linked still surfaces both of them,
  // not just entities whose literal name contains the query.
  const matches = useMemo(() => {
    const q = trimmedQuery;
    if (!q) return [];
    const scored = new Map<string, { score: number; reason: string }>();
    const consider = (id: string, score: number, reason: string) => {
      const existing = scored.get(id);
      if (!existing || score > existing.score) scored.set(id, { score, reason });
    };
    for (const e of graph.entities) {
      const nameLower = e.name.toLowerCase();
      if (nameLower === q) consider(e.name, 4, "exact name match");
      else if (nameLower.includes(q)) consider(e.name, 3, "name match");
      else if (e.type.toLowerCase().includes(q)) consider(e.name, 2, `type: ${e.type}`);
      else {
        const hitMention = e.mentions.find((m) => m.toLowerCase().includes(q));
        if (hitMention) consider(e.name, 2, `also referred to as "${hitMention}"`);
      }
    }
    for (const r of graph.relations) {
      if (r.evidence && r.evidence.toLowerCase().includes(q)) {
        consider(r.source_entity, 1, `context: "${r.evidence}"`);
        consider(r.target_entity, 1, `context: "${r.evidence}"`);
      }
    }
    return Array.from(scored.entries())
      .map(([id, v]) => ({ id, ...v }))
      .sort((a, b) => b.score - a.score);
  }, [trimmedQuery, graph.entities, graph.relations]);

  const activeSelection = trimmedQuery ? (manualSelectedId ?? matches[0]?.id ?? null) : null;
  const selectedEntity = useMemo(
    () => (activeSelection ? graph.entities.find((e) => e.name === activeSelection) ?? null : null),
    [activeSelection, graph.entities],
  );
  const connectedRelations: Relation[] = useMemo(
    () =>
      activeSelection
        ? graph.relations.filter((r) => r.source_entity === activeSelection || r.target_entity === activeSelection)
        : [],
    [activeSelection, graph.relations],
  );
  const neighborIds = useMemo(() => {
    const s = new Set<string>();
    if (!activeSelection) return s;
    for (const r of connectedRelations) {
      s.add(r.source_entity === activeSelection ? r.target_entity : r.source_entity);
    }
    return s;
  }, [activeSelection, connectedRelations]);

  useEffect(() => {
    activeSelectionRef.current = activeSelection;
    neighborIdsRef.current = neighborIds;
    matchIdsRef.current = new Set(matches.map((m) => m.id));
    wake();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSelection, neighborIds, matches]);

  function wake() {
    if (runningRef.current || !active) return;
    runningRef.current = true;
    requestAnimationFrame(loop);
  }

  function loop() {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx || !dims) { runningRef.current = false; return; }

    const nodes = nodesRef.current;
    const settled = alphaRef.current < 0.01 && !dragRef.current;

    if (!settled) {
      const alpha = alphaRef.current;
      const repulsion = 2200 * alpha;
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i], b = nodes[j];
          let dx = a.x - b.x, dy = a.y - b.y;
          const distSq = dx * dx + dy * dy || 0.01;
          const force = repulsion / distSq;
          const dist = Math.sqrt(distSq);
          dx /= dist; dy /= dist;
          if (!a.fixed) { a.vx += dx * force; a.vy += dy * force; }
          if (!b.fixed) { b.vx -= dx * force; b.vy -= dy * force; }
        }
      }
      const byId = new Map(nodes.map((n) => [n.id, n]));
      const springLen = 120, springK = 0.02 * alpha;
      for (const e of edges) {
        const a = byId.get(e.source), b = byId.get(e.target);
        if (!a || !b) continue;
        let dx = b.x - a.x, dy = b.y - a.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const force = (dist - springLen) * springK;
        dx /= dist; dy /= dist;
        if (!a.fixed) { a.vx += dx * force; a.vy += dy * force; }
        if (!b.fixed) { b.vx -= dx * force; b.vy -= dy * force; }
      }
      const cx = dims.width / 2, cy = dims.height / 2, centerK = 0.003 * alpha;
      for (const n of nodes) {
        if (n.fixed) continue;
        n.vx += (cx - n.x) * centerK;
        n.vy += (cy - n.y) * centerK;
        n.vx *= 0.85; n.vy *= 0.85;
        n.x += n.vx; n.y += n.vy;
        n.x = Math.max(n.r, Math.min(dims.width - n.r, n.x));
        n.y = Math.max(n.r, Math.min(dims.height - n.r, n.y));
      }
      if (!dragRef.current) alphaRef.current *= 0.985;
    }

    canvas.width = dims.width;
    canvas.height = dims.height;
    ctx.clearRect(0, 0, dims.width, dims.height);
    const byId = new Map(nodes.map((n) => [n.id, n]));

    const selected = activeSelectionRef.current;
    const neighbors = neighborIdsRef.current;
    const matched = matchIdsRef.current;

    for (const e of edges) {
      const a = byId.get(e.source), b = byId.get(e.target);
      if (!a || !b) continue;
      const isHovered = hoveredEdgeRef.current === e;
      const touchesSelected = selected !== null && (e.source === selected || e.target === selected);
      const dim = selected !== null && !touchesSelected;
      ctx.strokeStyle = isHovered
        ? "rgba(92,139,255,0.9)"
        : touchesSelected
        ? "rgba(20,184,166,0.85)"
        : dim
        ? "rgba(140,164,189,0.08)"
        : "rgba(140,164,189,0.25)";
      ctx.lineWidth = isHovered || touchesSelected ? 2 : 1;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }

    for (const n of nodes) {
      const isHovered = hoveredNodeRef.current === n;
      const isSelected = selected !== null && n.id === selected;
      const isNeighbor = neighbors.has(n.id);
      const isMatch = matched.has(n.id);
      const dim = selected !== null && !isSelected && !isNeighbor;

      ctx.globalAlpha = dim ? 0.25 : 1;
      ctx.beginPath();
      ctx.arc(n.x, n.y, isSelected ? n.r + 6 : isHovered ? n.r + 3 : n.r, 0, Math.PI * 2);
      ctx.fillStyle = colorFor(n.type);
      ctx.fill();

      if (isSelected) {
        ctx.lineWidth = 3;
        ctx.strokeStyle = "#14b8a6";
        ctx.stroke();
      } else if (isNeighbor) {
        ctx.lineWidth = 2;
        ctx.strokeStyle = "#f3ece1";
        ctx.stroke();
      } else if (isMatch) {
        ctx.setLineDash([3, 2]);
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = "#14b8a6";
        ctx.stroke();
        ctx.setLineDash([]);
      } else if (isHovered) {
        ctx.lineWidth = 2;
        ctx.strokeStyle = "#f3ece1";
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    if (settled) { runningRef.current = false; return; }
    requestAnimationFrame(loop);
  }

  // (Re)build nodes only when the actual entity set changes, and (re)score
  // degree/radius whenever edges change -- mutating existing node objects
  // in place so x/y/vx/vy (and any in-progress drag) survive a relation
  // update instead of resetting the whole layout.
  useEffect(() => {
    if (!dims) return;
    const sig = names.join("");
    if (namesSigRef.current !== sig) {
      namesSigRef.current = sig;
      nodesRef.current = names.map((name, i) => {
        const angle = (i / Math.max(names.length, 1)) * Math.PI * 2;
        const type = graph.entities.find((e) => e.name === name)?.type ?? "OTHER";
        return {
          id: name, type, r: MIN_R, degree: 0,
          x: dims.width / 2 + Math.cos(angle) * 150,
          y: dims.height / 2 + Math.sin(angle) * 150,
          vx: 0, vy: 0,
        };
      });
    }

    const degree = new Map<string, number>();
    for (const e of edges) {
      degree.set(e.source, (degree.get(e.source) ?? 0) + 1);
      degree.set(e.target, (degree.get(e.target) ?? 0) + 1);
    }
    const maxDeg = Math.max(1, ...degree.values());
    for (const n of nodesRef.current) {
      n.degree = degree.get(n.id) ?? 0;
      n.r = MIN_R + Math.sqrt(n.degree / maxDeg) * (MAX_R - MIN_R);
    }

    alphaRef.current = 1;
    wake();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dims, names, edges]);

  useEffect(() => {
    if (active && dims) wake();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, dims]);

  const nearestNode = (mx: number, my: number): Node | null => {
    let best: Node | null = null, bestDist = Infinity;
    for (const n of nodesRef.current) {
      const d = Math.hypot(n.x - mx, n.y - my);
      if (d <= n.r + 4 && d < bestDist) { best = n; bestDist = d; }
    }
    return best;
  };

  const nearestEdge = (mx: number, my: number): Edge | null => {
    const byId = new Map(nodesRef.current.map((n) => [n.id, n]));
    let best: Edge | null = null, bestDist = 6;
    for (const e of edges) {
      const a = byId.get(e.source), b = byId.get(e.target);
      if (!a || !b) continue;
      const d = distToSegment(mx, my, a.x, a.y, b.x, b.y);
      if (d < bestDist) { best = e; bestDist = d; }
    }
    return best;
  };

  const setTooltip = (text: string | null, x?: number, y?: number) => {
    const el = tooltipRef.current;
    if (!el) return;
    if (!text) { el.style.display = "none"; return; }
    el.textContent = text;
    el.style.display = "block";
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
  };

  const onMouseDown = (e: React.MouseEvent) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const node = nearestNode(e.clientX - rect.left, e.clientY - rect.top);
    if (node) { node.fixed = true; dragRef.current = node; alphaRef.current = 1; wake(); }
  };
  const onMouseMove = (e: React.MouseEvent) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    if (dragRef.current) {
      dragRef.current.x = mx; dragRef.current.y = my;
      return;
    }
    const node = nearestNode(mx, my);
    if (node) {
      hoveredNodeRef.current = node;
      hoveredEdgeRef.current = null;
      setTooltip(`${node.id} · ${node.type} · ${node.degree} connection${node.degree === 1 ? "" : "s"}`, mx + 14, my + 8);
    } else {
      const edge = nearestEdge(mx, my);
      hoveredNodeRef.current = null;
      hoveredEdgeRef.current = edge;
      if (edge) setTooltip(`${edge.source} —[${edge.type}]→ ${edge.target}`, mx + 14, my + 8);
      else setTooltip(null);
    }
  };
  const onMouseUp = () => {
    if (dragRef.current) dragRef.current.fixed = false;
    dragRef.current = null;
  };
  const onMouseLeave = () => {
    onMouseUp();
    hoveredNodeRef.current = null;
    hoveredEdgeRef.current = null;
    setTooltip(null);
  };

  if (graph.entities.length === 0) {
    return <p className="muted">No entities were extracted to build a graph from.</p>;
  }

  return (
    <div ref={wrapRef} className="graph-wrap">
      <div className="graph-search-row">
        <input
          className="graph-search-input"
          value={query}
          onChange={(e) => { setQuery(e.target.value); setManualSelectedId(null); }}
          placeholder="Search entities, aliases, or relationship context…"
        />
        {query && (
          <button
            className="graph-search-clear"
            onClick={() => { setQuery(""); setManualSelectedId(null); }}
            aria-label="Clear search"
          >
            ✕
          </button>
        )}
      </div>
      {trimmedQuery && (
        <div className="graph-search-results">
          {matches.length === 0 && <span className="muted small">No matches for "{query.trim()}".</span>}
          {matches.slice(0, 12).map((m) => (
            <button
              key={m.id}
              className={`graph-search-chip${m.id === activeSelection ? " active" : ""}`}
              title={m.reason}
              onClick={() => setManualSelectedId(m.id)}
            >
              {m.id}
            </button>
          ))}
          {matches.length > 12 && <span className="muted small">+{matches.length - 12} more</span>}
        </div>
      )}
      <div className="graph-legend">
        {Object.entries(TYPE_COLORS).map(([type, color]) => (
          <span key={type} className="legend-item">
            <span className="legend-dot" style={{ background: color }} /> {type}
          </span>
        ))}
      </div>
      <div className="graph-canvas-wrap">
        {dims && (
          <canvas
            ref={canvasRef}
            width={dims.width}
            height={dims.height}
            className="graph-canvas"
            onMouseDown={onMouseDown}
            onMouseMove={onMouseMove}
            onMouseUp={onMouseUp}
            onMouseLeave={onMouseLeave}
          />
        )}
        <div ref={tooltipRef} className="graph-tooltip" style={{ display: "none" }} />
      </div>
      {activeSelection && selectedEntity && (
        <div className="graph-detail-panel">
          <div className="graph-detail-header">
            <span className="legend-dot" style={{ background: colorFor(selectedEntity.type) }} />
            <strong>{selectedEntity.name}</strong>
            <span className="dtype-chip">{selectedEntity.type}</span>
          </div>
          <p className="muted small">
            Confidence {(selectedEntity.confidence * 100).toFixed(0)}% · from{" "}
            {selectedEntity.source_file.split("/").pop()}
          </p>
          {selectedEntity.mentions.length > 0 && (
            <p className="graph-detail-mentions">Also referred to as: {selectedEntity.mentions.join(", ")}</p>
          )}
          <p className="graph-detail-subhead">
            {connectedRelations.length} direct connection{connectedRelations.length === 1 ? "" : "s"}
          </p>
          {connectedRelations.length === 0 ? (
            <p className="muted small">No connected entities.</p>
          ) : (
            <ul className="graph-detail-connections">
              {connectedRelations.map((r) => {
                const isOutgoing = r.source_entity === activeSelection;
                const otherId = isOutgoing ? r.target_entity : r.source_entity;
                return (
                  <li key={r.relation_id}>
                    <button className="graph-detail-neighbor" onClick={() => setManualSelectedId(otherId)}>
                      {isOutgoing ? "→" : "←"} {otherId}
                    </button>
                    <span className="muted small"> {r.relation_type}</span>
                    {r.evidence && <p className="graph-detail-evidence">"{r.evidence}"</p>}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
      <p className="muted small">
        {graph.entities.length} entities · {graph.relations.length} relations. Node size = connection count.
        Drag nodes, hover a node or a line to see details, or search above to highlight a node and its
        direct connections.
      </p>
    </div>
  );
}
