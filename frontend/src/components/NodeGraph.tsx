import type { GraphData } from "../types";

const NODE_W = 188;
const ROW_H = 16;
const HEAD_H = 24;
const GAP_X = 56;
const GAP_Y = 18;
const MAX_ROWS = 4;

function shortValue(v: unknown): string {
  if (typeof v === "string") return v.length > 22 ? `${v.slice(0, 21)}…` : v;
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  return String(v);
}

/** Read-only drawing of a ComfyUI workflow: columns by dependency depth, links as curves. */
export function NodeGraph({ graph, highlight = "SF:" }: { graph: GraphData; highlight?: string }) {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const depth = new Map<string, number>();
  const visit = (id: string, seen = new Set<string>()): number => {
    if (depth.has(id)) return depth.get(id)!;
    if (seen.has(id)) return 0;
    seen.add(id);
    const parents = graph.links.filter((l) => l.to === id).map((l) => l.from);
    const d = parents.length ? Math.max(...parents.map((p) => visit(p, seen))) + 1 : 0;
    depth.set(id, d);
    return d;
  };
  graph.nodes.forEach((n) => visit(n.id));

  const columns: string[][] = [];
  for (const n of graph.nodes) {
    const d = depth.get(n.id) ?? 0;
    (columns[d] ??= []).push(n.id);
  }
  const heights = new Map(graph.nodes.map((n) => [n.id, HEAD_H + Math.min(Object.keys(n.inputs).length, MAX_ROWS) * ROW_H + 8]));
  const pos = new Map<string, { x: number; y: number }>();
  let totalH = 0;
  columns.forEach((col, ci) => {
    let y = 12;
    col.forEach((id) => {
      pos.set(id, { x: 12 + ci * (NODE_W + GAP_X), y });
      y += heights.get(id)! + GAP_Y;
    });
    totalH = Math.max(totalH, y);
  });
  const width = 24 + columns.length * (NODE_W + GAP_X) - GAP_X;

  return (
    <div className="node-graph">
      <svg width={width} height={totalH + 4} role="img" aria-label="노드 구조">
        {graph.links.map((l, i) => {
          const a = pos.get(l.from);
          const b = pos.get(l.to);
          if (!a || !b) return null;
          const x1 = a.x + NODE_W, y1 = a.y + HEAD_H / 2, x2 = b.x, y2 = b.y + HEAD_H / 2;
          const mx = (x1 + x2) / 2;
          return <path key={i} d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`} fill="none" stroke="#4a4d55" strokeWidth={1.2} />;
        })}
        {graph.nodes.map((n) => {
          const p = pos.get(n.id)!;
          const exposed = n.title.startsWith(highlight);
          const entries = Object.entries(n.inputs).slice(0, MAX_ROWS);
          return (
            <g key={n.id} transform={`translate(${p.x},${p.y})`}>
              <rect width={NODE_W} height={heights.get(n.id)} rx={3} fill="#1d1e22" stroke={exposed ? "#8a8e97" : "#33353b"} />
              <rect width={NODE_W} height={HEAD_H} rx={3} fill={exposed ? "#2c2e34" : "#25262b"} />
              <text x={8} y={16} fontSize={11} fontWeight={700} fill="#e2e3e5">{n.title || n.class_type}</text>
              <text x={NODE_W - 8} y={16} fontSize={10} fill="#6f737b" textAnchor="end">#{n.id}</text>
              {entries.map(([k, v], i) => (
                <g key={k}>
                  <text x={8} y={HEAD_H + 13 + i * ROW_H} fontSize={10} fill="#7d818a">{k}</text>
                  <text x={NODE_W - 8} y={HEAD_H + 13 + i * ROW_H} fontSize={10} fill="#b7bac0" textAnchor="end">{shortValue(v)}</text>
                </g>
              ))}
              {!entries.length && <text x={8} y={HEAD_H + 13} fontSize={10} fill="#6f737b">{n.class_type}</text>}
            </g>
          );
        })}
      </svg>
      {byId.size === 0 && <p className="faint small" style={{ padding: 10 }}>노드가 없습니다.</p>}
    </div>
  );
}
