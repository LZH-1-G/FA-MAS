"""Deterministic plan templates.

* ``dynamic_plan`` – what the FA-MAS planner produces for repository research:
  one research task per subject (parallel), one comparison per aspect (parallel,
  fan-in from research), an optional computed feature matrix, and a final report.
  Simple requests collapse to a single generalist task.
* ``single_plan`` – Baseline A: one monolithic task.
* ``fixed_plan`` – Baseline B: a predefined linear pipeline.

Every task lists the *work units* it must deliver (``covers``); the evaluator
checks them and failure injection targets them.
"""

from __future__ import annotations

from famas.core.models import Task, TaskAnalysis, TaskType
from famas.knowledge.catalog import aspect_title


def research_units(s: str, aspects: list[str]) -> list[str]:
    return [f"research:{s}:{a}" for a in aspects]


def analysis_units(a: str, subjects: list[str]) -> list[str]:
    return [f"analysis:{a}:{s}" for s in subjects]


def report_units(aspects: list[str]) -> list[str]:
    return ["report:summary"] + [f"report:{a}" for a in aspects] + ["report:references"]


def _title(analysis: TaskAnalysis) -> str:
    names = ", ".join(s.name for s in analysis.subjects)
    return f"Technical comparison: {names}" if len(analysis.subjects) > 1 else f"Technical overview: {names}"


def monolith_task(analysis: TaskAnalysis, task_id: str = "solve") -> Task:
    if analysis.domain != "repo_research":
        return Task(
            id=task_id,
            name="Answer request",
            description=f"Complete the user's request end-to-end: {analysis.request}",
            task_type=TaskType.GENERAL,
            required_capabilities=["general"],
            expected_output="A complete answer to the request.",
            covers=["answer"],
            origin="template",
        )
    subjects = [s.key for s in analysis.subjects]
    aspects = analysis.aspects
    covers = []
    for s in subjects:
        covers += research_units(s, aspects)
    for a in aspects:
        covers += analysis_units(a, subjects)
    if analysis.needs_code:
        covers.append("code:feature_matrix")
    covers += report_units(aspects)
    return Task(
        id=task_id,
        name="Research, analyse and report",
        description=(
            f"Research {', '.join(s.name for s in analysis.subjects)} on {', '.join(aspect_title(a) for a in aspects)}, "
            "compare them and write the final report."
        ),
        task_type=TaskType.GENERAL,
        required_capabilities=["general", "research", "analysis", "writing"],
        expected_output="Complete Markdown report with references.",
        inputs={
            "mode": "monolith",
            "subjects": subjects,
            "aspects": aspects,
            "needs_code": analysis.needs_code,
            "title": _title(analysis),
        },
        acceptance_criteria=["covers every subject and aspect", "includes references"],
        covers=covers,
        origin="template",
    )


