import pytest

from famas.core.models import TaskResult, TaskStatus
from famas.planning.dag import TaskGraph
from tests.conftest import make_task


def diamond() -> TaskGraph:
    #      A
    #    /   \
    #   B     C
    #    \   /
    #      D
    #      |
    #      E
    return TaskGraph(
        [
            make_task("A"),
            make_task("B", ["A"]),
            make_task("C", ["A"]),
            make_task("D", ["B", "C"]),
            make_task("E", ["D"]),
        ]
    )


def complete(g: TaskGraph, tid: str, content: str = "x") -> None:
    t = g.get(tid)
    t.status = TaskStatus.COMPLETED
    t.result = TaskResult(
        task_id=tid, agent_id="a", content=content, output_fingerprint=f"out-{tid}-{content}"
    )


def test_structure_queries():
    g = diamond()
    assert g.roots() == ["A"] and g.sinks() == ["E"]
    assert g.layers() == [["A"], ["B", "C"], ["D"], ["E"]]
    assert g.descendants("B") == {"D", "E"}
    assert g.ancestors("D") == {"A", "B", "C"}
    assert g.topological_order()[0] == "A"
    assert g.critical_path_length() == 4


def test_ready_tasks_follow_dependencies():
    g = diamond()
    assert [t.id for t in g.ready_tasks()] == ["A"]
    complete(g, "A")
    assert [t.id for t in g.ready_tasks()] == ["B", "C"]
    complete(g, "B")
    assert [t.id for t in g.ready_tasks()] == ["C"]
    complete(g, "C")
    assert [t.id for t in g.ready_tasks()] == ["D"]


def test_skipped_dependency_counts_as_satisfied():
    g = diamond()
    complete(g, "A")
    complete(g, "B")
    g.get("C").status = TaskStatus.SKIPPED
    assert [t.id for t in g.ready_tasks()] == ["D"]


def test_cycle_rejected_and_duplicates():
    g = diamond()
    with pytest.raises(ValueError):
        g.add_dependency("A", "E")
    with pytest.raises(ValueError):
        g.add_task(make_task("A"))


def test_remove_task_cleans_dependencies():
    g = diamond()
    g.remove_task("C")
    assert g.get("D").dependencies == ["B"]
    assert "C" not in g.nx


def test_input_fingerprint_is_merkle_style():
    g = diamond()
    complete(g, "A", "v1")
    complete(g, "B", "v1")
    complete(g, "C", "v1")
    fp1 = g.input_fingerprint("D")
    complete(g, "C", "v1")  # identical output -> identical fingerprint (early cut-off)
    assert g.input_fingerprint("D") == fp1
    complete(g, "C", "v2")  # changed upstream output -> fingerprint changes
    assert g.input_fingerprint("D") != fp1
    fp2 = g.input_fingerprint("D")
    g.get("D").feedback.append("be thorough")  # spec change -> fingerprint changes
    assert g.input_fingerprint("D") != fp2


def test_serialization_roundtrip():
    g = diamond()
    complete(g, "A")
    g2 = TaskGraph.from_dict(g.to_dict())
    assert g2.ids() == g.ids()
    assert g2.get("A").status == TaskStatus.COMPLETED
    assert g2.input_fingerprint("B") == g.input_fingerprint("B")
    view = g2.view()
    assert len(view["nodes"]) == 5 and len(view["edges"]) == 5
