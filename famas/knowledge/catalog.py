"""Catalog of known subjects (frameworks/repositories) and analysis aspects.

Used by the task analyzer to resolve names in a request to GitHub
repositories and canonical aspect keys. Unknown subjects are still supported:
they are researched without a repository hint.
"""

from __future__ import annotations

import re

from famas.core.models import Subject

SUBJECTS: dict[str, dict] = {
    "langgraph": {
        "name": "LangGraph",
        "repo": "langchain-ai/langgraph",
        "aliases": ["langgraph", "lang graph"],
    },
    "crewai": {
        "name": "CrewAI",
        "repo": "crewAIInc/crewAI",
        "aliases": ["crewai", "crew ai"],
    },
    "autogen": {
        "name": "AutoGen",
        "repo": "microsoft/autogen",
        "aliases": ["autogen", "auto gen", "ag2"],
    },
    "agent_framework": {
        "name": "Microsoft Agent Framework",
        "repo": "microsoft/agent-framework",
        "aliases": ["microsoft agent framework", "agent framework", "ms agent framework", "maf"],
    },
    "metagpt": {
        "name": "MetaGPT",
        "repo": "FoundationAgents/MetaGPT",
        "aliases": ["metagpt", "meta gpt"],
    },
    "openai_agents": {
        "name": "OpenAI Agents SDK",
        "repo": "openai/openai-agents-python",
        "aliases": ["openai agents sdk", "openai agents", "agents sdk"],
    },
}

ASPECTS: dict[str, dict] = {
    "architecture": {
        "title": "System Architecture",
        "keywords": ["architecture", "architectures", "design", "structure", "components"],
    },
    "communication": {
        "title": "Agent Communication",
        "keywords": ["communication", "messaging", "message passing", "message", "collaboration"],
    },
    "scheduling": {
        "title": "Scheduling & Orchestration",
        "keywords": ["scheduling", "orchestration", "control flow", "execution model", "workflow"],
    },
    "failure_recovery": {
        "title": "Failure Recovery",
        "keywords": ["failure", "recovery", "fault", "error handling", "reliability", "resilience", "retry"],
    },
    "state_management": {
        "title": "State & Memory",
        "keywords": ["state management", "state", "memory", "persistence", "checkpoint"],
    },
    "tooling": {
        "title": "Tools & Integrations",
        "keywords": ["tooling", "tools", "tool use", "integrations", "ecosystem"],
    },
    "observability": {
        "title": "Observability",
        "keywords": ["observability", "tracing", "monitoring", "telemetry", "debugging"],
    },
}

DEFAULT_ASPECTS = ["architecture", "scheduling"]

_REPO_RE = re.compile(r"(?:github\.com/)?\b([A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*)\b")


def aspect_title(key: str) -> str:
    return ASPECTS.get(key, {}).get("title", key.replace("_", " ").title())


def subject_from_key(key: str) -> Subject:
    info = SUBJECTS.get(key)
    if info:
        return Subject(
            key=key, name=info["name"], repo=info["repo"], url=f"https://github.com/{info['repo']}"
        )
    return Subject(key=key, name=key.replace("_", " ").title())


def find_subjects(text: str) -> list[Subject]:
    """Find known frameworks (by alias) and explicit owner/repo references, in order of appearance."""
    lowered = text.lower()
    hits: list[tuple[int, Subject]] = []
    seen: set[str] = set()
    # longest aliases first so "microsoft agent framework" wins over "agent framework"
    alias_pairs = sorted(
        ((alias, key) for key, info in SUBJECTS.items() for alias in info["aliases"]),
        key=lambda p: -len(p[0]),
    )
    consumed: list[tuple[int, int]] = []
    for alias, key in alias_pairs:
        for m in re.finditer(r"(?<![\w-])" + re.escape(alias) + r"(?![\w-])", lowered):
            span = (m.start(), m.end())
            if any(s <= span[0] < e or s < span[1] <= e for s, e in consumed):
                continue
            consumed.append(span)
            if key not in seen:
                seen.add(key)
                hits.append((m.start(), subject_from_key(key)))
    for m in _REPO_RE.finditer(text):
        repo = m.group(1).rstrip(".")
        if repo.count("/") != 1 or "." in repo.split("/")[0]:
            continue
        key = repo.split("/")[1].lower().replace("-", "_")
        known = next((k for k, v in SUBJECTS.items() if v["repo"].lower() == repo.lower()), None)
        if known:
            key = known
        if key in seen:
            continue
        seen.add(key)
        subj = (
            subject_from_key(key)
            if known
            else Subject(key=key, name=repo.split("/")[1], repo=repo, url=f"https://github.com/{repo}")
        )
        hits.append((m.start(), subj))
    hits.sort(key=lambda p: p[0])
    return [s for _, s in hits]


def find_aspects(text: str) -> list[str]:
    lowered = text.lower()
    found: list[tuple[int, str]] = []
    for key, info in ASPECTS.items():
        positions = []
        for kw in info["keywords"]:
            m = re.search(r"\b" + re.escape(kw) + r"\b", lowered)
            if m:
                positions.append(m.start())
        if positions:
            found.append((min(positions), key))
    found.sort()
    return [k for _, k in found]
