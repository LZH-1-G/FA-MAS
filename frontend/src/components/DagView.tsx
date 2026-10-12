import { useMemo } from "react";
import dagre from "@dagrejs/dagre";
import { Background, Controls, Handle, MiniMap, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import type { GraphNode, GraphView } from "../types";
import { TAG_LABEL } from "../util";

type TaskNodeData = { task: GraphNode; selected: boolean };
type TaskFlowNode = Node<TaskNodeData, "task">;

const NODE_W = 210;
const NODE_H = 64;

function TaskNodeCard({ data }: NodeProps<TaskFlowNode>) {
  const t = data.task;
  const tag = t.tag ? TAG_LABEL[t.tag] ?? t.tag : null;
  return (
    <div className={`tnode ${t.status} ${data.selected ? "selected" : ""}`} title={`${t.name} — ${t.status}`}>
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div className="bar" />
      <div className="title">{t.name}</div>
      <div className="meta">
        <span>{t.agent ?? "unassigned"}</span>
        <span>
          {t.attempts > 1 ? `×${t.attempts} ` : ""}
          {tag && <span className={`tag ${t.tag}`}>{tag}</span>}
        </span>
      </div>
      <div className="meta">
        <span className="status">
          <span className={`dot ${t.status}`} />
          {t.status}
        </span>
        <span>{t.type}</span>
      </div>
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}

const nodeTypes = { task: TaskNodeCard };

function layout(view: GraphView): Record<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 22, ranksep: 70, marginx: 10, marginy: 10 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of view.nodes) g.setNode(n.id, { width: NODE_W, height: NODE_H + 14 });
  for (const e of view.edges) g.setEdge(e.source, e.target);
  dagre.layout(g);
  const pos: Record<string, { x: number; y: number }> = {};
  for (const n of view.nodes) {
    const p = g.node(n.id);
    pos[n.id] = { x: (p?.x ?? 0) - NODE_W / 2, y: (p?.y ?? 0) - NODE_H / 2 };
  }
  return pos;
}

export default function DagView({ view, selected, onSelect }: { view: GraphView; selected: string | null; onSelect: (id: string) => void }) {
  const structureKey = useMemo(
    () => view.nodes.map((n) => n.id).sort().join(",") + "|" + view.edges.map((e) => `${e.source}>${e.target}`).sort().join(","),
    [view],
  );
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const positions = useMemo(() => layout(view), [structureKey]);

  const nodes: TaskFlowNode[] = view.nodes.map((t) => ({
    id: t.id,
    type: "task",
    position: positions[t.id] ?? { x: 0, y: 0 },
    data: { task: t, selected: t.id === selected },
    draggable: false,
  }));
  const status = Object.fromEntries(view.nodes.map((n) => [n.id, n.status]));
  const edges: Edge[] = view.edges.map((e) => ({
    id: `${e.source}->${e.target}`,
    source: e.source,
    target: e.target,
    animated: status[e.target] === "running",
    style: { strokeWidth: 1.5, stroke: status[e.source] === "completed" ? "var(--text-3)" : "var(--border)" },
  }));

  return (
    <div>
      <div className="dag">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.15 }}
          minZoom={0.2}
          nodesConnectable={false}
          onNodeClick={(_, n) => onSelect(n.id)}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={18} size={1} color="var(--border)" />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable nodeStrokeWidth={2} />
        </ReactFlow>
      </div>
      <div className="legend">
        <span><span className="dot pending" /> pending</span>
        <span><span className="dot running" /> running</span>
        <span><span className="dot completed" /> completed</span>
        <span><span className="dot failed" /> failed</span>
        <span><span className="dot skipped" /> skipped</span>
        <span><span className="tag reused">reused</span> kept across recovery</span>
        <span><span className="tag retry">retry</span> re-executed</span>
        <span><span className="tag repair">repair</span> root cause fixed</span>
        <span><span className="tag new">new</span> added by replanner</span>
      </div>
    </div>
  );
}
