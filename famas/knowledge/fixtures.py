"""Offline knowledge fixtures for deterministic (mock) execution.

These hand-written summaries let the whole pipeline - research, analysis,
code execution, report writing, failure recovery - run without network access
or API keys, which is what tests and benchmarks need.

They are *fixtures*, not a source of truth: framework APIs evolve quickly and
these notes were written from public documentation as of 2025-2026. Live mode
(``mode="live"``) fetches current repository data from the GitHub API instead.
"""

from __future__ import annotations

KB: dict[str, dict] = {
    "langgraph": {
        "language": "Python (JS/TS port available)",
        "docs": "https://github.com/langchain-ai/langgraph",
        "aspects": {
            "architecture": {
                "summary": "Low-level orchestration framework that models agent workflows as graphs: a StateGraph of nodes and edges over a shared, typed state, compiled into a Pregel-inspired runtime.",
                "points": [
                    "Nodes are plain functions or agents; edges (including conditional edges) define control flow.",
                    "State schemas use reducer functions to merge concurrent updates.",
                    "Subgraphs allow hierarchical composition of multi-agent systems.",
                ],
            },
            "communication": {
                "summary": "Agents communicate through the shared graph state rather than direct messages; each node returns a partial state update that reducers merge.",
                "points": [
                    "Message lists are accumulated with an add_messages-style reducer.",
                    "The Send API supports dynamic fan-out (map-reduce) to parallel workers.",
                    "Command objects combine a state update with routing, enabling agent handoffs.",
                ],
            },
            "scheduling": {
                "summary": "Execution proceeds in discrete super-steps (bulk-synchronous parallel): nodes triggered in the same step run in parallel and their writes become visible in the next step.",
                "points": [
                    "Parallel branches arise from multiple outgoing edges or Send fan-out.",
                    "A recursion limit bounds the number of super-steps per run.",
                    "Interrupts pause a run for human-in-the-loop review and resume later.",
                ],
            },
            "failure_recovery": {
                "summary": "Durable execution through checkpointers that persist state after every super-step, so a failed or interrupted run can resume from its last checkpoint.",
                "points": [
                    "Per-node retry policies handle transient errors.",
                    "Writes from nodes that succeeded in a failed super-step are kept, so they need not re-run on resume.",
                    "Checkpoint history supports replay and time-travel debugging from earlier states.",
                ],
            },
            "state_management": {
                "summary": "State is a typed schema shared by all nodes; checkpointers (in-memory, SQLite, Postgres) persist it per thread.",
                "points": [
                    "Threads identify independent conversations or runs.",
                    "A separate store offers long-term, cross-thread memory.",
                    "State can be inspected and edited between steps.",
                ],
            },
            "tooling": {
                "summary": "Integrates with LangChain tools and models; ships prebuilt components such as a tool-calling ReAct agent and a tool-executing node.",
                "points": [
                    "Any LangChain-compatible chat model can drive nodes.",
                    "Prebuilt agents reduce boilerplate for common patterns.",
                    "A deployment server exposes graphs as long-running, stateful APIs.",
                ],
            },
            "observability": {
                "summary": "Streaming of state values, updates and tokens, with tracing through LangSmith.",
                "points": [
                    "Multiple stream modes (values, updates, messages, custom events).",
                    "LangSmith traces visualise node-level execution.",
                    "Checkpoint history doubles as an execution log.",
                ],
            },
        },
    },
    "crewai": {
        "language": "Python",
        "docs": "https://docs.crewai.com",
        "aspects": {
            "architecture": {
                "summary": "Role-based framework: Agents (role, goal, backstory) execute Tasks (description, expected output) inside a Crew; Flows add event-driven workflow orchestration around crews.",
                "points": [
                    "Agents are configured declaratively, often from YAML.",
                    "Tasks declare expected outputs and can produce structured (Pydantic) results.",
                    "Flows combine crews and plain Python steps into larger applications.",
                ],
            },
            "communication": {
                "summary": "Agents communicate mainly through task outputs passed as context to later tasks, plus optional delegation and question-asking between crew members.",
                "points": [
                    "Task context links feed earlier outputs into later tasks.",
                    "Delegation tools let an agent hand work to a coworker.",
                    "Flows share a state object between steps.",
                ],
            },
            "scheduling": {
                "summary": "Processes define execution order: sequential, or hierarchical where a manager agent plans, delegates and validates work; Flows route with start/listen/router decorators.",
                "points": [
                    "Sequential process executes tasks in declared order.",
                    "Hierarchical process uses a manager LLM to allocate tasks to agents.",
                    "Tasks can be marked for asynchronous execution.",
                ],
            },
            "failure_recovery": {
                "summary": "Recovery is mostly at the agent/task level: iteration and retry limits, plus task guardrails that validate outputs and trigger re-attempts.",
                "points": [
                    "Agents have max iteration and max retry settings.",
                    "Task guardrails check outputs and retry on failure.",
                    "Flow state persistence allows resuming long-running flows.",
                ],
            },
            "state_management": {
                "summary": "Provides short-term, long-term and entity memory for crews, and structured or unstructured state for Flows.",
                "points": [
                    "Memory can be enabled per crew.",
                    "Flow state can be typed with Pydantic models.",
                    "Knowledge sources attach documents to agents.",
                ],
            },
            "tooling": {
                "summary": "A companion tools library plus custom tools via a base class or decorator; supports MCP servers as tool sources.",
                "points": [
                    "Ready-made tools for search, scraping and file access.",
                    "Custom tools are simple Python callables.",
                    "Tools can be scoped per agent or per task.",
                ],
            },
            "observability": {
                "summary": "Event listeners expose crew and task lifecycle events; integrates with external tracing platforms.",
                "points": [
                    "Verbose mode logs agent reasoning steps.",
                    "Event bus supports custom listeners.",
                    "Third-party observability integrations are documented.",
                ],
            },
        },
    },
    "autogen": {
        "language": "Python (.NET available)",
        "docs": "https://microsoft.github.io/autogen/",
        "aspects": {
            "architecture": {
                "summary": "Layered design since v0.4: Core (event-driven, actor-model agent runtime), AgentChat (high-level agents and teams) and Extensions (model clients, tools, code executors).",
                "points": [
                    "Agents are actors that react to messages.",
                    "AgentChat offers preset agents and team patterns.",
                    "Extensions package model clients and executors.",
                ],
            },
            "communication": {
                "summary": "Asynchronous message passing: direct messages and topic-based publish/subscribe in Core; teams share a group conversation in AgentChat.",
                "points": [
                    "Typed messages are routed by the runtime.",
                    "Topics and subscriptions decouple senders from receivers.",
                    "Group chats broadcast messages to all participants.",
                ],
            },
            "scheduling": {
                "summary": "Team presets decide who acts next: round-robin, model-based speaker selection, swarm-style handoffs, and a Magentic-One orchestrator; termination conditions end a run.",
                "points": [
                    "RoundRobinGroupChat cycles through agents.",
                    "SelectorGroupChat lets a model pick the next speaker.",
                    "Termination conditions bound conversation length.",
                ],
            },
            "failure_recovery": {
                "summary": "Relies on termination conditions, state save/load and orchestrator-level re-planning (the Magentic-One orchestrator revises its plan when progress stalls).",
                "points": [
                    "Agent and team state can be saved and restored.",
                    "Termination conditions stop runaway loops.",
                    "The Magentic-One orchestrator tracks progress and re-plans when stuck.",
                ],
            },
            "state_management": {
                "summary": "Agents and teams expose save/load of state; a memory protocol supports retrieval-augmented memories.",
                "points": [
                    "State is serialisable for persistence.",
                    "Memory components can be attached to agents.",
                    "Model contexts bound conversation history.",
                ],
            },
            "tooling": {
                "summary": "Function tools, code executors (local or containerised) and model clients for several providers.",
                "points": [
                    "Python functions become tools automatically.",
                    "Code executors run generated code.",
                    "A low-code studio supports prototyping.",
                ],
            },
            "observability": {
                "summary": "Runtime emits OpenTelemetry-compatible traces; message streams can be consumed in real time.",
                "points": [
                    "OpenTelemetry tracing support.",
                    "Streaming of agent events.",
                    "Console UIs for debugging teams.",
                ],
            },
        },
        "note": "Microsoft has positioned Agent Framework as the successor for new projects.",
    },
    "agent_framework": {
        "language": "Python and .NET",
        "docs": "https://github.com/microsoft/agent-framework",
        "aspects": {
            "architecture": {
                "summary": "Open-source SDK for Python and .NET that unifies ideas from Semantic Kernel and AutoGen: chat agents over pluggable model clients, plus graph-based workflows.",
                "points": [
                    "Agents wrap model clients with instructions, tools and threads.",
                    "Workflows connect executors with typed edges.",
                    "Middleware hooks intercept agent and function calls.",
                ],
            },
            "communication": {
                "summary": "Workflow executors exchange typed messages along edges; agents can participate as executors and communicate through workflow events.",
                "points": [
                    "Type-based routing delivers messages to compatible executors.",
                    "Fan-out and fan-in edges support parallel branches.",
                    "Agents can be composed as tools for other agents.",
                ],
            },
            "scheduling": {
                "summary": "Graph-based workflow execution with conditional routing and built-in orchestration patterns such as sequential, concurrent, handoff, group chat and Magentic-style orchestration.",
                "points": [
                    "Conditional edges route on message content.",
                    "Concurrent patterns run agents in parallel and aggregate results.",
                    "Workflows can request human input mid-run.",
                ],
            },
            "failure_recovery": {
                "summary": "Checkpointing persists workflow state so long-running processes can pause and resume; human-in-the-loop requests support manual intervention.",
                "points": [
                    "Checkpoints are captured during workflow execution.",
                    "Runs can be resumed from a stored checkpoint.",
                    "Request/response steps enable approval gates.",
                ],
            },
            "state_management": {
                "summary": "Agent threads hold conversation state; workflow state and checkpoints are stored through pluggable storage.",
                "points": [
                    "Threads can be serialised.",
                    "Shared state is available to workflow executors.",
                    "Checkpoint storage is pluggable.",
                ],
            },
            "tooling": {
                "summary": "Function tools, hosted tools, MCP and OpenAPI integrations, with multiple model providers.",
                "points": [
                    "MCP servers can supply tools.",
                    "OpenAPI specs can be imported as tools.",
                    "Supports several model providers.",
                ],
            },
            "observability": {
                "summary": "Built on OpenTelemetry for tracing of agents and workflows; a developer UI supports local debugging.",
                "points": [
                    "OpenTelemetry spans for agent runs.",
                    "Workflow events are observable.",
                    "Developer tooling for local inspection.",
                ],
            },
        },
    },
    "metagpt": {
        "language": "Python",
        "docs": "https://github.com/FoundationAgents/MetaGPT",
        "aspects": {
            "architecture": {
                "summary": "Simulates a software company: role-specialised agents (product manager, architect, engineer, QA) follow standard operating procedures (SOPs) to turn a requirement into artifacts.",
                "points": [
                    "Roles encapsulate actions and responsibilities.",
                    "SOPs encode the hand-off order between roles.",
                    "Produces documents and code as intermediate artifacts.",
                ],
            },
            "communication": {
                "summary": "A shared environment with a message pool: roles publish structured messages and subscribe to the message types they care about.",
                "points": [
                    "Publish-subscribe reduces irrelevant context.",
                    "Messages carry structured documents rather than free chat.",
                    "The environment mediates all exchanges.",
                ],
            },
            "scheduling": {
                "summary": "SOP-driven, largely sequential hand-offs; each role runs an observe-think-act loop when relevant messages arrive.",
                "points": [
                    "Roles react to subscribed messages.",
                    "Execution rounds advance the simulation.",
                    "Order is fixed by the SOP.",
                ],
            },
            "failure_recovery": {
                "summary": "Recovery is centred on executable feedback: generated code is run and debugged iteratively; workflow-level re-planning is limited.",
                "points": [
                    "Engineer role executes and fixes code.",
                    "Structured outputs reduce hallucinated hand-offs.",
                    "Limited support for resuming partial workflows.",
                ],
            },
            "state_management": {
                "summary": "Roles keep memories of observed messages; project artifacts are persisted to a workspace.",
                "points": [
                    "Per-role memory of messages.",
                    "Workspace stores generated files.",
                    "Serialisation supports pausing projects.",
                ],
            },
            "tooling": {
                "summary": "Actions wrap LLM prompts and tools; integrations for search, browsing and code execution.",
                "points": [
                    "Custom actions extend roles.",
                    "Tool registry for agent use.",
                    "Data-interpreter style agents run code.",
                ],
            },
            "observability": {
                "summary": "Logging of role actions and token cost tracking.",
                "points": [
                    "Cost manager tracks spending.",
                    "Action logs per role.",
                    "Artifacts provide an audit trail.",
                ],
            },
        },
    },
    "openai_agents": {
        "language": "Python (JS/TS available)",
        "docs": "https://openai.github.io/openai-agents-python/",
        "aspects": {
            "architecture": {
                "summary": "Lightweight SDK with a small set of primitives: agents (instructions + tools), handoffs, guardrails and sessions, executed by a Runner loop.",
                "points": [
                    "Few abstractions, Python-first design.",
                    "Agents can be used as tools by other agents.",
                    "Sessions keep conversation history.",
                ],
            },
            "communication": {
                "summary": "Agents coordinate through handoffs (transferring control of the conversation) or by calling other agents as tools.",
                "points": [
                    "Handoffs are exposed to the model as tools.",
                    "Manager-style agents orchestrate specialists.",
                    "Context objects pass application state.",
                ],
            },
            "scheduling": {
                "summary": "A Runner loops model calls and tool executions until a final output or handoff resolves; a max-turns limit bounds the loop.",
                "points": [
                    "Loop terminates on final output.",
                    "Max turns guard against runaway loops.",
                    "Parallel tool calls are supported by the model API.",
                ],
            },
            "failure_recovery": {
                "summary": "Guardrails validate inputs and outputs and can halt a run; exceptions such as exceeded turn limits are surfaced to the application for handling.",
                "points": [
                    "Input/output guardrails act as tripwires.",
                    "Typed exceptions for limits and guardrail trips.",
                    "Retries are left to the application.",
                ],
            },
            "state_management": {
                "summary": "Sessions persist conversation history across runs; a context object carries dependencies.",
                "points": [
                    "Pluggable session storage.",
                    "Typed run context.",
                    "Result objects expose new items.",
                ],
            },
            "tooling": {
                "summary": "Function tools from Python callables, hosted tools, and MCP server integration.",
                "points": [
                    "Automatic schema generation for tools.",
                    "Hosted tools such as web search.",
                    "MCP support.",
                ],
            },
            "observability": {
                "summary": "Built-in tracing records agent runs, tool calls and handoffs.",
                "points": ["Tracing is on by default.", "Custom trace processors.", "Spans for each step."],
            },
        },
    },
}

