from famas.agents.registry import AgentRegistry
from famas.core.models import InjectionRule, TaskType
from famas.orchestration.selector import AgentSelector
from famas.orchestration.stats import AgentStatsStore
from famas.planning.analyzer import heuristic_analysis
from famas.planning.templates import dynamic_plan, single_plan
from famas.simulation.injection import FailureInjector
from tests.conftest import FLAGSHIP, make_task


def selector(**kw) -> AgentSelector:
    return AgentSelector(AgentRegistry(), kw.get("stats") or AgentStatsStore(), kw.get("fixed"))


def test_selector_only_picks_capable_agents_and_respects_exclusions():
    t = make_task("r", ttype=TaskType.RESEARCH, required_capabilities=["research", "github"], covers=["u1"])
    sel = selector().select(t)
    assert sel.agent_id in {"research-fast", "research-deep", "generalist"}
    assert sel.candidates and all(c.breakdown for c in sel.candidates if c.eligible)
    t.excluded_agents = ["research-fast", "research-deep"]
    assert selector().select(t).agent_id == "generalist"
    t.excluded_agents.append("generalist")
    assert selector().select(t) is None


def test_selector_prefers_specialists_and_cheap_agents_for_narrow_tasks():
    t = make_task("r", ttype=TaskType.RESEARCH, required_capabilities=["research"], covers=["u1"])
    assert selector().select(t).agent_id == "research-fast"


def test_selector_learns_from_history():
    stats = AgentStatsStore()
    t = make_task("r", ttype=TaskType.RESEARCH, required_capabilities=["research"], covers=["u1"])
    for _ in range(6):
        stats.record("research-fast", "research", success=False)
    assert selector(stats=stats).select(t).agent_id != "research-fast"


def test_fixed_selector():
    s = selector(fixed={TaskType.RESEARCH: "research-fast", TaskType.GENERAL: "generalist"})
    t = make_task("r", ttype=TaskType.RESEARCH)
    assert s.select(t).strategy == "fixed"
    t.excluded_agents = ["research-fast"]
    assert s.select(t) is None


def test_injection_placeholders_and_execution_counting():
    a = heuristic_analysis(FLAGSHIP)
    tasks = {t.id: t for t in dynamic_plan(a)}
    inj = FailureInjector([InjectionRule(kind="timeout", target="research:{s0}:*", on_executions=[1])], a)
    assert inj.rules[0].target == "research:langgraph:*"
    assert inj.decide(tasks["research_crewai"], "research-fast") is None
    f = inj.decide(tasks["research_langgraph"], "research-fast")
    assert f is not None and f.kind == "timeout" and f.transient
    assert inj.decide(tasks["research_langgraph"], "research-fast") is None  # second execution succeeds


def test_injection_agent_binding_and_too_complex():
    a = heuristic_analysis(FLAGSHIP)
    tasks = {t.id: t for t in dynamic_plan(a)}
    inj = FailureInjector(
        [
            InjectionRule(
                kind="agent_incapable", target="analysis:{a0}:*", on_executions="all", agent="@initial"
            )
        ],
        a,
    )
    assert inj.decide(tasks["analyze_architecture"], "analysis-standard").kind == "agent_incapable"
    assert inj.decide(tasks["analyze_architecture"], "analysis-standard") is not None
    assert inj.decide(tasks["analyze_architecture"], "analysis-expert") is None

    inj2 = FailureInjector([InjectionRule(kind="too_complex", target="research:{s1}:*", max_units=2)], a)
    f = inj2.decide(tasks["research_crewai"], "research-fast")
    assert f.kind == "too_complex" and len(f.units) == 2
    small = tasks["research_crewai"].model_copy(update={"covers": tasks["research_crewai"].covers[:2]})
    assert inj2.decide(small, "research-fast") is None


def test_monolith_meets_faults_sequentially():
    a = heuristic_analysis(FLAGSHIP)
    mono = single_plan(a)[0]
    rules = [
        InjectionRule(kind="timeout", target="research:{s0}:*", on_executions=[1]),
        InjectionRule(kind="invalid_format", target="report:*", on_executions=[1]),
    ]
    inj = FailureInjector(rules, a)
    assert inj.decide(mono, "generalist").kind == "timeout"
    assert inj.decide(mono, "generalist").kind == "invalid_format"  # reached only on the second pass
    assert inj.decide(mono, "generalist") is None


def test_drop_dependency_applies_once():
    a = heuristic_analysis(FLAGSHIP)
    tasks = dynamic_plan(a)
    inj = FailureInjector(
        [InjectionRule(kind="drop_dependency", target="analysis:{a0}:*", subject="{s1}")], a
    )
    assert inj.dependency_drops(tasks) == [("analyze_architecture", "research_crewai")]
    assert inj.dependency_drops(tasks) == []
