from famas.core.models import TaskType
from famas.planning.analyzer import heuristic_analysis
from famas.planning.dag import TaskGraph
from famas.planning.templates import dynamic_plan, fixed_plan, single_plan
from famas.planning.validator import validate_graph
from tests.conftest import FLAGSHIP, MEDIUM, make_task


def test_analyzer_detects_subjects_aspects_and_complexity():
    a = heuristic_analysis(FLAGSHIP)
    assert [s.key for s in a.subjects] == ["langgraph", "crewai", "autogen", "agent_framework"]
    assert a.aspects == ["architecture", "communication", "scheduling", "failure_recovery"]
    assert a.complexity == "complex" and a.strategy == "multi_agent"
    assert a.subjects[3].repo == "microsoft/agent-framework"

    m = heuristic_analysis(MEDIUM)
    assert m.complexity == "medium" and [s.key for s in m.subjects] == ["langgraph", "crewai"]

    s = heuristic_analysis("Summarize the architecture of LangGraph.")
    assert s.complexity == "simple" and s.strategy == "single_agent"

    g = heuristic_analysis("Write a haiku about distributed systems.")
    assert g.domain == "general" and g.strategy == "single_agent"


def test_analyzer_handles_explicit_repos_and_code():
    a = heuristic_analysis(
        "Compare github.com/pydantic/pydantic-ai and CrewAI and compute a feature matrix of their tooling"
    )
    keys = [s.key for s in a.subjects]
    assert "crewai" in keys and any(s.repo == "pydantic/pydantic-ai" for s in a.subjects)
    assert a.needs_code


def test_dynamic_plan_shape():
    tasks = dynamic_plan(heuristic_analysis(FLAGSHIP))
    g = TaskGraph(tasks)
    assert g.is_acyclic()
    layers = g.layers()
    assert len(layers[0]) == 4 and all(t.startswith("research_") for t in layers[0])
    assert len(layers[1]) == 4 and all(t.startswith("analyze_") for t in layers[1])
    assert layers[2] == ["report"]
    report = g.get("report")
    assert "report:summary" in report.covers and "report:references" in report.covers


def test_dynamic_plan_with_code_task():
    tasks = dynamic_plan(
        heuristic_analysis("Compare LangGraph and CrewAI architecture and include a computed feature matrix")
    )
    ids = [t.id for t in tasks]
    assert "code_feature_matrix" in ids
    assert "code_feature_matrix" in next(t for t in tasks if t.id == "report").dependencies


def test_single_and_fixed_plans():
    a = heuristic_analysis(FLAGSHIP)
    single = single_plan(a)
    assert len(single) == 1 and single[0].task_type == TaskType.GENERAL and len(single[0].covers) > 20
    fixed = TaskGraph(fixed_plan(a))
    assert fixed.layers() == [["stage_research"], ["stage_analysis"], ["stage_report"]]


def test_validator_autofixes_and_detects_cycles():
    g = TaskGraph([make_task("a"), make_task("b", ["a", "ghost"]), make_task("c", ["c"])])
    rep = validate_graph(g, known_capabilities={"general"})
    assert rep.valid
    assert g.get("b").dependencies == ["a"]
    assert g.get("c").dependencies == []
    assert any(i.code == "unknown_dependency" for i in rep.issues)

    cyc = TaskGraph([make_task("x", ["y"]), make_task("y", ["x"])])
    rep2 = validate_graph(cyc)
    assert not rep2.valid and rep2.errors[0].code == "cycle"

    empty = validate_graph(TaskGraph())
    assert not empty.valid


def test_validator_maps_unknown_capabilities():
    t = make_task("a", required_capabilities=["telepathy"])
    g = TaskGraph([t])
    rep = validate_graph(g, known_capabilities={"general", "research"})
    assert rep.valid and g.get("a").required_capabilities == ["general"]
