"""Writing agent: synthesises validated upstream results into the final report."""

from __future__ import annotations

import re

from famas.agents import prompts
from famas.agents.base import AgentOutput, BaseAgent, ExecContext
from famas.core.models import Issue, IssueKind, Source, Task, TaskResult
from famas.knowledge.catalog import aspect_title, subject_from_key
from famas.llm.base import MockReply

MOCK_NOTE = (
    "_Generated in offline mock mode from FA-MAS's built-in knowledge fixtures (not live repository data); "
    "run with `--mode live` for current findings._"
)


def split_sections(markdown: str) -> dict[str, str]:
    """Map lower-cased level-2 headings to their section text (heading included)."""
    out: dict[str, str] = {}
    parts = re.split(r"^##\s+", markdown, flags=re.M)
    for part in parts[1:]:
        heading, _, body = part.partition("\n")
        out[heading.strip().lower()] = f"## {heading.strip()}\n{body.strip()}"
    return out


def references_section(sources: list[Source]) -> str:
    lines = ["## References", ""]
    n = 0
    for s in sources:
        if s.kind == "derived":
            continue
        n += 1
        lines.append(f"{n}. {s.title}" + (f" — <{s.url}>" if s.url else ""))
    if n == 0:
        lines.append("_No external sources were recorded._")
    return "\n".join(lines)


def aspect_section(aspect: str, data: dict) -> str:
    lines = [f"## {aspect_title(aspect)}", ""]
    rows = data.get("rows") or []
    if rows:
        lines.append("| System | Approach | Strengths |")
        lines.append("|---|---|---|")
        for r in rows:
            lines.append(f"| {r['subject']} | {r['approach']} | {' '.join(r.get('strengths', [])) or '-'} |")
        lines.append("")
    for o in data.get("observations") or []:
        lines.append(f"- {o}")
    return "\n".join(lines).strip()


class WritingAgent(BaseAgent):
    async def run(self, task: Task, inputs: dict[str, TaskResult], ectx: ExecContext) -> AgentOutput:
        aspects: list[str] = list(task.inputs.get("aspects") or [])
        subjects: list[str] = list(task.inputs.get("subjects") or [])
        if not aspects:
            from famas.agents.general import run_generic

            return await run_generic(self, task, inputs, ectx)
        names = [subject_from_key(s).name for s in subjects]
        by_aspect: dict[str, dict] = {}
        extra: list[TaskResult] = []
        for res in inputs.values():
            a = res.data.get("aspect")
            if a in aspects:
                by_aspect[a] = res.data
            elif res.data.get("per_aspect"):
                for pa, d in res.data["per_aspect"].items():
                    if pa in aspects:
                        by_aspect[pa] = d
            else:
                extra.append(res)
        issues = [
            Issue(
                kind=IssueKind.MISSING_INFORMATION,
                aspect=a,
                message=f"No validated analysis for {aspect_title(a)} was provided.",
            )
            for a in aspects
            if a not in by_aspect and a not in task.inputs.get("degraded_aspects", [])
        ]
        sources = self.collect_sources(inputs)
        matrix_md = next((r.content for r in extra if r.data.get("matrix")), "")
        title = task.inputs.get("title") or f"Technical comparison: {', '.join(names)}"

        def _mock() -> MockReply:
            body = [f"# {title}", "", MOCK_NOTE, ""]
            first_obs = []
            for a in aspects:
                if a in by_aspect:
                    obs = by_aspect[a].get("observations") or []
                    if obs:
                        first_obs.append(f"**{aspect_title(a)}:** {obs[0]}")
            body.append("## Executive Summary")
            body.append("")
            body.append(
                f"This report compares {', '.join(names)} across {len(aspects)} aspect(s): "
                f"{', '.join(aspect_title(a) for a in aspects)}. Findings were gathered per system by research "
                "agents, validated, and compared aspect-by-aspect by analysis agents."
            )
            body.append("")
            body.extend(f"- {o}" for o in first_obs)
            body.append("")
            for a in aspects:
                if a in by_aspect:
                    body.append(aspect_section(a, by_aspect[a]))
                else:
                    body.append(f"## {aspect_title(a)}\n\n_Analysis unavailable (degraded result)._")
                body.append("")
            if matrix_md:
                body.append(matrix_md)
                body.append("")
            return MockReply(text="\n".join(body).strip())

        inputs_text = "\n\n".join(r.content[:3000] for r in inputs.values())
        prompt = prompts.WRITING_PROMPT.format(
            request=ectx.run.request,
            aspect_headings="\n".join(f"## {aspect_title(a)}" for a in aspects),
            inputs=inputs_text,
            feedback=self.feedback_block(task),
        )
        resp = await ectx.llm(system=prompts.WRITING_SYSTEM, prompt=prompt, mock=_mock)
        body = resp.text.strip()
        body = re.sub(r"\n## References.*", "", body, flags=re.S | re.I).strip()
        content = body + "\n\n" + references_section(sources)

        sections = split_sections(content)
        units: dict[str, str] = {}
        if sections.get("executive summary"):
            units["report:summary"] = sections["executive summary"]
        for a in aspects:
            text = sections.get(aspect_title(a).lower())
            if text:
                units[f"report:{a}"] = text
        units["report:references"] = sections.get("references", "")
        return AgentOutput(
            content=content,
            data={
                "title": title,
                "aspects": aspects,
                "subjects": subjects,
                "units": units,
                "format": "markdown",
            },
            sources=sources,
            issues=issues,
        )
