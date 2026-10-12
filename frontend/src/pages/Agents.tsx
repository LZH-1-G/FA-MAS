import { useEffect, useState } from "react";
import { api } from "../api";
import type { AgentSpec } from "../types";
import { fmt } from "../util";

type Stat = { agent_id: string; task_type: string; successes: number; failures: number; avg_ms: number; tokens: number };

export default function Agents() {
  const [agents, setAgents] = useState<AgentSpec[]>([]);
  const [stats, setStats] = useState<Stat[]>([]);
  useEffect(() => {
    api.agents().then((r) => {
      setAgents(r.agents);
      setStats(r.stats as unknown as Stat[]);
    });
  }, []);
  return (
    <div style={{ display: "grid", gap: 16 }}>
      <section className="card">
        <h2>Agent registry</h2>
        <table>
          <thead>
            <tr>
              <th>Agent</th>
              <th>Kind</th>
              <th>Model tier</th>
              <th>Capabilities</th>
              <th className="num">Prior reliability</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            {agents.map((a) => (
              <tr key={a.id}>
                <td><b>{a.id}</b></td>
                <td>{a.kind}</td>
                <td>{a.model_tier}</td>
                <td className="small">{a.capabilities.join(", ")}</td>
                <td className="num">{fmt.pct(a.prior_reliability)}</td>
                <td className="small muted">{a.description}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="card">
        <h2>Learned performance (all API runs)</h2>
        {stats.length === 0 ? (
          <div className="muted">No history yet — run a few tasks.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Agent</th>
                <th>Task type</th>
                <th className="num">Successes</th>
                <th className="num">Failures</th>
                <th className="num">Success rate</th>
                <th className="num">Avg duration</th>
                <th className="num">Tokens</th>
              </tr>
            </thead>
            <tbody>
              {stats.map((s) => (
                <tr key={`${s.agent_id}-${s.task_type}`}>
                  <td>{s.agent_id}</td>
                  <td>{s.task_type}</td>
                  <td className="num">{s.successes}</td>
                  <td className="num">{s.failures}</td>
                  <td className="num">{fmt.pct(s.successes / Math.max(1, s.successes + s.failures))}</td>
                  <td className="num">{fmt.sec(s.avg_ms)}</td>
                  <td className="num">{fmt.int(s.tokens)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
