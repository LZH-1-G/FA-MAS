import { useEffect, useState } from "react";
import { api } from "./api";
import type { Meta } from "./types";
import { useHashRoute } from "./util";
import NewRun from "./pages/NewRun";
import RunsList from "./pages/RunsList";
import RunView from "./pages/RunView";
import Benchmarks from "./pages/Benchmarks";
import Agents from "./pages/Agents";

export default function App() {
  const route = useHashRoute();
  const [meta, setMeta] = useState<Meta | null>(null);
  const [health, setHealth] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.meta().then(setMeta).catch((e) => setError(String(e)));
    api.health().then(setHealth).catch(() => undefined);
  }, []);

  const section = route[0] ?? "";
  const link = (href: string, label: string, active: boolean) => (
    <a href={href} className={active ? "active" : ""}>
      {label}
    </a>
  );

  let page;
  if (error) page = <div className="card err">Cannot reach the FA-MAS API: {error}. Start it with <code>famas serve</code>.</div>;
  else if (!meta) page = <div className="muted">Loading…</div>;
  else if (section === "runs" && route[1]) page = <RunView runId={route[1]} meta={meta} />;
  else if (section === "runs") page = <RunsList />;
  else if (section === "bench") page = <Benchmarks meta={meta} />;
  else if (section === "agents") page = <Agents />;
  else page = <NewRun meta={meta} />;

  return (
    <>
      <header className="topbar">
        <div className="brand">
          FA-MAS <small>failure-aware multi-agent console</small>
        </div>
        <nav className="nav">
          {link("#/", "New run", section === "")}
          {link("#/runs", "Runs", section === "runs")}
          {link("#/bench", "Benchmarks", section === "bench")}
          {link("#/agents", "Agents", section === "agents")}
        </nav>
        <div className="spacer" />
        {health && (
          <span className="pill">
            {String(health.llm_provider)} · {health.langgraph ? "LangGraph" : "fallback runtime"} · {String(health.database)}
          </span>
        )}
      </header>
      <main className="page">{page}</main>
    </>
  );
}
