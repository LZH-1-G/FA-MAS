import { useEffect, useState } from "react";
import { api } from "../api";
import GroupedBars from "../components/GroupedBars";
import type { BenchAgg, Benchmark, Meta } from "../types";
import { fmt, SYSTEM_ORDER } from "../util";

const QUICK = ["none", "transient", "corruption", "late_failure", "cascade"];

export default function Benchmarks({ meta }: { meta: Meta }) {
  const [list, setList] = useState<Benchmark[]>([]);
  const [current, setCurrent] = useState<Benchmark | null>(null);
  const [systems, setSystems] = useState<string[]>(SYSTEM_ORDER);
  const [scenarios, setScenarios] = useState<string[]>(QUICK);
  const [tasks, setTasks] = useState<string[]>(["T2", "T4"]);
  const [error, setError] = useState<string | null>(null);

  const loadList = () => api.benchmarks().then((l) => {
    setList(l);
    if (!current && l.length) api.benchmark(l[0].id).then(setCurrent);
  });

  useEffect(() => {
    loadList().catch((e) => setError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!current || current.status !== "running") return;
    const id = window.setInterval(() => api.benchmark(current.id).then(setCurrent), 700);
    return () => window.clearInterval(id);
  }, [current?.id, current?.status]);

  async function start() {
    setError(null);
    try {
      const { benchmark_id } = await api.startBenchmark({ systems, scenarios, tasks, seeds: [0, 1, 2] });
      setCurrent(await api.benchmark(benchmark_id));
      loadList();
    } catch (e) {
      setError(String(e));
    }
  }

  const toggle = (arr: string[], set: (v: string[]) => void, v: string) => set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  const s = current?.summary ?? null;
  const sysKeys = s ? SYSTEM_ORDER.filter((k) => k in s.overall) : [];
  const series = sysKeys.map((k) => ({ key: k, label: s?.systems[k] ?? k }));
  const scen = s ? Object.keys(s.per_scenario) : [];

  const cell = (a: BenchAgg | undefined, key: keyof BenchAgg, kind: "pct" | "int" | "s" | "usd" | "num") => {
    const v = a?.[key] as number | null | undefined;
    return kind === "pct" ? fmt.pct(v) : kind === "int" ? fmt.int(v) : kind === "s" ? (v == null ? "–" : `${v.toFixed(1)}s`) : kind === "usd" ? fmt.usd(v) : fmt.num(v);
  };

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <section className="card">
        <h2>Benchmark: FA-MAS vs. baselines</h2>
        <p className="small muted" style={{ marginTop: 0 }}>
          Runs every task × scenario × system on the deterministic simulator (virtual clock, mock LLM). Stochastic scenarios use seeds 0–2.
        </p>
        <div className="form-grid">
          <div>
            <h4>Systems</h4>
            {meta.systems.map((x) => (
              <label key={x.name} style={{ flexDirection: "row", alignItems: "center" }}>
                <input type="checkbox" checked={systems.includes(x.name)} onChange={() => toggle(systems, setSystems, x.name)} /> {x.short}
              </label>
            ))}
          </div>
          <div>
            <h4>Scenarios</h4>
            {meta.scenarios.map((x) => (
              <label key={x.name} style={{ flexDirection: "row", alignItems: "center" }}>
                <input type="checkbox" checked={scenarios.includes(x.name)} onChange={() => toggle(scenarios, setScenarios, x.name)} /> {x.title}
              </label>
            ))}
          </div>
          <div>
            <h4>Tasks</h4>
            {meta.benchmark_tasks.map((t) => (
              <label key={t.id} style={{ flexDirection: "row", alignItems: "center" }} title={t.request}>
                <input type="checkbox" checked={tasks.includes(t.id)} onChange={() => toggle(tasks, setTasks, t.id)} /> {t.id} ({t.complexity})
              </label>
            ))}
          </div>
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" onClick={start} disabled={!systems.length || !scenarios.length || !tasks.length}>
            Run benchmark
          </button>
          {current?.status === "running" && (
            <span className="small">
              running… {current.progress?.done ?? 0}/{current.progress?.total ?? "?"}
            </span>
          )}
          {error && <span className="err">{error}</span>}
          {list.length > 0 && (
            <select value={current?.id ?? ""} onChange={(e) => api.benchmark(e.target.value).then(setCurrent)}>
              {list.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.id} · {b.status} · {fmt.time(b.created_at)}
                </option>
              ))}
            </select>
          )}
        </div>
      </section>

      {s && (
        <>
          <section className="card">
            <h3>Under failure (all scenarios except “none”) — {s.n_runs} runs total</h3>
            <table>
              <thead>
                <tr>
                  <th>System</th>
                  <th className="num">Correct</th>
                  <th className="num">Latency</th>
                  <th className="num">Tokens</th>
                  <th className="num">Cost</th>
                  <th className="num">Agent calls</th>
                  <th className="num">Reuse rate</th>
                  <th className="num">Invalid reuse</th>
                  <th className="num">Redundant</th>
                  <th className="num">Overhead tok</th>
                </tr>
              </thead>
              <tbody>
                {sysKeys.map((k) => {
                  const a = s.under_failure[k];
                  return (
                    <tr key={k}>
                      <td>{s.systems[k] ?? k}</td>
                      <td className="num">{cell(a, "correct_rate", "pct")}</td>
                      <td className="num">{cell(a, "latency_s", "s")}</td>
                      <td className="num">{cell(a, "tokens", "int")}</td>
                      <td className="num">{cell(a, "cost_usd", "usd")}</td>
                      <td className="num">{cell(a, "agent_invocations", "num")}</td>
                      <td className="num">{cell(a, "reuse_rate", "pct")}</td>
                      <td className="num">{cell(a, "invalid_reuse_rate", "pct")}</td>
                      <td className="num">{cell(a, "redundant_executions", "num")}</td>
                      <td className="num">{cell(a, "overhead_tokens", "int")}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>
          <section className="card">
            <h3>Mean tokens per run by scenario</h3>
            <GroupedBars
              groups={scen.map((k) => ({ key: k, label: k.replace(/_/g, " ") }))}
              series={series}
              value={(g, sk) => s.per_scenario[g]?.[sk]?.tokens}
              format={(v) => `${Math.round(v).toLocaleString()} tokens`}
            />
          </section>
          <section className="card">
            <h3>Mean latency per run by scenario (simulated seconds)</h3>
            <GroupedBars
              groups={scen.map((k) => ({ key: k, label: k.replace(/_/g, " ") }))}
              series={series}
              value={(g, sk) => s.per_scenario[g]?.[sk]?.latency_s}
              format={(v) => `${v.toFixed(1)}s`}
            />
          </section>
          <section className="card">
            <h3>Correct final output, by scenario</h3>
            <table>
              <thead>
                <tr>
                  <th>Scenario</th>
                  {sysKeys.map((k) => (
                    <th key={k} className="num">{s.systems[k] ?? k}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {scen.map((sc) => (
                  <tr key={sc}>
                    <td>{s.scenarios[sc] ?? sc}</td>
                    {sysKeys.map((k) => {
                      const v = s.per_scenario[sc]?.[k]?.correct_rate ?? null;
                      return (
                        <td key={k} className="num" style={{ background: v == null ? undefined : `color-mix(in srgb, var(--accent) ${Math.round(v * 35)}%, transparent)` }}>
                          {fmt.pct(v)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </div>
  );
}
