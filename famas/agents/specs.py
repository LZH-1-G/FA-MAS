"""Default agent roster.

Several agents share capabilities but differ in model tier, cost and latency,
which is what gives the Agent Selector real decisions to make. The roster is
plain data: add an ``AgentSpec`` (and, if needed, an implementation class in
``registry.AGENT_CLASSES``) to extend the system.
"""

from __future__ import annotations

from famas.core.models import AgentSpec, TaskType

DEFAULT_AGENTS: list[AgentSpec] = [
    AgentSpec(
        id="research-fast",
        name="Research Agent (fast)",
        kind=TaskType.RESEARCH,
        capabilities=["research", "github", "web_research"],
        model_tier="small",
        latency_factor=0.6,
        prior_reliability=0.85,
        description="Quick repository and documentation research on a small model.",
    ),
    AgentSpec(
        id="research-deep",
        name="Research Agent (deep)",
        kind=TaskType.RESEARCH,
        capabilities=["research", "github", "web_research", "deep_research"],
        model_tier="large",
        latency_factor=1.0,
        prior_reliability=0.93,
        description="Thorough research on a large model; better for multi-aspect subjects.",
    ),
    AgentSpec(
        id="analysis-standard",
        name="Analysis Agent (standard)",
        kind=TaskType.ANALYSIS,
        capabilities=["analysis", "comparison"],
        model_tier="small",
        latency_factor=0.6,
        prior_reliability=0.86,
        description="Comparative analysis of collected findings on a small model.",
    ),
    AgentSpec(
        id="analysis-expert",
        name="Analysis Agent (expert)",
        kind=TaskType.ANALYSIS,
        capabilities=["analysis", "comparison", "synthesis"],
        model_tier="large",
        latency_factor=1.0,
        prior_reliability=0.94,
        description="Deeper comparative analysis and synthesis on a large model.",
    ),
    AgentSpec(
        id="coder",
        name="Coding Agent",
        kind=TaskType.CODING,
        capabilities=["coding", "code_execution", "data_processing"],
        model_tier="large",
        latency_factor=1.0,
        prior_reliability=0.9,
        description="Writes and executes Python in a sandbox for quantitative processing.",
    ),
    AgentSpec(
        id="writer",
        name="Writing Agent",
        kind=TaskType.WRITING,
        capabilities=["writing", "reporting", "summarization"],
        model_tier="large",
        latency_factor=1.0,
        prior_reliability=0.93,
        description="Synthesises validated results into structured reports.",
    ),
    AgentSpec(
        id="generalist",
        name="Generalist Agent",
        kind=TaskType.GENERAL,
        capabilities=[
            "general",
            "research",
            "github",
            "web_research",
            "analysis",
            "comparison",
            "synthesis",
            "writing",
            "reporting",
            "summarization",
            "coding",
            "data_processing",
        ],
        model_tier="large",
        latency_factor=1.1,
        prior_reliability=0.88,
        description="Single general-purpose agent (fallback, and the single-agent baseline).",
    ),
    AgentSpec(
        id="aggregator",
        name="Aggregator",
        kind=TaskType.AGGREGATE,
        capabilities=["aggregate"],
        model_tier="none",
        latency_factor=0.0,
        prior_reliability=1.0,
        system=True,
        description="Deterministic merge of decomposed subtask outputs (no LLM).",
    ),
]
