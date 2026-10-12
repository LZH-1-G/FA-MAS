import type { RunSnapshot } from "../types";
import { fmt } from "../util";

export default function AgentsPanel({ snap }: { snap: RunSnapshot }) {
  const byAgent: Record<string, { running: string[]; done: string[]; failed: string[] }> = {};
  for (const n of snap.view.nodes) {
    if (!n.agent) continue;
    const a = (byAgent[n.agent] ??= { running: [], done: [], failed: [] });
    if (n.status === "running") a.running.push(n.id);
    else if (n.status === "completed") a.done.push(n.id);
    else if (n.status === "failed") a.failed.push(n.id);
  }
  const usage = snap.metrics?.by_agent ?? {};
  const agents = Array.from(new Set([...Object.keys(byAgent), ...Object.keys(usage)])).sort();
  if (!agents.length) return <div className="muted">No agents assigned yet.</div>;
  return (
    <table className="small">
      <thead>
        <tr>
          <th>Agent</th>
          <th>Active task</th>
          <th className="num">Done</th>
          <th className="num">Calls</th>
          <th className="num">Tokens</th>
          <th className="num">Cost</th>
        </tr>
      </thead>
      <tbody>
        {agents.map((a) => {
          const s = byAgent[a] ?? { running: [], done: [], failed: [] };
          const u = usage[a];
          return (
            <tr key={a}>
              <td>
                <span className="status">
                  <span className={`dot ${s.running.length ? "running" : s.done.length ? "completed" : "pending"}`} />
                  {a}
                </span>
              </td>
              <td>{s.running.length ? s.running.map((r) => <code key={r}>{r} </code>) : <span className="muted">idle</span>}</td>
              <td className="num">{s.done.length}</td>
              <td className="num">{u ? u.calls : "–"}</td>
              <td className="num">{u ? fmt.int(u.tokens_in + u.tokens_out) : "–"}</td>
              <td className="num">{u ? fmt.usd(u.cost) : "–"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
