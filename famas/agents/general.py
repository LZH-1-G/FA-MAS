"""Generalist agent (single-agent baseline / simple requests), generic task path and aggregator."""

from __future__ import annotations

import re

from famas.agents import prompts
from famas.agents.analysis import _feature_observations
from famas.agents.base import AgentOutput, BaseAgent, ExecContext
from famas.agents.research import mock_research, unit_text
from famas.agents.writing import MOCK_NOTE, aspect_section, references_section, split_sections
from famas.core.models import Source, Task, TaskResult, TaskType
from famas.knowledge.catalog import aspect_title, subject_from_key
from famas.knowledge.fixtures import FEATURE_KEYWORDS, KB, readme_excerpt
from famas.llm.base import MockReply


def _extractive_summary(text: str, n: int = 3) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    picked = [s for s in sentences if len(s.split()) > 3][:n]
    return " ".join(picked) if picked else text[:400]


async def run_generic(
    agent: BaseAgent, task: Task, inputs: dict[str, TaskResult], ectx: ExecContext
) -> AgentOutput:
    """Domain-independent execution path used for LLM-planned or free-form tasks."""
    upstream = BaseAgent.upstream_block(inputs)

    def _mock() -> MockReply:
        if inputs:
            gist = "\n".join(
                f"- From `{tid}`: {_extractive_summary(r.content, 1)}" for tid, r in inputs.items()
            )
            body = f"## {task.name}\n\n{task.description}\n\nSynthesis of upstream results:\n{gist}"
        else:
            body = f"## {task.name}\n\n{_extractive_summary(ectx.run.request, 4)}"
        return MockReply(text=body)

    prompt = prompts.GENERAL_PROMPT.format(
        description=task.description,
        request=ectx.run.request,
        inputs=upstream,
        feedback=agent.feedback_block(task),
    )
    resp = await ectx.llm(system=prompts.GENERAL_SYSTEM, prompt=prompt, mock=_mock)
    content = resp.text.strip()
    units = {u: content for u in (task.covers or [f"task:{task.id}"])}
    return AgentOutput(content=content, data={"units": units}, sources=agent.collect_sources(inputs))


class GeneralistAgent(BaseAgent):
    """Can do everything; follows the same procedure (and checks) as the specialists for typed tasks."""

    async def run(self, task: Task, inputs: dict[str, TaskResult], ectx: ExecContext) -> AgentOutput:
        if task.inputs.get("mode") != "monolith":
            from famas.agents.analysis import AnalysisAgent
            from famas.agents.coding import CodingAgent
            from famas.agents.research import ResearchAgent
            from famas.agents.writing import WritingAgent

            specialist = {
                TaskType.RESEARCH: ResearchAgent,
                TaskType.ANALYSIS: AnalysisAgent,
                TaskType.CODING: CodingAgent,
                TaskType.WRITING: WritingAgent,
            }.get(task.task_type)
            if specialist is not None:
                return await specialist(self.spec).run(task, inputs, ectx)
            return await run_generic(self, task, inputs, ectx)
        subjects: list[str] = list(task.inputs.get("subjects") or [])
        aspects: list[str] = list(task.inputs.get("aspects") or [])
        needs_code = bool(task.inputs.get("needs_code"))
        subj = {s: subject_from_key(s) for s in subjects}

        sources: list[Source] = []
        material_parts = []
        for s in subjects:
            repo = subj[s].repo
            if ectx.live and repo:
                info = await ectx.run.github.repo_overview(repo)
                material_parts.append(f"# {subj[s].name}\n{info.as_prompt()[:6000]}")
                sources.extend(info.sources())
            else:
                ectx.add_time(250.0)
                material_parts.append(f"# {subj[s].name}\n{readme_excerpt(s)}")
                if repo:
                    sources.append(
                        Source(
                            title=f"{subj[s].name} repository",
                            url=f"https://github.com/{repo}",
                            kind="github",
                        )
                    )
                docs = KB.get(s, {}).get("docs")
                if docs and not (repo and docs.endswith(repo)):
                    sources.append(Source(title=f"{subj[s].name} documentation", url=docs, kind="docs"))
        if not ectx.live:
            sources.append(Source(title="FA-MAS offline fixtures", kind="fixture"))

        research = {s: mock_research(s, subj[s].name, aspects) for s in subjects}
        units: dict[str, str] = {}
        title = (
            task.inputs.get("title") or f"Technical comparison: {', '.join(v.name for v in subj.values())}"
        )

        def _mock() -> MockReply:
            notes = []
            for s, rep in research.items():
                for f in rep.findings:
                    notes.append(f"[{subj[s].name} / {f.aspect}] {f.summary} " + " ".join(f.key_points))
            body = [f"# {title}", "", MOCK_NOTE, "", "## Executive Summary", ""]
            body.append(
                f"This report compares {', '.join(v.name for v in subj.values())} across "
                f"{', '.join(aspect_title(a) for a in aspects)}, produced by a single general-purpose agent."
            )
            body.append("")
            for a in aspects:
                rows, texts = [], {}
                for s, rep in research.items():
                    f = next((x for x in rep.findings if x.aspect == a), None)
                    if f:
                        rows.append(
                            {"subject": subj[s].name, "approach": f.summary, "strengths": f.key_points[:2]}
                        )
                        texts[subj[s].name] = f.summary + " " + " ".join(f.key_points)
                body.append(
                    aspect_section(a, {"rows": rows, "observations": _feature_observations(texts, a)})
                )
                body.append("")
            if needs_code:
                body.append(_inline_matrix(research, subj))
            # The single agent must "think through" all research notes in its output.
            return MockReply(
                text="<research-notes>\n" + "\n".join(notes) + "\n</research-notes>\n" + "\n".join(body)
            )

        prompt = prompts.WRITING_PROMPT.format(
            request=ectx.run.request,
            aspect_headings="\n".join(f"## {aspect_title(a)}" for a in aspects),
            inputs="\n\n".join(material_parts)[:20000],
            feedback=self.feedback_block(task),
        )
        resp = await ectx.llm(system=prompts.GENERAL_SYSTEM, prompt=prompt, mock=_mock)
        text = re.sub(r"<research-notes>.*?</research-notes>\s*", "", resp.text, flags=re.S).strip()
        text = re.sub(r"\n## References.*", "", text, flags=re.S | re.I).strip()
        content = text + "\n\n" + references_section(sources)

        sections = split_sections(content)
        if sections.get("executive summary"):
            units["report:summary"] = sections["executive summary"]
        units["report:references"] = sections.get("references", "")
        for a in aspects:
            sec = sections.get(aspect_title(a).lower(), "")
            if not sec:
                continue
            units[f"report:{a}"] = sec
            for s in subjects:
                if subj[s].name.lower() in sec.lower():
                    f = next((x for x in research[s].findings if x.aspect == a), None)
                    units[f"analysis:{a}:{s}"] = f"{subj[s].name}: {f.summary if f else 'see report'}"
                    units[f"research:{s}:{a}"] = unit_text(f.model_dump()) if f else sec
        if needs_code and "## Feature matrix" in content:
            units["code:feature_matrix"] = sections.get("feature matrix (computed)", "feature matrix")
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
        )


