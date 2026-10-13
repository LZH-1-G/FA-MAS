import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from famas.api.app import create_app  # noqa: E402
from famas.config import Settings  # noqa: E402
from famas.persistence.store import Store  # noqa: E402
from tests.conftest import MEDIUM  # noqa: E402


@pytest.fixture
def client(tmp_path):
    app = create_app(
        Settings(database_url="sqlite:///:memory:", frontend_dist=str(tmp_path / "none")),
        Store("sqlite:///:memory:"),
    )
    with TestClient(app, base_url="http://localhost") as c:
        yield c


def wait(client, run_id, timeout=20.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = client.get(f"/api/runs/{run_id}").json()
        if s["status"] != "running":
            return s
        time.sleep(0.05)
    raise TimeoutError


def test_health_and_meta(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["database"] == "sqlite"
    meta = client.get("/api/meta").json()
    assert {s["name"] for s in meta["systems"]} >= {"fa_mas", "full_replan"}
    assert any(s["name"] == "corruption" for s in meta["scenarios"])
    assert len(meta["examples"]) >= 3


def test_run_lifecycle(client):
    r = client.post("/api/runs", json={"request": MEDIUM, "scenario": "corruption", "simulated": True})
    assert r.status_code == 201
    run_id = r.json()["run_id"]
    snap = wait(client, run_id)
    assert snap["status"] == "completed"
    assert {n["id"] for n in snap["view"]["nodes"]} == {
        "research_langgraph",
        "research_crewai",
        "analyze_architecture",
        "report",
    }
    assert snap["replans"] and snap["metrics"]["recovery_rounds"] == 1

    time.sleep(2.2)  # finalisation persists the graph
    snap = client.get(f"/api/runs/{run_id}").json()
    assert snap["live"] is False and snap["final_output"].startswith("# Technical comparison")
    t = client.get(f"/api/runs/{run_id}/tasks/analyze_architecture").json()
    assert t["failures"] and t["result"]["content"]
    assert client.get(f"/api/runs/{run_id}/report").text.startswith("# Technical")

    with client.stream("GET", f"/api/runs/{run_id}/events") as st:
        events = [line.split(": ", 1)[1] for line in st.iter_lines() if line.startswith("event:")]
    assert events[0] == "run_started" and "replan_applied" in events and events[-1] == "end"

    runs = client.get("/api/runs").json()
    assert runs[0]["id"] == run_id and runs[0]["recovery_rounds"] == 1
    assert client.delete(f"/api/runs/{run_id}").status_code == 204
    assert client.get(f"/api/runs/{run_id}").status_code == 404


def test_validation_errors(client):
    assert client.post("/api/runs", json={"request": "x"}).status_code == 422
    assert client.post("/api/runs", json={"request": MEDIUM, "scenario": "nope"}).status_code == 400
    assert client.post("/api/runs", json={"request": MEDIUM, "mode": "live"}).status_code == 400
    assert client.get("/api/runs/missing").status_code == 404
    assert client.get("/api/runs/missing/report").status_code == 404


def test_benchmark_endpoint(client):
    b = client.post(
        "/api/benchmarks",
        json={"tasks": ["T1"], "scenarios": ["none"], "systems": ["fa_mas", "single_agent"]},
    ).json()
    t0 = time.time()
    while time.time() - t0 < 20:
        res = client.get(f"/api/benchmarks/{b['benchmark_id']}").json()
        if res["status"] != "running":
            break
        time.sleep(0.05)
    assert res["status"] == "completed" and set(res["summary"]["overall"]) == {"fa_mas", "single_agent"}
    assert client.get("/api/benchmarks").json()[0]["id"] == b["benchmark_id"]
