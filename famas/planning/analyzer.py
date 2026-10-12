"""Task Analyzer: estimates complexity, required capabilities and execution strategy."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from famas.agents import prompts
from famas.core.models import Subject, TaskAnalysis
from famas.knowledge.catalog import ASPECTS, DEFAULT_ASPECTS, find_aspects, find_subjects, subject_from_key
from famas.llm.base import MockReply

if TYPE_CHECKING:  # pragma: no cover
    from famas.orchestration.context import RunContext

CODE_RE = re.compile(
    r"\b(code|coding|implementation[s]?|feature matrix|matrix|quantitative|benchmark|compute|computed|script)\b",
    re.I,
)


class SubjectRef(BaseModel):
    name: str
    repo: str | None = None


class AnalysisSchema(BaseModel):
    domain: Literal["repo_research", "general"] = "general"
    complexity: Literal["simple", "medium", "complex"] = "medium"
    strategy: Literal["single_agent", "multi_agent"] = "multi_agent"
    subjects: list[SubjectRef] = Field(default_factory=list)
    aspects: list[str] = Field(default_factory=list)
    needs_code: bool = False
    deliverable: str = "report"
    rationale: str = ""


def heuristic_analysis(request: str) -> TaskAnalysis:
    subjects = find_subjects(request)
    aspects = find_aspects(request)
    needs_code = bool(CODE_RE.search(request)) and bool(subjects)
    words = len(request.split())
    if subjects:
        aspects = aspects or list(DEFAULT_ASPECTS)
        units = len(subjects) * len(aspects)
        if len(subjects) == 1 and len(aspects) <= 1 and not needs_code:
            complexity = "simple"
        elif len(subjects) >= 3 or units >= 6 or needs_code:
            complexity = "complex"
        else:
            complexity = "medium"
        domain = "repo_research"
        caps = ["research", "github", "analysis", "writing"] + (["coding"] if needs_code else [])
        est = 1 if complexity == "simple" else len(subjects) + len(aspects) + int(needs_code) + 1
    else:
        complexity = "simple" if words < 80 else "medium"
        domain = "general"
        caps = ["general"] if complexity == "simple" else ["general", "writing"]
        est = 1 if complexity == "simple" else 2
    strategy = "single_agent" if complexity == "simple" else "multi_agent"
    rationale = (
        f"{len(subjects)} subject(s), {len(aspects)} aspect(s), code={'yes' if needs_code else 'no'} -> {complexity}; "
        f"{'one agent suffices' if strategy == 'single_agent' else 'decompose across specialised agents'}."
    )
    return TaskAnalysis(
        request=request,
        domain=domain,
        complexity=complexity,
        strategy=strategy,
        subjects=subjects,
        aspects=aspects,
        required_capabilities=caps,
        needs_code=needs_code,
        deliverable="report" if domain == "repo_research" else "answer",
        rationale=rationale,
        estimated_tasks=est,
    )


def _merge_llm(heur: TaskAnalysis, llm: AnalysisSchema) -> TaskAnalysis:
    """Trust the catalog for known subjects/aspects; let the model add what heuristics missed."""
    subjects = list(heur.subjects)
    known = {s.key for s in subjects}
    for ref in llm.subjects:
        found = find_subjects(f"{ref.name} {ref.repo or ''}")
        if found:
            s = found[0]
        else:
            key = re.sub(r"[^a-z0-9]+", "_", ref.name.lower()).strip("_")
            s = Subject(
                key=key,
                name=ref.name,
                repo=ref.repo,
                url=f"https://github.com/{ref.repo}" if ref.repo else None,
            )
        if s.key not in known:
            known.add(s.key)
            subjects.append(s)
    aspects = list(heur.aspects)
    for a in llm.aspects:
        key = re.sub(r"[^a-z0-9]+", "_", a.lower()).strip("_")
        key = next((k for k in ASPECTS if k in key or key in k), key)
        if key and key not in aspects:
            aspects.append(key)
    out = heur.model_copy(deep=True)
    out.subjects = subjects
    out.aspects = aspects if subjects else heur.aspects
    if subjects and not heur.subjects:
        out.domain = "repo_research"
        out.aspects = out.aspects or list(DEFAULT_ASPECTS)
    out.needs_code = heur.needs_code or (llm.needs_code and bool(subjects))
    out.complexity = llm.complexity if not subjects else heur.complexity
    out.strategy = "single_agent" if out.complexity == "simple" else "multi_agent"
    out.rationale = llm.rationale or heur.rationale
    return out


class TaskAnalyzer:
    async def analyze(self, request: str, ctx: RunContext) -> TaskAnalysis:
        heur = heuristic_analysis(request)

        def _mock() -> MockReply:
            return MockReply(
                parsed=AnalysisSchema(
                    domain=heur.domain,
                    complexity=heur.complexity,
                    strategy=heur.strategy,
                    subjects=[SubjectRef(name=s.name, repo=s.repo) for s in heur.subjects],
                    aspects=heur.aspects,
                    needs_code=heur.needs_code,
                    deliverable=heur.deliverable,
                    rationale=heur.rationale,
                )
            )

        prompt = (
            f"User request:\n{request}\n\nKnown aspect keys: {', '.join(ASPECTS)}.\n"
            "Return the domain (repo_research if the request is about software projects/repositories), complexity, "
            "strategy, subjects (with owner/repo if known), aspects (use the known keys where possible), whether "
            "computation/code is needed, and a one-sentence rationale."
        )
        try:
            resp = await ctx.llm_call(
                component="analyzer",
                system=prompts.ANALYZER_SYSTEM,
                prompt=prompt,
                schema=AnalysisSchema,
                tier="small",
                mock=_mock,
            )
            if ctx.llm.is_mock:
                return heur
            return _merge_llm(heur, resp.parsed)
        except Exception:  # analysis must never block a run: fall back to heuristics
            return heur


__all__ = ["TaskAnalyzer", "heuristic_analysis", "subject_from_key"]
