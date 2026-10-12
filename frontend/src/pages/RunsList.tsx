import { useEffect, useState } from "react";
import { api } from "../api";
import type { RunListItem } from "../types";
import { fmt, navigate } from "../util";

export default function RunsList({ compact = false }: { compact?: boolean }) {
  const [runs, setRuns] = useState<RunListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => api.runs().then((r) => alive && setRuns(r)).catch((e) => alive && setError(String(e)));
    load();
    const id = window.setInterval(load, 3000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  if (error) return <div className="err">{error}</div>;
  if (!runs) return <div className="muted">Loading…</div>;
  if (runs.length === 0) return <div className="muted">No runs yet.</div>;
  const rows = compact ? runs.slice(0, 8) : runs;

  const table = (
    <table>
      <thead>
        <tr>
          <th>Status</th>
          <th>Request</th>
          <th>System</th>
          <th>Scenario</th>
          <th className="num">Latency</th>
          <th className="num">Tokens</th>
          <th className="num">Cost</th>
          <th className="num">Recoveries</th>
          {!compact && <th>Started</th>}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id} className="clickable" onClick={() => navigate(`/runs/${r.id}`)}>
            <td>
              <span className="status">
                <span className={`dot ${r.status}`} />
                {r.status}
              </span>
            </td>
            <td style={{ maxWidth: 420 }}>
              <div style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={r.request}>
                {r.request}
              </div>
            </td>
            <td>{r.system}</td>
            <td>{r.scenario}</td>
            <td className="num">{fmt.sec(r.latency_ms)}</td>
            <td className="num">{fmt.int(r.tokens)}</td>
            <td className="num">{fmt.usd(r.cost)}</td>
            <td className="num">{r.recovery_rounds ?? "–"}</td>
            {!compact && <td className="small muted">{fmt.time(r.created_at)}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
  return compact ? table : <section className="card"><h2>Runs</h2>{table}</section>;
}
