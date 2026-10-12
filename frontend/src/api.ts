import type { Benchmark, Meta, RunListItem, RunSnapshot, TaskDetail } from "./types";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* keep status text */
    }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

export interface StartRunBody {
  request: string;
  system: string;
  scenario: string;
  mode: "mock" | "live";
  seed: number;
  max_concurrency: number;
  simulated: boolean;
  time_scale?: number;
  detection_probability: number;
}

export const api = {
  meta: () => fetch("/api/meta").then(json<Meta>),
  health: () => fetch("/api/health").then(json<Record<string, unknown>>),
  runs: () => fetch("/api/runs").then(json<RunListItem[]>),
  run: (id: string) => fetch(`/api/runs/${id}`).then(json<RunSnapshot>),
  task: (id: string, taskId: string) => fetch(`/api/runs/${id}/tasks/${taskId}`).then(json<TaskDetail>),
  startRun: (body: StartRunBody) =>
    fetch("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(
      json<{ run_id: string }>,
    ),
  deleteRun: (id: string) => fetch(`/api/runs/${id}`, { method: "DELETE" }),
  benchmarks: () => fetch("/api/benchmarks").then(json<Benchmark[]>),
  benchmark: (id: string) => fetch(`/api/benchmarks/${id}`).then(json<Benchmark>),
  startBenchmark: (body: { tasks?: string[]; scenarios?: string[]; systems?: string[]; seeds?: number[] }) =>
    fetch("/api/benchmarks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(
      json<{ benchmark_id: string }>,
    ),
  agents: () => fetch("/api/agents").then(json<{ agents: Meta["agents"]; stats: Record<string, unknown>[] }>),
};

export function eventsUrl(runId: string, after = 0): string {
  return `/api/runs/${runId}/events?after=${after}`;
}
