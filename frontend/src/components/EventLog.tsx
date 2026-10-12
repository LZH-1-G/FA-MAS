import type { RunEvent } from "../types";

function describe(e: RunEvent): { text: string; cls: string } {
  const d = e.data;
  switch (e.type) {
    case "run_started":
      return { text: `run started (${d.config?.system ?? ""}, scenario ${d.config?.scenario ?? "none"})`, cls: "" };
    case "analysis_completed":
      return { text: `analysis: ${d.analysis?.complexity} · ${d.analysis?.strategy} · ${d.analysis?.subjects?.length ?? 0} subjects`, cls: "" };
    case "plan_created":
      return { text: `plan: ${d.view?.nodes?.length ?? 0} tasks in ${d.layers?.length ?? "?"} layers`, cls: "" };
    case "plan_validated":
      return { text: `plan ${d.valid ? "valid" : "INVALID"}${d.fixes?.length ? ` (${d.fixes.length} fixes)` : ""}`, cls: d.valid ? "" : "ev-failed" };
    case "agents_assigned":
      return { text: `assigned ${Object.entries(d.assignments ?? {}).map(([t, a]) => `${t}→${a}`).join(", ")}`, cls: "" };
    case "task_started":
      return { text: `▶ ${e.task_id} ← ${d.agent} (attempt ${d.attempt}${d.tag ? `, ${d.tag}` : ""})`, cls: "" };
    case "task_completed":
      return { text: `✓ ${e.task_id} (${d.tokens} tok, ${(d.duration_ms / 1000).toFixed(1)}s)`, cls: "ev-ok" };
    case "task_failed":
      return { text: `✗ ${e.task_id}: ${d.error_type} — ${d.message}`, cls: "ev-failed" };
    case "task_reused":
      return { text: `↺ ${e.task_id} reused (${d.how})`, cls: "ev-ok" };
    case "failure_diagnosed":
      return { text: `⚑ ${e.task_id}: ${d.diagnosis?.failure_type} → ${d.diagnosis?.strategy}`, cls: "ev-recovery" };
    case "impact_analyzed":
      return { text: `impact: reuse ${d.impact?.reused?.length ?? 0}, invalidate ${d.impact?.invalidated?.length ?? 0}, affected ${d.impact?.affected?.length ?? 0}`, cls: "ev-recovery" };
    case "replan_applied":
      return { text: `⟳ round ${d.record?.round}: ${d.record?.ops?.length ?? 0} graph edits`, cls: "ev-recovery" };
    case "wave_finished":
      return { text: `wave finished: ${d.completed?.length ?? 0} completed, ${d.failed?.length ?? 0} failed`, cls: "" };
    case "run_completed":
      return { text: "run completed", cls: "ev-ok" };
    case "run_failed":
      return { text: `run failed: ${d.error}`, cls: "ev-failed" };
    default:
      return { text: e.type, cls: "" };
  }
}

export default function EventLog({ events }: { events: RunEvent[] }) {
  if (!events.length) return <div className="muted">Waiting for events…</div>;
  return (
    <div className="panel-scroll events">
      {events.map((e) => {
        const { text, cls } = describe(e);
        return (
          <div key={e.seq} className={cls} title={text}>
            <span className="muted">{(e.t_ms / 1000).toFixed(1).padStart(6)}s </span>
            {text}
          </div>
        );
      })}
    </div>
  );
}
