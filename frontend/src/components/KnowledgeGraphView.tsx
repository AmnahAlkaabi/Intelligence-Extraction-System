import { useEffect, useMemo, useRef, useState } from "react";
import type { KnowledgeGraphExport } from "../api/types";

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

    for (const e of edges) {
      const a = byId.get(e.source), b = byId.get(e.target);
      if (!a || !b) continue;
      const isHovered = hoveredEdgeRef.current === e;
      ctx.strokeStyle = isHovered ? "rgba(92,139,255,0.9)" : "rgba(140,164,189,0.25)";
      ctx.lineWidth = isHovered ? 2 : 1;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }

    for (const n of nodes) {
      const isHovered = hoveredNodeRef.current === n;
      ctx.beginPath();
      ctx.arc(n.x, n.y, isHovered ? n.r + 3 : n.r, 0, Math.PI * 2);
      ctx.fillStyle = colorFor(n.type);
      ctx.fill();
      if (isHovered) {
        ctx.lineWidth = 2;
        ctx.strokeStyle = "#f3ece1";
        ctx.stroke();
      }
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
      <p className="muted small">
        {graph.entities.length} entities · {graph.relations.length} relations. Node size = connection count.
        Drag nodes, hover a node or a line to see details.
      </p>
    </div>
  );
}
