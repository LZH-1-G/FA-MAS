import { useState } from "react";
import { api } from "../api";
import type { Meta } from "../types";
import { navigate } from "../util";
import RunsList from "./RunsList";

export default function NewRun({ meta }: { meta: Meta }) {
  const [request, setRequest] = useState(meta.examples[3] ?? meta.examples[0] ?? "");
  const [system, setSystem] = useState("fa_mas");
  const [scenario, setScenario] = useState("corruption");
  const [mode, setMode] = useState<"mock" | "live">("mock");
  const [pacing, setPacing] = useState<"paced" | "instant">("paced");
  const [timeScale, setTimeScale] = useState(meta.default_time_scale);
  const [seed, setSeed] = useState(0);
  const [concurrency, setConcurrency] = useState(4);
  const [detect, setDetect] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sys = meta.systems.find((s) => s.name === system);
  const sc = meta.scenarios.find((s) => s.name === scenario);
  const claude = meta.llm_provider === "claude_agent_sdk";
  const liveLabel = claude ? "Claude Agent (experimental)" : "Live LLM";
  const liveHint = meta.live_available ? "" : claude ? " (unavailable)" : " (configure API key)";

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const { run_id } = await api.startRun({
        request,
        system,
        scenario,
        mode,
        seed,
        max_concurrency: concurrency,
        simulated: mode === "mock" && pacing === "instant",
        time_scale: timeScale,
        detection_probability: detect,
      });
      navigate(`/runs/${run_id}`);
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <section className="card">
        <h2>Submit a task</h2>
        <p className="muted small" style={{ marginTop: 0 }}>
          FA-MAS analyses the request, plans a task DAG, selects agents, executes independent tasks in parallel and
          recovers from failures by replanning only the affected part of the graph.
        </p>
        <textarea value={request} onChange={(e) => setRequest(e.target.value)} placeholder="Describe a complex task…" />
        <div className="chips" style={{ margin: "8px 0 14px" }}>
          {meta.examples.map((ex) => (
            <button key={ex} className="chip" title={ex} onClick={() => setRequest(ex)}>
              {ex}
            </button>
          ))}
        </div>
        <div className="form-grid">
          <label>
            System
            <select value={system} onChange={(e) => setSystem(e.target.value)}>
              {meta.systems.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.short}
                </option>
              ))}
            </select>
          </label>
          <label>
            Failure scenario
            <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
              {meta.scenarios.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.title}
                </option>
              ))}
            </select>
          </label>
          <label title={meta.live_reason}>
            Mode
            <select value={mode} onChange={(e) => setMode(e.target.value as "mock" | "live")}>
              <option value="mock">Mock LLM (deterministic)</option>
              <option value="live" disabled={!meta.live_available}>
                {liveLabel}
                {liveHint}
              </option>
            </select>
          </label>
          <label>
            Pacing
            <select value={pacing} onChange={(e) => setPacing(e.target.value as "paced" | "instant")} disabled={mode === "live"}>
              <option value="paced">Real time (watch it run)</option>
              <option value="instant">Instant (virtual clock)</option>
            </select>
          </label>
          <label>
            Time scale
            <input type="number" step="0.01" min="0" value={timeScale} onChange={(e) => setTimeScale(Number(e.target.value))} disabled={pacing === "instant" || mode === "live"} />
          </label>
          <label>
            Max concurrency
            <input type="number" min="1" max="32" value={concurrency} onChange={(e) => setConcurrency(Number(e.target.value))} />
          </label>
          <label>
            Seed
            <input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} />
          </label>
          <label>
            Corruption detection p
            <input type="number" step="0.1" min="0" max="1" value={detect} onChange={(e) => setDetect(Number(e.target.value))} />
          </label>
        </div>
        <div className="small muted" style={{ marginTop: 10 }}>
          {sys && <div><b>{sys.title}.</b> {sys.description}</div>}
          {sc && <div><b>{sc.title}.</b> {sc.description}</div>}
          {meta.live_reason && (mode === "live" || (claude && !meta.live_available)) && (
            <div>
              <b>{liveLabel}:</b> {meta.live_reason}
            </div>
          )}
        </div>
        <div className="row" style={{ marginTop: 14 }}>
          <button className="primary" disabled={busy || request.trim().length < 3} onClick={submit}>
            {busy ? "Starting…" : "Run"}
          </button>
          {error && <span className="err">{error}</span>}
        </div>
      </section>
      <section className="card">
        <h3>Recent runs</h3>
        <RunsList compact />
      </section>
    </div>
  );
}
