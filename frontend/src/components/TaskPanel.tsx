import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../api";
import type { CheckResult, GraphNode, TaskDetail } from "../types";
import { fmt, TAG_LABEL } from "../util";

function Checks({ checks }: { checks: CheckResult[] }) {
  return (
    <div>
      {checks.map((c) => (
        <div key={c.name} className="check">
          <span className={c.passed ? "ev-ok" : "ev-failed"}>{c.passed ? "✓" : "✗"}</span>
          <b>{c.name}</b>
          <span className="muted">{c.detail}</span>
        </div>
      ))}
    </div>
  );
}

export default function TaskPanel({ runId, node, refreshKey }: { runId: string; node: GraphNode | null; refreshKey: string }) {
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!node) return;
    let alive = true;
    api
      .task(runId, node.id)
      .then((d) => alive && (setDetail(d), setError(null)))
      .catch((e) => alive && setError(String(e)));
    return () => {
      alive = false;
    };
  }, [runId, node?.id, refreshKey]);

  if (!node) return <div className="muted">Select a task in the graph to inspect it.</div>;
  if (error) return <div className="err">{error}</div>;
  const d = detail;
  const content = d?.result?.content ?? d?.preview ?? null;
  const checks = d?.evaluation?.checks ?? d?.checks ?? [];

  return (
    <div className="panel-scroll">
      <h3>{node.name}</h3>
      <div className="kv">
        <span className="k">Task id</span>
        <code>{node.id}</code>
        <span className="k">Status</span>
        <span className="status">
          <span className={`dot ${node.status}`} />
          {node.status}
          {node.tag && <span className={`tag ${node.tag}`}>{TAG_LABEL[node.tag] ?? node.tag}</span>}
        </span>
        <span className="k">Agent</span>
        <span>{node.agent ?? "–"}</span>
        <span className="k">Attempts</span>
        <span>{node.attempts}</span>
        {d?.dependencies && (
          <>
            <span className="k">Depends on</span>
            <span className="idlist">{d.dependencies.length ? d.dependencies.map((x) => <code key={x}>{x}</code>) : "—"}</span>
          </>
        )}
        {d?.excluded_agents && d.excluded_agents.length > 0 && (
          <>
            <span className="k">Excluded</span>
            <span>{d.excluded_agents.join(", ")}</span>
          </>
        )}
        {d?.result && (
          <>
            <span className="k">Usage</span>
            <span>
              {fmt.int((d.result.tokens_in ?? 0) + (d.result.tokens_out ?? 0))} tokens · {fmt.usd(d.result.cost)} · {fmt.sec(d.result.duration_ms)}
            </span>
            <span className="k">Fingerprints</span>
            <span className="mono small">
              in {d.result.input_fingerprint?.slice(0, 10)} → out {d.result.output_fingerprint?.slice(0, 10)}
            </span>
          </>
        )}
      </div>
      {d?.description && <p className="small">{d.description}</p>}
      {d?.feedback && d.feedback.length > 0 && (
        <>
          <h4>Recovery feedback</h4>
          <ul className="small">{d.feedback.map((f, i) => <li key={i}>{f}</li>)}</ul>
        </>
      )}
      {d?.selection && d.selection.candidates?.length > 0 && (
        <>
          <h4>Agent selection ({d.selection.strategy})</h4>
          <table className="small">
            <thead>
              <tr>
                <th>Agent</th>
                <th className="num">Score</th>
                <th className="num">Reliab.</th>
                <th className="num">Fit</th>
                <th className="num">Cost</th>
              </tr>
            </thead>
            <tbody>
              {d.selection.candidates.map((c) => (
                <tr key={c.agent_id}>
                  <td>
                    {c.agent_id === d.selection!.agent_id ? <b>{c.agent_id}</b> : c.agent_id}
                    {!c.eligible && <span className="muted"> ({c.reason})</span>}
                  </td>
                  <td className="num">{fmt.num(c.score)}</td>
                  <td className="num">{fmt.num(c.breakdown.reliability)}</td>
                  <td className="num">{fmt.num(c.breakdown.fit)}</td>
                  <td className="num">{fmt.num(c.breakdown.cost)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {checks.length > 0 && (
        <>
          <h4>Evaluation</h4>
          <Checks checks={checks} />
        </>
      )}
      {d?.failures && d.failures.length > 0 && (
        <>
          <h4>Failures</h4>
          {d.failures.map((f, i) => (
            <div key={i} className="round small">
              <div>
                <b>attempt {f.attempt}</b> · {f.agent_id ?? f.agent_id} · <span className="err">{f.error_type}</span>
              </div>
              <div className="muted">{f.message}</div>
              {(f.evaluation?.checks ?? f.checks) && <Checks checks={(f.evaluation?.checks ?? f.checks)!} />}
            </div>
          ))}
        </>
      )}
      {content && (
        <>
          <h4>Output</h4>
          <div className="output markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
          </div>
        </>
      )}
      {d?.result?.sources && d.result.sources.length > 0 && (
        <>
          <h4>Sources</h4>
          <ul className="small">
            {d.result.sources.map((s, i) => (
              <li key={i}>{s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.title}</a> : s.title}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