def dynamic_plan(analysis: TaskAnalysis) -> list[Task]:
    if analysis.strategy == "single_agent":
        return [monolith_task(analysis)]
    if analysis.domain != "repo_research":
        return [
            Task(
                id="draft",
                name="Draft solution",
                description=f"Work out a complete draft solution for: {analysis.request}",
                task_type=TaskType.GENERAL,
                required_capabilities=["general"],
                expected_output="A thorough draft.",
                covers=["task:draft"],
                origin="template",
            ),
            Task(
                id="finalize",
                name="Finalize answer",
                description="Review the draft for correctness and completeness and write the final answer.",
                task_type=TaskType.WRITING,
                required_capabilities=["writing"],
                dependencies=["draft"],
                expected_output="The final, polished answer.",
                covers=["task:finalize"],
                origin="template",
            ),
        ]
    subjects = [s.key for s in analysis.subjects]
    aspects = analysis.aspects
    tasks: list[Task] = []
    research_ids = []
    for subj in analysis.subjects:
        tid = f"research_{subj.key}"
        research_ids.append(tid)
        tasks.append(
            Task(
                id=tid,
                name=f"Research {subj.name}",
                description=(
                    f"Investigate {subj.name}"
                    + (f" (github.com/{subj.repo})" if subj.repo else "")
                    + f" and report findings on: {', '.join(aspect_title(a) for a in aspects)}."
                ),
                task_type=TaskType.RESEARCH,
                required_capabilities=["research", "github"],
                expected_output="Per-aspect summary and key points with sources.",
                inputs={"subject": subj.key, "name": subj.name, "repo": subj.repo, "aspects": list(aspects)},
                acceptance_criteria=["every requested aspect covered", "at least one source"],
                covers=research_units(subj.key, aspects),
                origin="template",
            )
        )
    analysis_ids = []
    for a in aspects:
        tid = f"analyze_{a}"
        analysis_ids.append(tid)
        tasks.append(
            Task(
                id=tid,
                name=f"Compare {aspect_title(a)}",
                description=f"Compare {', '.join(s.name for s in analysis.subjects)} on {aspect_title(a)} using the research findings.",
                task_type=TaskType.ANALYSIS,
                required_capabilities=["analysis", "comparison"],
                dependencies=list(research_ids),
                expected_output="Comparison table with observations.",
                inputs={"aspect": a, "subjects": list(subjects)},
                acceptance_criteria=["one row per subject", "no contradictions with inputs"],
                covers=analysis_units(a, subjects),
                origin="template",
            )
        )
    final_deps = list(analysis_ids)
    if analysis.needs_code:
        tasks.append(
            Task(
                id="code_feature_matrix",
                name="Compute feature matrix",
                description="Write and run a program that derives a feature matrix from the research findings.",
                task_type=TaskType.CODING,
                required_capabilities=["coding", "code_execution"],
                dependencies=list(research_ids),
                expected_output="Feature matrix table computed by executed code.",
                inputs={"program": "feature_matrix"},
                covers=["code:feature_matrix"],
                origin="template",
            )
        )
        final_deps.append("code_feature_matrix")
    tasks.append(
        Task(
            id="report",
            name="Write final report",
            description="Synthesise the validated analyses into a structured technical report with references.",
            task_type=TaskType.WRITING,
            required_capabilities=["writing", "reporting"],
            dependencies=final_deps,
            expected_output="Markdown report.",
            inputs={"aspects": list(aspects), "subjects": list(subjects), "title": _title(analysis)},
            acceptance_criteria=["executive summary", "one section per aspect", "references"],
            covers=report_units(aspects),
            origin="template",
        )
    )
    return tasks


def single_plan(analysis: TaskAnalysis) -> list[Task]:
    return [monolith_task(analysis)]


def fixed_plan(analysis: TaskAnalysis) -> list[Task]:
    if analysis.domain != "repo_research":
        plan = dynamic_plan(analysis.model_copy(update={"strategy": "multi_agent"}))
        return plan
    subjects = [s.key for s in analysis.subjects]
    aspects = analysis.aspects
    r_units = [u for s in subjects for u in research_units(s, aspects)]
    a_units = [u for a in aspects for u in analysis_units(a, subjects)]
    tasks = [
        Task(
            id="stage_research",
            name="Stage 1: research",
            description="Research every subject on every aspect.",
            task_type=TaskType.RESEARCH,
            required_capabilities=["research"],
            inputs={"subjects": subjects, "aspects": aspects},
            covers=r_units,
            origin="template",
        ),
        Task(
            id="stage_analysis",
            name="Stage 2: analysis",
            description="Compare all subjects on every aspect.",
            task_type=TaskType.ANALYSIS,
            required_capabilities=["analysis"],
            dependencies=["stage_research"],
            inputs={"subjects": subjects, "aspects": aspects},
            covers=a_units,
            origin="template",
        ),
    ]
    report_deps = ["stage_analysis"]
    if analysis.needs_code:
        tasks.append(
            Task(
                id="stage_code",
                name="Stage 3: feature matrix",
                description="Compute the feature matrix from the research findings.",
                task_type=TaskType.CODING,
                required_capabilities=["coding"],
                dependencies=["stage_research", "stage_analysis"],
                inputs={"program": "feature_matrix"},
                covers=["code:feature_matrix"],
                origin="template",
            )
        )
        report_deps = ["stage_analysis", "stage_code"]
    tasks.append(
        Task(
            id="stage_report",
            name="Final stage: report",
            description="Write the final report.",
            task_type=TaskType.WRITING,
            required_capabilities=["writing"],
            dependencies=report_deps,
            inputs={"aspects": aspects, "subjects": subjects, "title": _title(analysis)},
            covers=report_units(aspects),
            origin="template",
        )
    )
    return tasks
