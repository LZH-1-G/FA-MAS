import type { ReplanRecord } from "../types";
import { fmt } from "../util";

function Ids({ ids, empty = "—" }: { ids: string[]; empty?: string }) {
  if (!ids.length) return <span className="muted">{empty}</span>;
  return (
    <span className="idlist">
      {ids.map((i) => (
        <code key={i}>{i}</code>
      ))}
    </span>
  );
}

export default function RecoveryPanel({ replans, onSelect }: { replans: ReplanRecord[]; onSelect: (id: string) => void }) {
  if (!replans.length) return <div className="muted">No failures so far — nothing to recover.</div>;
  return (
    <div className="panel-scroll">
      {replans.map((r) => (
        <div key={r.round} className="round">
          <div className="round-head">
            <h3>Recovery round {r.round}</h3>
            <span className="small muted">
              {r.policy} · at {fmt.sec(r.at_ms)} · overhead {fmt.int(r.overhead_tokens)} tok / {fmt.sec(r.overhead_ms)}
            </span>
          </div>
          {r.aborted && <div className="err small">Aborted: {r.abort_reason}</div>}
          {r.diagnoses.map((d, i) => (
            <div key={i} className="small" style={{ marginBottom: 8 }}>
              <div>
                <a onClick={() => onSelect(d.task_id)} style={{ cursor: "pointer" }}>
                  <code>{d.task_id}</code>
                </a>{" "}
                failed: <b>{d.failure_type.replace(/_/g, " ")}</b> → strategy <b>{d.strategy.replace(/_/g, " ")}</b>
                {d.root_cause_tasks.length > 0 && (
                  <>
                    {" "}
                    · root cause <Ids ids={d.root_cause_tasks} />
                  </>
                )}
              </div>
              <div className="muted">{d.explanation || d.rationale}</div>
            </div>
          ))}
          <div className="kv small">
            <span className="k">Reused</span>
            <Ids ids={r.reused} />
            <span className="k">Invalidated</span>
            <Ids ids={r.invalidated} />
            <span className="k">Re-executing</span>
            <Ids ids={r.retried} />
            <span className="k">Added</span>
            <Ids ids={r.added} />
            {r.impact && (
              <>
                <span className="k">Affected</span>
                <Ids ids={r.impact.affected} />
              </>
            )}
            {r.invalid_reuse.length > 0 && (
              <>
                <span className="k">Latent corrupt</span>
                <Ids ids={r.invalid_reuse} />
              </>
            )}
          </div>
          {r.ops.length > 0 && (
            <details style={{ marginTop: 6 }}>
              <summary className="small muted">{r.ops.length} graph edits</summary>
              <ul className="small mono">
                {r.ops.map((o, i) => (
                  <li key={i}>
                    {o.op} {o.task_id ?? ""} {Object.keys(o.detail ?? {}).length ? JSON.stringify(o.detail) : ""}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      ))}
    </div>
  );
}
