export type TaskStatus = "pending" | "running" | "completed" | "failed" | "skipped" | "cancelled";

export interface GraphNode {
  id: string;
  name: string;
  type: string;
  status: TaskStatus;
  agent: string | null;
  attempts: number;
  tag: string | null;
  round: number | null;
  origin: string;
  parent: string | null;
  layer: number;
  optional: boolean;
  started_ms: number | null;
  finished_ms: number | null;
}

export interface GraphView {
  nodes: GraphNode[];
  edges: { source: string; target: string }[];
  version?: number;
}

export interface Diagnosis {
  task_id: string;
  failure_type: string;
  transient: boolean;
  strategy: string;
  root_cause_tasks: string[];
  missing_subjects: string[];
  missing_units: string[];
  target_agent: string | null;
  rationale: string;
  confidence: number;
  explanation: string;
}

export interface Impact {
  round: number;
  failed: string[];
  repair_set: string[];
  affected: string[];
  invalidated: string[];
  reused: string[];
  pending_affected: string[];
}

export interface PatchOp {
  op: string;
  task_id: string | null;
  detail: Record<string, unknown>;
}

export interface FailureRecord {
  task_id: string;
  attempt: number;
  agent_id: string | null;
  kind: string;
  error_type: string;
  message: string;
}

export interface ReplanRecord {
  round: number;
  policy: string;
  at_ms: number;
  failures: FailureRecord[];
  diagnoses: Diagnosis[];
  impact: Impact | null;
  ops: PatchOp[];
  reused: string[];
  invalidated: string[];
  retried: string[];
  added: string[];
  invalid_reuse: string[];
  overhead_tokens: number;
  overhead_cost: number;
  overhead_ms: number;
  aborted: boolean;
  abort_reason: string | null;
}

export interface ComponentUsage {
  calls: number;
  tokens_in: number;
  tokens_out: number;
  cost: number;
  duration_ms: number;
}

export interface RunMetrics {
  success: boolean;
  correct: boolean | null;
  latency_ms: number;
  total_tokens: number;
  tokens_in: number;
  tokens_out: number;
  cost: number;
  llm_calls: number;
  agent_invocations: number;
  task_executions: number;
  failed_executions: number;
  retries: number;
  recovery_rounds: number;
  tasks_total: number;
  tasks_completed: number;
  cache_hits: number;
  reused_results: number;
  invalidated_results: number;
  reuse_rate: number | null;
  invalid_reuse: number;
  redundant_executions: number;
  wasted_tokens: number;
  overhead_tokens: number;
  overhead_cost: number;
  overhead_ms: number;
  by_component: Record<string, ComponentUsage>;
  by_agent: Record<string, ComponentUsage>;
}

export interface Analysis {
  domain: string;
  complexity: string;
  strategy: string;
  subjects: { key: string; name: string; repo: string | null }[];
  aspects: string[];
  needs_code: boolean;
  rationale: string;
}

export interface RunSnapshot {
  id: string;
  request: string;
  status: "running" | "completed" | "failed";
  error: string | null;
  config: Record<string, unknown> | null;
  analysis: Analysis | null;
  view: GraphView;
  replans: ReplanRecord[];
  metrics: RunMetrics | null;
  running: Record<string, string>;
  final_output?: string | null;
  live: boolean;
  t_ms?: number;
}

export interface RunEvent {
  seq: number;
  run_id: string;
  type: string;
  t_ms: number;
  task_id: string | null;
  data: Record<string, any>;
}

export interface CheckResult {
  name: string;
  passed: boolean;
  detail: string;
}

export interface TaskDetail {
  id: string;
  name?: string;
  description?: string;
  type?: string;
  status?: string;
  agent?: string | null;
  dependencies?: string[];
  covers?: string[];
  attempts?: number;
  feedback?: string[];
  excluded_agents?: string[];
  tag?: string | null;
  selection?: {
    agent_id: string;
    score: number;
    strategy: string;
    candidates: { agent_id: string; score: number; eligible: boolean; reason: string; breakdown: Record<string, number> }[];
  } | null;
  evaluation?: { passed: boolean; score: number; checks: CheckResult[]; coverage: number } | null;
  failures?: (FailureRecord & { evaluation?: { checks: CheckResult[] } | null; checks?: CheckResult[] })[];
  diagnoses?: Diagnosis[];
  result?: {
    content: string | null;
    agent_id: string;
    tokens_in: number;
    tokens_out: number;
    cost: number;
    duration_ms: number;
    sources: { title: string; url: string | null; kind: string }[];
    input_fingerprint: string;
    output_fingerprint: string;
  } | null;
  preview?: string;
  checks?: CheckResult[];
  live?: boolean;
}

export interface SystemSpec {
  name: string;
  title: string;
  short: string;
  description: string;
}

export interface Scenario {
  name: string;
  title: string;
  description: string;
}

export interface AgentSpec {
  id: string;
  name: string;
  kind: string;
  capabilities: string[];
  model_tier: string;
  description: string;
  prior_reliability: number;
  latency_factor: number;
  system: boolean;
}

export interface Meta {
  systems: SystemSpec[];
  scenarios: Scenario[];
  agents: AgentSpec[];
  examples: string[];
  benchmark_tasks: { id: string; complexity: string; request: string }[];
  live_available: boolean;
  default_time_scale: number;
}

export interface RunListItem {
  id: string;
  request: string;
  system: string;
  mode: string;
  scenario: string;
  status: string;
  error: string | null;
  created_at: number;
  latency_ms: number | null;
  tokens: number | null;
  cost: number | null;
  recovery_rounds: number | null;
}

export interface BenchAgg {
  runs: number;
  success_rate: number | null;
  correct_rate: number | null;
  latency_s: number | null;
  tokens: number | null;
  cost_usd: number | null;
  agent_invocations: number | null;
  redundant_executions: number | null;
  wasted_tokens: number | null;
  overhead_tokens: number | null;
  recovery_success_rate: number | null;
  reuse_rate: number | null;
  invalid_reuse_rate: number | null;
}

export interface BenchSummary {
  systems: Record<string, string>;
  scenarios: Record<string, string>;
  overall: Record<string, BenchAgg>;
  under_failure: Record<string, BenchAgg>;
  per_scenario: Record<string, Record<string, BenchAgg>>;
  n_runs: number;
}

export interface Benchmark {
  id: string;
  status: string;
  summary: BenchSummary | null;
  progress: { done: number; total: number | null } | null;
  created_at: number;
}
