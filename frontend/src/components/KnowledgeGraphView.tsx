import { useEffect, useRef, useState } from "react";
import type { KnowledgeGraphExport } from "../api/types";

interface Node {
  id: string;
  type: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
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

// Minimal self-contained force simulation — no external graph library needed,
// which keeps the frontend's npm dependency surface small for offline installs.
function useForceLayout(nodes: Node[], edges: Edge[], width: number, height: number) {
  useEffect(() => {
    if (nodes.length === 0) return;
    let frame: number;
    const byId = new Map(nodes.map((n) => [n.id, n]));

    function tick() {
      const repulsion = 2200;
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i], b = nodes[j];
          let dx = a.x - b.x, dy = a.y - b.y;
          let distSq = dx * dx + dy * dy || 0.01;
          const force = repulsion / distSq;
          const dist = Math.sqrt(distSq);
          dx /= dist; dy /= dist;
          if (!a.fixed) { a.vx += dx * force; a.vy += dy * force; }
          if (!b.fixed) { b.vx -= dx * force; b.vy -= dy * force; }
        }
      }
      const springLen = 120, springK = 0.02;
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
      const cx = width / 2, cy = height / 2, centerK = 0.003;
      for (const n of nodes) {
        if (n.fixed) continue;
        n.vx += (cx - n.x) * centerK;
        n.vy += (cy - n.y) * centerK;
        n.vx *= 0.85; n.vy *= 0.85;
        n.x += n.vx; n.y += n.vy;
        n.x = Math.max(20, Math.min(width - 20, n.x));
        n.y = Math.max(20, Math.min(height - 20, n.y));
      }
      frame = requestAnimationFrame(tick);
    }
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [nodes, edges, width, height]);
}

export function KnowledgeGraphView({ graph }: { graph: KnowledgeGraphExport }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState({ width: 800, height: 520 });
  const [hovered, setHovered] = useState<Node | null>(null);
  const nodesRef = useRef<Node[]>([]);
  const dragRef = useRef<Node | null>(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setDims({ width: el.clientWidth, height: 520 }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const names = Array.from(new Set(graph.entities.map((e) => e.name)));
  if (nodesRef.current.length !== names.length) {
    nodesRef.current = names.map((name, i) => {
      const angle = (i / Math.max(names.length, 1)) * Math.PI * 2;
      const type = graph.entities.find((e) => e.name === name)?.type ?? "OTHER";
      return {
        id: name, type,
        x: dims.width / 2 + Math.cos(angle) * 150,
        y: dims.height / 2 + Math.sin(angle) * 150,
        vx: 0, vy: 0,
      };
    });
  }
  const edges: Edge[] = graph.relations.map((r) => ({
    source: r.source_entity, target: r.target_entity, type: r.relation_type,
  }));

  useForceLayout(nodesRef.current, edges, dims.width, dims.height);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    let raf: number;

    function draw() {
      canvas!.width = dims.width;
      canvas!.height = dims.height;
      ctx!.clearRect(0, 0, dims.width, dims.height);
      const byId = new Map(nodesRef.current.map((n) => [n.id, n]));

      ctx!.strokeStyle = "rgba(140,164,189,0.25)";
      ctx!.lineWidth = 1;
      for (const e of edges) {
        const a = byId.get(e.source), b = byId.get(e.target);
        if (!a || !b) continue;
        ctx!.beginPath();
        ctx!.moveTo(a.x, a.y);
        ctx!.lineTo(b.x, b.y);
        ctx!.stroke();
      }

      for (const n of nodesRef.current) {
        ctx!.beginPath();
        ctx!.arc(n.x, n.y, n === hovered ? 9 : 6, 0, Math.PI * 2);
        ctx!.fillStyle = colorFor(n.type);
        ctx!.fill();
        if (n === hovered) {
          ctx!.fillStyle = "#e8f0fa";
          ctx!.font = "11px monospace";
          ctx!.fillText(n.id, n.x + 12, n.y + 4);
        }
      }
      raf = requestAnimationFrame(draw);
    }
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [dims, edges, hovered]);

  const nearestNode = (mx: number, my: number): Node | null => {
    let best: Node | null = null, bestDist = 16;
    for (const n of nodesRef.current) {
      const d = Math.hypot(n.x - mx, n.y - my);
      if (d < bestDist) { best = n; bestDist = d; }
    }
    return best;
  };

  const onMouseDown = (e: React.MouseEvent) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const node = nearestNode(e.clientX - rect.left, e.clientY - rect.top);
    if (node) { node.fixed = true; dragRef.current = node; }
  };
  const onMouseMove = (e: React.MouseEvent) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    if (dragRef.current) {
      dragRef.current.x = mx; dragRef.current.y = my;
    } else {
      setHovered(nearestNode(mx, my));
    }
  };
  const onMouseUp = () => {
    if (dragRef.current) dragRef.current.fixed = false;
    dragRef.current = null;
  };

  if (graph.entities.length === 0) {
    return <p className="muted">No entities were extracted to build a graph from.</p>;
  }

  return (
    <div ref={wrapRef}>
      <div className="graph-legend">
        {Object.entries(TYPE_COLORS).map(([type, color]) => (
          <span key={type} className="legend-item">
            <span className="legend-dot" style={{ background: color }} /> {type}
          </span>
        ))}
      </div>
      <canvas
        ref={canvasRef}
        width={dims.width}
        height={dims.height}
        className="graph-canvas"
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
      />
      <p className="muted small">{graph.entities.length} entities · {graph.relations.length} relations. Drag nodes, hover to see names.</p>
    </div>
  );
}