GENERIC_ASPECT = {
    "summary": "No offline fixture is available for this subject; live mode researches it from its repository and documentation.",
    "points": [
        "Fixture placeholder: run in live mode for real findings.",
        "Inspect the repository README and docs for details.",
        "Treat this entry as unverified.",
    ],
}

# Feature vocabulary used by the coding agent's feature-matrix computation.
FEATURE_KEYWORDS: dict[str, list[str]] = {
    "parallel_execution": ["parallel", "concurrent", "fan-out"],
    "checkpointing": ["checkpoint", "resume", "persist"],
    "human_in_the_loop": ["human", "approval", "interrupt"],
    "retries": ["retry", "retries", "re-attempt"],
    "dynamic_replanning": ["re-plan", "replan", "revises its plan"],
    "pub_sub_messaging": ["publish", "subscribe", "topic"],
    "hierarchical_orchestration": ["manager", "orchestrator", "hierarchical"],
    "handoffs": ["handoff", "hand-off", "hand work"],
    "guardrails": ["guardrail", "tripwire", "validate"],
    "tracing": ["trace", "tracing", "opentelemetry", "langsmith"],
}


def fixture_for(subject_key: str, aspect: str) -> dict:
    entry = KB.get(subject_key, {}).get("aspects", {}).get(aspect)
    return entry or GENERIC_ASPECT


def readme_excerpt(subject_key: str) -> str:
    """A synthetic stand-in for README text, sized like a real README excerpt (for token modelling)."""
    entry = KB.get(subject_key)
    if not entry:
        return ""
    parts = [f"{a}: {v['summary']} " + " ".join(v["points"]) for a, v in entry["aspects"].items()]
    return ("\n".join(parts) + "\n") * 2