def _inline_matrix(research, subj) -> str:
    feats = list(FEATURE_KEYWORDS)
    lines = [
        "## Feature matrix (computed)",
        "",
        "| System | " + " | ".join(f.replace("_", " ") for f in feats) + " |",
    ]
    lines.append("|" + "---|" * (len(feats) + 1))
    for s, rep in research.items():
        text = " ".join(f.summary + " " + " ".join(f.key_points) for f in rep.findings).lower()
        cells = " | ".join("✓" if any(k in text for k in FEATURE_KEYWORDS[f]) else "·" for f in feats)
        lines.append(f"| {subj[s].name} | {cells} |")
    return "\n".join(lines)


class AggregatorAgent(BaseAgent):
    """Merges the outputs of decomposed subtasks back into the original node's interface."""

    async def run(self, task: Task, inputs: dict[str, TaskResult], ectx: ExecContext) -> AgentOutput:
        parts = [
            inputs[d]
            for d in task.dependencies
            if d in inputs and inputs[d].task_id.startswith(task.id + "__")
        ]
        if not parts:
            parts = list(inputs.values())
        units: dict[str, str] = {}
        findings: dict[str, dict] = {}
        rows: list = []
        observations: list[str] = []
        base: dict = {}
        for p in parts:
            units.update(p.units)
            findings.update(p.data.get("findings") or {})
            rows.extend(p.data.get("rows") or [])
            observations.extend(p.data.get("observations") or [])
            for k in ("subject", "name", "repo", "aspect"):
                if k in p.data and k not in base:
                    base[k] = p.data[k]
        data = {**base, "units": units, "merged_from": [p.task_id for p in parts]}
        if findings:
            data["findings"] = findings
            data["aspects"] = list(findings)
        if rows:
            data["rows"] = rows
            data["observations"] = list(dict.fromkeys(observations))
        reports = [p for p in parts if p.data.get("format") == "markdown"]
        if reports:  # an escalated single-agent task: its deliverable is the sub-plan's report
            data["format"] = "markdown"
            content = reports[-1].content
        else:
            content = "\n\n".join(p.content for p in parts)
        ectx.add_time(5.0)
        return AgentOutput(
            content=content, data=data, sources=self.collect_sources({p.task_id: p for p in parts})
        )
