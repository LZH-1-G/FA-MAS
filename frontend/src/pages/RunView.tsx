import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, eventsUrl } from "../api";
import AgentsPanel from "../components/AgentsPanel";
import DagView from "../components/DagView";
import EventLog from "../components/EventLog";
import RecoveryPanel from "../components/RecoveryPanel";
import TaskPanel from "../components/TaskPanel";
import type { Meta, RunEvent, RunSnapshot } from "../types";
import { fmt } from "../util";

const EVENT_TYPES = [
  "run_started", "analysis_completed", "plan_created", "plan_validated", "agents_assigned", "task_started",
  "task_completed", "task_failed", "task_reused", "task_skipped", "wave_finished", "failure_diagnosed",
  "impact_analyzed", "replan_applied", "run_completed", "run_failed", "log",
];

type Tab = "task" | "recovery" | "agents" | "events" | "report";

function Metric({ k, v }: { k: string; v: string }) {
  return (
    <div className="metric">
      <div className="v">{v}</div>
      <div className="k">{k}</div>
    </div>
  );
}

export default function RunView({ runId, meta }: { runId: string; meta: Meta }) {
  const [snap, setSnap] = useState<RunSnapshot | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("task");
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const refresh = useCallback(() => {
    api.run(runId).then(setSnap).catch((e) => setError(String(e)));
  }, [runId]);

  const scheduleRefresh = useCallback(() => {
    if (timer.current != null) return;
    timer.current = window.setTimeout(() => {
      timer.current = null;
      refresh();
    }, 250);
  }, [refresh]);

  useEffect(() => {
    setSnap(null);
    setEvents([]);
    setSelected(null);
    refresh();
    const es = new EventSource(eventsUrl(runId));
    const onEvent = (msg: MessageEvent) => {
      const ev = JSON.parse(msg.data) as RunEvent;
      setEvents((prev) => (prev.length && prev[prev.length - 1].seq >= ev.seq ? prev : [...prev.slice(-1999), ev]));
      if (ev.type === "replan_applied") setTab((t) => (t === "task" ? "recovery" : t));
      scheduleRefresh();
    };
    for (const t of EVENT_TYPES) es.addEventListener(t, onEvent as EventListener);
    es.addEventListener("end", () => {
      es.close();
      refresh();
      window.setTimeout(refresh, 2500); // final graph + report are persisted shortly after completion
    });
    es.onerror = () => es.close();
    return () => {
      es.close();
      if (timer.current != null) window.clearTimeout(timer.current);
      timer.current = null;
    };
  }, [runId, refresh, scheduleRefresh]);

  if (error) return <div className="card err">{error}</div>;
  if (!snap) return <div className="muted">Loading run…</div>;

  const m = snap.metrics;
  const nodes = snap.view.nodes;
  const done = nodes.filter((n) => n.status === "completed").length;
  const node = nodes.find((n) => n.id === selected) ?? null;
  const sysName = meta.systems.find((s) => s.name === snap.config?.system)?.short ?? String(snap.config?.system ?? "");
  const scenario = meta.scenarios.find((s) => s.name === snap.config?.scenario)?.title ?? String(snap.config?.scenario ?? "");

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <section className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div className="status" style={{ fontSize: 14 }}>
            <span className={`dot ${snap.status}`} /> {snap.status}
            <span className="pill">{sysName}</span>
            <span className="pill">scenario: {scenario}</span>
            {snap.analysis && (
              <span className="pill">
                {snap.analysis.complexity} · {snap.analysis.strategy.replace("_", " ")}
              </span>
            )}
          </div>
          <span className="small muted mono">{snap.id}</span>
        </div>
        <p style={{ margin: "10px 0 0" }}>{snap.request}</p>
        {snap.error && <p className="err small">{snap.error}</p>}
      </section>

      <section className="metrics">
        <Metric k="Tasks done" v={`${done}/${nodes.length}`} />
        <Metric k="Latency" v={m ? fmt.sec(m.latency_ms) : fmt.sec(snap.t_ms ?? null)} />
        <Metric k="Tokens" v={fmt.int(m?.total_tokens)} />
        <Metric k="Cost" v={fmt.usd(m?.cost)} />
        <Metric k="Agent calls" v={fmt.int(m?.agent_invocations)} />
        <Metric k="Recovery rounds" v={String(m?.recovery_rounds ?? snap.replans.length)} />
        <Metric k="Reuse rate" v={fmt.pct(m?.reuse_rate)} />
        <Metric k="Redundant exec." v={fmt.int(m?.redundant_executions)} />
        <Metric k="Replan overhead" v={m ? `${fmt.int(m.overhead_tokens)} tok` : "–"} />
      </section>

      <div className="grid2">
        <section className="card">
          <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
            <h3 style={{ margin: 0 }}>Task dependency graph</h3>
            <span className="small muted">click a node to inspect · version {snap.view.version ?? "–"}</span>
          </div>
          {nodes.length ? (
            <DagView view={snap.view} selected={selected} onSelect={(id) => (setSelected(id), setTab("task"))} />
          ) : (
            <div className="muted">Analysing and planning…</div>
          )}
        </section>
        <section className="card">
          <div className="tabs">
            {(["task", "recovery", "agents", "events", "report"] as Tab[]).map((t) => (
              <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
                {t === "recovery" ? `recovery (${snap.replans.length})` : t}
              </button>
            ))}
          </div>
          {tab === "task" && <TaskPanel runId={runId} node={node} refreshKey={`${node?.status}-${node?.attempts}-${snap.live}`} />}
          {tab === "recovery" && <RecoveryPanel replans={snap.replans} onSelect={(id) => (setSelected(id), setTab("task"))} />}
          {tab === "agents" && <AgentsPanel snap={snap} />}
          {tab === "events" && <EventLog events={events} />}
          {tab === "report" &&
            (snap.final_output ? (
              <div className="panel-scroll markdown">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{snap.final_output}</ReactMarkdown>
              </div>
            ) : (
              <div className="muted">{snap.status === "running" ? "The report appears when the run finishes." : "No report produced."}</div>
            ))}
        </section>
      </div>

      {m && (
        <section className="card">
          <h3>Cost by component</h3>
          <table className="small">
            <thead>
              <tr>
                <th>Component</th>
                <th className="num">Calls</th>
                <th className="num">Tokens in</th>
                <th className="num">Tokens out</th>
                <th className="num">Cost</th>
                <th className="num">Model time</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(m.by_component)
                .sort((a, b) => b[1].cost - a[1].cost)
                .map(([k, u]) => (
                  <tr key={k}>
                    <td>{k}</td>
                    <td className="num">{u.calls}</td>
                    <td className="num">{fmt.int(u.tokens_in)}</td>
                    <td className="num">{fmt.int(u.tokens_out)}</td>
                    <td className="num">{fmt.usd(u.cost)}</td>
                    <td className="num">{fmt.sec(u.duration_ms)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
