"""Claude Agent SDK provider: safety gates, message handling and end-to-end runs - all offline.

No test here starts Claude Code or calls a model. Two kinds of doubles are used:

* ``FakeSession`` - a pure-Python stand-in for an SDK session that yields
  objects shaped like the SDK's messages. These tests run without the SDK.
* ``ScriptedTransport`` - the SDK's own ``Transport`` interface fed with
  scripted CLI frames, so the *real* ``ClaudeSDKClient`` (handshake, message
  parser, error types) is exercised. Skipped if ``claude-agent-sdk`` is absent.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import BaseModel

from famas.config import CLAUDE_PROVIDER, Settings, get_settings
from famas.core.models import RunConfig
from famas.llm import MockReply, create_llm
from famas.llm import claude_agent_sdk_client as cs
from famas.llm.base import (
    LLMAuthError,
    LLMBudgetExceeded,
    LLMConfigError,
    LLMError,
    LLMOutputError,
    LLMTimeout,
    LLMUsageLimit,
)
from famas.llm.mock import MockLLM
from famas.orchestration.engine import Engine
from famas.orchestration.stats import AgentStatsStore
from tests.conftest import MEDIUM

SUB_ACCOUNT = {"subscriptionType": "pro", "apiProvider": "firstParty"}
REQUEST = "Compare LangGraph, CrewAI and AutoGen on scheduling and failure recovery"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    """Isolate from the host: no credential variables, fresh guard, temp working dir."""
    for name in cs.BILLING_OVERRIDE_ENV + cs.CLOUD_PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FAMAS_CLAUDE_WORKDIR", str(tmp_path / "claude-wd"))
    cs.reset_usage_guard()
    yield
    cs.reset_usage_guard()


def claude_settings(tmp_path=None, **kw) -> Settings:
    base = {
        "llm_provider": CLAUDE_PROVIDER,
        "claude_workdir": str(tmp_path / "wd") if tmp_path else "./data/claude-workdir-test",
        "database_url": "sqlite:///:memory:",
    }
    base.update(kw)
    return Settings(**base)


# ----------------------------------------------------------------------------- SDK-shaped fakes
@dataclass
class TextBlock:
    text: str


@dataclass
class AssistantMessage:
    content: list[Any]
    model: str
    error: str | None = None


@dataclass
class ResultMessage:
    subtype: str = "success"
    is_error: bool = False
    result: str | None = ""
    structured_output: Any = None
    usage: dict[str, Any] = field(default_factory=dict)
    total_cost_usd: float | None = None
    errors: list[str] | None = None
    api_error_status: int | None = None


@dataclass
class RateLimitInfo:
    status: str
    rate_limit_type: str | None = None
    resets_at: int | None = None
    utilization: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RateLimitEvent:
    rate_limit_info: RateLimitInfo


def ok(text: str = "pong", structured: Any = None, cost: float | None = 0.0123, model: str = "claude-x"):
    return [
        AssistantMessage([TextBlock(text)], model),
        ResultMessage(
            result=text,
            structured_output=structured,
            usage={"input_tokens": 10, "output_tokens": 3, "cache_read_input_tokens": 5},
            total_cost_usd=cost,
        ),
    ]


def from_mock(spec: cs.CallSpec) -> list[Any]:
    """Answer with the component's deterministic mock reply (needs dry-run so it is computed)."""
    reply = spec.mock_reply or MockReply(text=f"[{spec.component}] ok")
    parsed = reply.parsed.model_dump(mode="json") if isinstance(reply.parsed, BaseModel) else reply.parsed
    text = json.dumps(parsed) if (spec.json_schema is not None and parsed is not None) else reply.text
    return ok(text=text, structured=parsed if spec.schema_mode == "native" else None)


class FakeSession:
    def __init__(self, spec, script, account=SUB_ACCOUNT, log=None, delay=0.0, hang=False):
        self.spec, self.script, self.account = spec, script, account
        self.log = log if log is not None else []
        self.delay, self.hang = delay, hang
        self.sent: list[str] = []
        self.closed = False

    async def connect(self):
        self.log.append(("connect", self.spec.component))
        return {"account": self.account} if self.account is not None else {}

    async def send(self, prompt):
        self.sent.append(prompt)
        self.log.append(("send", self.spec.component))

    def receive(self):
        async def gen():
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.hang:
                await asyncio.sleep(3600)
            for m in self.script(self.spec):
                yield m

        return gen()

    async def close(self):
        self.closed = True
        self.log.append(("close", self.spec.component))


class Factory:
    """Session factory that records every session it creates."""

    def __init__(self, script=lambda spec: ok(), **session_kw):
        self.script, self.kw = script, session_kw
        self.sessions: list[FakeSession] = []
        self.log: list[tuple[str, str]] = []

    def __call__(self, spec):
        s = FakeSession(spec, self.script, log=self.log, **self.kw)
        self.sessions.append(s)
        return s


def client_with(factory, tmp_path, **settings_kw) -> cs.ClaudeAgentSDKLLM:
    return cs.ClaudeAgentSDKLLM(
        claude_settings(tmp_path, **settings_kw), session_factory=factory, guard=cs.UsageGuard()
    )


async def ask(client, schema=None, mock=None, tier="large"):
    return await client.complete(
        component="test", system="sys", prompt="ping", schema=schema, tier=tier, mock=mock
    )


class Pong(BaseModel):
    ok: bool
    word: str


@pytest.fixture
def no_sdk_sessions(monkeypatch):
    """Fail the test if anything tries to start a real Claude Code session."""

    def boom(*a, **k):
        raise AssertionError("a real Claude Code session was started")

    monkeypatch.setattr(cs, "SDKSession", boom)
    monkeypatch.setattr(cs, "dry_run_session", boom)


# ----------------------------------------------------------------------------- configuration
def test_settings_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("FAMAS_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("FAMAS_LLM_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setenv("FAMAS_CLAUDE_MAX_BUDGET_USD_PER_CALL", "none")
    monkeypatch.setenv("FAMAS_CLAUDE_SMALL_MODEL", "claude-haiku-4-5")
    get_settings.cache_clear()
    try:
        s = get_settings()
    finally:
        get_settings.cache_clear()
    assert s.llm_provider == CLAUDE_PROVIDER
    assert s.llm_api_key is None  # the provider never reads API keys
    assert s.claude_allow_real_calls is False and s.claude_dry_run is False
    assert s.claude_max_budget_usd_per_call is None
    assert s.claude_small_model == "claude-haiku-4-5" and s.claude_model == "sonnet"


def test_factory_keeps_mock_mode_mock(tmp_path):
    s = claude_settings(tmp_path)
    assert isinstance(create_llm("mock", s), MockLLM)
    live = create_llm("live", s)
    assert isinstance(live, cs.ClaudeAgentSDKLLM) and not live.is_mock


def test_live_status_reasons(tmp_path):
    assert Settings().live_status() == (False, "FAMAS_LLM_PROVIDER is 'mock' (see .env.example)")
    ok_, reason = claude_settings(tmp_path).live_status()
    assert not ok_ and ("disabled" if cs.sdk_installed() else "not installed") in reason
    if cs.sdk_installed():
        assert claude_settings(tmp_path, claude_dry_run=True).live_status()[0] is True
        assert claude_settings(tmp_path, claude_allow_real_calls=True).live_status()[0] is True
    ok_, reason = claude_settings(tmp_path, claude_auth="api_key").live_status()
    assert not ok_ and "only 'subscription'" in reason


def test_live_status_flags_api_key_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-value")
    st = cs.provider_status(claude_settings(tmp_path, claude_allow_real_calls=True))
    if cs.sdk_installed():
        assert st["live_available"] is False and "ANTHROPIC_API_KEY" in st["reason"]
    assert st["conflicting_env"] == ["ANTHROPIC_API_KEY"]
    assert "sk-secret-value" not in json.dumps(st)


def test_conflicting_env_detection():
    assert cs.conflicting_env({}) == []
    env = {"ANTHROPIC_AUTH_TOKEN": "t", "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "0"}
    assert cs.conflicting_env(env) == ["ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK"]
    assert cs.conflicting_env({"ANTHROPIC_API_KEY": "  "}) == []


# ----------------------------------------------------------------------------- safety gates
async def test_real_calls_disabled_by_default(tmp_path, no_sdk_sessions):
    client = cs.ClaudeAgentSDKLLM(claude_settings(tmp_path), guard=cs.UsageGuard())
    with pytest.raises(LLMConfigError, match="disabled"):
        await client.preflight()
    with pytest.raises(LLMConfigError, match="disabled"):
        await ask(client)
    assert client.calls_made == 0


async def test_api_key_in_environment_is_refused(tmp_path, no_sdk_sessions):
    client = cs.ClaudeAgentSDKLLM(
        claude_settings(tmp_path, claude_allow_real_calls=True),
        environ={"ANTHROPIC_API_KEY": "sk-secret-value"},
        guard=cs.UsageGuard(),
    )
    with pytest.raises(LLMAuthError) as ei:
        await ask(client)
    assert "ANTHROPIC_API_KEY" in str(ei.value) and "sk-secret-value" not in str(ei.value)
    assert ei.value.fatal


async def test_unsupported_auth_mode_is_refused(tmp_path):
    client = client_with(Factory(), tmp_path, claude_auth="api_key")
    with pytest.raises(LLMConfigError, match="only uses a Claude"):
        await ask(client)


@pytest.mark.parametrize(
    "account, ok_",
    [
        ({"subscriptionType": "pro", "apiProvider": "firstParty"}, True),
        ({"subscriptionType": "max", "apiProvider": "firstParty"}, True),
        ({"tokenSource": "CLAUDE_CODE_OAUTH_TOKEN", "apiProvider": "firstParty"}, True),
        (
            {"subscriptionType": "pro", "apiProvider": "firstParty", "apiKeySource": "ANTHROPIC_API_KEY"},
            False,
        ),
        ({"apiProvider": "firstParty", "apiKeySource": "/login managed key"}, False),
        ({"apiProvider": "firstParty", "apiKeySource": "apiKeyHelper"}, False),
        ({"apiProvider": "bedrock"}, False),
        ({"apiProvider": "firstParty", "tokenSource": "ANTHROPIC_AUTH_TOKEN"}, False),
        ({"apiProvider": "firstParty"}, False),  # not logged in
        (None, False),  # CLI did not report credentials: fail closed
    ],
)
def test_account_verification(account, ok_):
    info = {"account": account} if account is not None else {"commands": []}
    if ok_:
        summary = cs.verify_subscription_account(info)
        assert summary["api_provider"] == "firstParty"
        assert "email" not in json.dumps(summary)
    else:
        with pytest.raises(LLMAuthError):
            cs.verify_subscription_account(info)


async def test_api_key_account_refused_before_prompt_is_sent(tmp_path):
    factory = Factory(account={**SUB_ACCOUNT, "apiKeySource": "ANTHROPIC_API_KEY"})
    client = client_with(factory, tmp_path)
    with pytest.raises(LLMAuthError, match="API key"):
        await ask(client)
    s = factory.sessions[0]
    assert s.sent == [] and s.closed  # handshake only; the prompt never left
    assert client.calls_made == 0


# ----------------------------------------------------------------------------- completions
async def test_plain_completion(tmp_path):
    factory = Factory()
    client = client_with(factory, tmp_path)
    resp = await ask(client, tier="small")
    assert resp.text == "pong"
    assert resp.usage.tokens_in == 15 and resp.usage.tokens_out == 3  # input + cache reads
    assert resp.usage.cost == pytest.approx(0.0123) and resp.usage.model == "claude-x"
    spec = factory.sessions[0].spec
    assert spec.model == "haiku" and spec.json_schema is None and spec.max_turns == 3
    assert factory.log == [("connect", "test"), ("send", "test"), ("close", "test")]
    assert client.calls_made == 1 and client.account["subscription_type"] == "pro"


async def test_cost_falls_back_to_price_table(tmp_path):
    client = client_with(Factory(lambda spec: ok(cost=None)), tmp_path)
    resp = await ask(client)
    assert resp.usage.cost > 0  # API-equivalent estimate for "sonnet"


async def test_structured_native(tmp_path):
    factory = Factory(lambda spec: ok(text="", structured={"ok": True, "word": "pong"}))
    resp = await ask(client_with(factory, tmp_path), schema=Pong)
    assert resp.parsed == Pong(ok=True, word="pong") and json.loads(resp.text)["word"] == "pong"
    spec = factory.sessions[0].spec
    assert spec.schema_mode == "native" and spec.json_schema["title"] == "Pong"
    assert "JSON schema" not in spec.prompt


async def test_structured_falls_back_to_text_json(tmp_path):
    factory = Factory(lambda spec: ok(text='Here you go:\n```json\n{"ok": true, "word": "pong"}\n```'))
    resp = await ask(client_with(factory, tmp_path), schema=Pong)
    assert resp.parsed.word == "pong"


async def test_structured_prompt_mode(tmp_path):
    factory = Factory(lambda spec: ok(text='{"ok": false, "word": "x"}'))
    client = client_with(factory, tmp_path, claude_structured_output="prompt")
    resp = await ask(client, schema=Pong)
    assert resp.parsed == Pong(ok=False, word="x")
    assert "JSON schema" in factory.sessions[0].spec.prompt


async def test_unparseable_structured_output(tmp_path):
    client = client_with(Factory(lambda spec: ok(text="no json here")), tmp_path)
    with pytest.raises(LLMOutputError):
        await ask(client, schema=Pong)


async def test_deterministic_steps_never_call_claude(tmp_path):
    factory = Factory()
    client = client_with(factory, tmp_path)
    resp = await client.complete(
        component="aggregate", system="", prompt="", tier="none", mock=lambda: MockReply(text="merged")
    )
    assert resp.text == "merged" and resp.usage.calls == 0 and not factory.sessions


# ----------------------------------------------------------------------------- usage limits & errors
async def test_rejected_rate_limit_halts_everything(tmp_path):
    def script(spec):
        return [RateLimitEvent(RateLimitInfo("rejected", "five_hour", resets_at=4102444800))] + ok()

    factory = Factory(script)
    client = client_with(factory, tmp_path)
    with pytest.raises(LLMUsageLimit, match="usage limit reached") as ei:
        await ask(client)
    assert ei.value.fatal and client.guard.snapshot()["halted"]
    with pytest.raises(LLMUsageLimit):  # fails fast without starting another session
        await ask(client)
    assert len(factory.sessions) == 1 and factory.sessions[0].closed


@pytest.mark.parametrize(
    "info",
    [
        RateLimitInfo("allowed", "overage", raw={"isUsingOverage": True}),
        RateLimitInfo("allowed", "five_hour", raw={"overageInUse": True}),
        RateLimitInfo("allowed_warning", "overage"),
    ],
)
async def test_extra_usage_stops_immediately(tmp_path, info):
    factory = Factory(lambda spec: [RateLimitEvent(info)] + ok())
    client = client_with(factory, tmp_path)
    with pytest.raises(LLMUsageLimit, match="extra usage"):
        await ask(client)
    snap = client.guard.snapshot()
    assert snap["halted"] and snap["halted_until"] is None  # until restart / explicit reset
    client.guard.reset()
    client.guard.check()


async def test_limit_warning_continues_unless_configured(tmp_path):
    def script(spec):
        return [RateLimitEvent(RateLimitInfo("allowed_warning", "seven_day", utilization=0.85))] + ok()

    client = client_with(Factory(script), tmp_path)
    assert (await ask(client)).text == "pong"
    assert client.guard.snapshot()["last_event"]["utilization"] == 0.85
    strict = client_with(Factory(script), tmp_path, claude_stop_on_limit_warning=True)
    with pytest.raises(LLMUsageLimit, match="close to your usage limit"):
        await ask(strict)


def test_expired_halt_clears_itself():
    g = cs.UsageGuard()
    g.halt("limit", until=1.0)  # in the past
    g.check()
    assert not g.snapshot()["halted"]


@pytest.mark.parametrize(
    "result, exc, fatal",
    [
        (
            ResultMessage(is_error=True, result="Claude AI usage limit reached", api_error_status=429),
            LLMUsageLimit,
            True,
        ),
        (ResultMessage(is_error=True, result="Invalid API key · Please run /login"), LLMAuthError, True),
        (ResultMessage(is_error=True, result="Credit balance is too low"), LLMUsageLimit, True),
        (ResultMessage(is_error=True, result="API Error: Overloaded", api_error_status=529), LLMError, False),
        (
            ResultMessage(is_error=True, subtype="error_max_turns", errors=["max turns"]),
            LLMOutputError,
            False,
        ),
        (ResultMessage(is_error=True, subtype="error_during_execution", errors=["boom"]), LLMError, False),
    ],
)
async def test_error_results_are_classified(tmp_path, result, exc, fatal):
    client = client_with(Factory(lambda spec: [result]), tmp_path)
    with pytest.raises(exc) as ei:
        await ask(client)
    assert ei.value.fatal is fatal


async def test_overloaded_is_transient(tmp_path):
    result = ResultMessage(is_error=True, result="API Error: Overloaded", api_error_status=529)
    client = client_with(Factory(lambda spec: [result]), tmp_path)
    with pytest.raises(LLMError) as ei:
        await ask(client)
    assert ei.value.transient and not ei.value.fatal


async def test_assistant_error_field(tmp_path):
    msg = AssistantMessage([TextBlock("OAuth token has expired")], "claude-x", error="authentication_failed")
    client = client_with(Factory(lambda spec: [msg]), tmp_path)
    with pytest.raises(LLMAuthError, match="/login"):
        await ask(client)


async def test_missing_result_is_transient(tmp_path):
    client = client_with(Factory(lambda spec: [AssistantMessage([TextBlock("hi")], "m")]), tmp_path)
    with pytest.raises(LLMError) as ei:
        await ask(client)
    assert ei.value.transient


async def test_sdk_exception_mapping():
    class CLINotFoundError(Exception):
        pass

    class ProcessError(Exception):
        stderr = "Error: not logged in"

    assert isinstance(cs._map_exception(CLINotFoundError("x")), LLMConfigError)
    assert isinstance(cs._map_exception(ProcessError("exit 1")), LLMAuthError)


# ----------------------------------------------------------------------------- budgets
async def test_call_budget_per_run(tmp_path):
    factory = Factory()
    client = client_with(factory, tmp_path, claude_max_calls_per_run=2)
    await ask(client)
    await ask(client)
    with pytest.raises(LLMBudgetExceeded):
        await ask(client)
    assert len(factory.sessions) == 2


async def test_parallel_sessions_are_capped(tmp_path):
    active = peak = 0

    class Counting(FakeSession):
        async def connect(self):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            return await super().connect()

        async def close(self):
            nonlocal active
            active -= 1
            await super().close()

    sessions = []

    def factory(spec):
        s = Counting(spec, lambda _s: ok(), delay=0.05)
        sessions.append(s)
        return s

    client = client_with(factory, tmp_path, claude_max_parallel=2)
    await asyncio.gather(*(ask(client) for _ in range(6)))
    assert len(sessions) == 6 and peak == 2


async def test_timeout_closes_session(tmp_path):
    factory = Factory(hang=True)
    client = client_with(factory, tmp_path, claude_timeout_s=0.1)
    with pytest.raises(LLMTimeout):
        await ask(client)
    assert factory.sessions[0].closed


# ----------------------------------------------------------------------------- engine integration
def patched_engine(monkeypatch, settings, factory, guard=None) -> Engine:
    guard = guard or cs.UsageGuard()

    def make(mode, s=None, time_scale=0.0):
        if mode == "mock":
            return MockLLM(time_scale=time_scale)
        return cs.ClaudeAgentSDKLLM(settings, session_factory=factory, guard=guard)

    monkeypatch.setattr("famas.orchestration.engine.create_llm", make)
    return Engine(settings, stats=AgentStatsStore())


async def test_disabled_provider_fails_run_before_planning(tmp_path, no_sdk_sessions):
    engine = Engine(claude_settings(tmp_path), stats=AgentStatsStore())
    out = await engine.run(MEDIUM, RunConfig(mode="live"))
    assert out.status == "failed" and "LLMConfigError" in out.error and "disabled" in out.error
    types = [e.type for e in out.events]
    assert "plan_created" not in types and "task_started" not in types


async def test_full_run_with_replanning_through_provider(monkeypatch, tmp_path):
    """LangGraph workflow + DAG dispatch + incremental replanning, completions from the provider."""
    factory = Factory(from_mock)
    settings = claude_settings(tmp_path, claude_dry_run=True)  # dry run: mock replies are computed
    engine = patched_engine(monkeypatch, settings, factory)
    out = await engine.run(REQUEST, RunConfig(mode="live", scenario="cascade"))
    assert out.status == "completed", out.error
    assert out.metrics.recovery_rounds >= 1 and out.metrics.reused_results > 0
    assert {"plan_created", "replan_applied", "run_completed"} <= {e.type for e in out.events}
    components = {s.spec.component for s in factory.sessions}
    assert "analyzer" in components and any(c.startswith("agent:") for c in components)
    assert all(s.closed for s in factory.sessions)
    assert out.final_output.startswith("# Technical comparison")
    assert out.metrics.cost > 0 and out.metrics.total_tokens > 0


async def test_llm_planner_through_provider(monkeypatch, tmp_path):
    factory = Factory(from_mock)
    engine = patched_engine(monkeypatch, claude_settings(tmp_path, claude_dry_run=True), factory)
    out = await engine.run(REQUEST, RunConfig(mode="live", planner="llm"))
    assert out.status == "completed", out.error
    planner = [s for s in factory.sessions if s.spec.component == "planner"]
    assert len(planner) == 1 and planner[0].spec.json_schema["title"] == "PlanSchema"


async def test_usage_limit_mid_run_aborts_instead_of_replanning(monkeypatch, tmp_path):
    def script(spec):
        if spec.component.startswith("agent:research"):
            return [RateLimitEvent(RateLimitInfo("rejected", "five_hour", resets_at=4102444800))]
        return from_mock(spec)

    factory = Factory(script)
    guard = cs.UsageGuard()
    engine = patched_engine(monkeypatch, claude_settings(tmp_path, claude_dry_run=True), factory, guard)
    out = await engine.run(REQUEST, RunConfig(mode="live"))
    assert out.status == "failed" and "usage limit" in (out.error or "")
    assert out.metrics.recovery_rounds <= 1
    research_sessions = [s for s in factory.sessions if s.spec.component.startswith("agent:research")]
    assert 1 <= len(research_sessions) <= 2  # max_parallel=2: no retries, reassignments or restarts
    diag = [e for e in out.events if e.type == "failure_diagnosed"]
    assert diag and all(e.data["diagnosis"]["strategy"] == "abort" for e in diag)
    assert guard.snapshot()["halted"]


async def test_fatal_errors_abort_baselines_too(monkeypatch, tmp_path):
    def script(spec):
        if spec.component.startswith("agent:"):
            return [ResultMessage(is_error=True, result="Invalid API key · Please run /login")]
        return from_mock(spec)

    for system in ("full_replan", "fixed_workflow", "naive_incremental"):
        factory = Factory(script)
        engine = patched_engine(monkeypatch, claude_settings(tmp_path, claude_dry_run=True), factory)
        out = await engine.run(MEDIUM, RunConfig(mode="live", system=system))
        assert out.status == "failed", system
        agent_sessions = [s for s in factory.sessions if s.spec.component.startswith("agent:")]
        assert len(agent_sessions) <= 2, system


async def test_mock_mode_unaffected_by_claude_provider(tmp_path, no_sdk_sessions):
    engine = Engine(claude_settings(tmp_path), stats=AgentStatsStore())
    out = await engine.run(MEDIUM, RunConfig(scenario="corruption"))
    assert out.status == "completed" and out.metrics.recovery_rounds == 1


# ----------------------------------------------------------------------------- API
def test_api_reports_provider_status(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from famas.api.app import create_app
    from famas.persistence.store import Store

    s = claude_settings(tmp_path, frontend_dist=str(tmp_path / "none"))
    with TestClient(create_app(s, Store("sqlite:///:memory:")), base_url="http://localhost") as c:
        h = c.get("/api/health").json()
        assert h["llm_provider"] == CLAUDE_PROVIDER and h["live_available"] is False
        assert h["llm"]["real_calls_allowed"] is False and h["llm"]["conflicting_env"] == []
        assert ("disabled" if cs.sdk_installed() else "not installed") in h["live_reason"]
        assert c.get("/api/meta").json()["live_reason"] == h["live_reason"]
        r = c.post("/api/runs", json={"request": MEDIUM, "mode": "live"})
        assert r.status_code == 400 and r.json()["detail"].startswith("live mode is not available")
        assert c.post("/api/runs", json={"request": MEDIUM, "simulated": True}).status_code == 201


# ----------------------------------------------------------------------------- real SDK, scripted transport
@pytest.fixture
def scripted():
    pytest.importorskip("claude_agent_sdk")
    from famas.llm import claude_sdk_scripted

    return claude_sdk_scripted


def scripted_factory(scripted, responder, account=None):
    transports = []

    def factory(spec):
        t = scripted.ScriptedTransport(responder, account=account)
        transports.append(t)
        return cs.SDKSession(spec, transport=t)

    return factory, transports


def test_options_are_tool_less_and_isolated(scripted, tmp_path):
    spec = cs.CallSpec(
        component="c",
        system="SYS",
        prompt="p",
        model="haiku",
        tier="small",
        json_schema=Pong.model_json_schema(),
        schema_mode="native",
        max_turns=3,
        max_budget_usd=0.5,
        cwd=str(tmp_path),
        cli_path=None,
    )
    o = cs.build_options(spec, environ={})
    assert o.tools == [] and o.allowed_tools == [] and o.setting_sources == [] and o.skills == []
    assert o.permission_mode == "dontAsk" and o.system_prompt == "SYS" and o.model == "haiku"
    assert o.output_format == {"type": "json_schema", "schema": Pong.model_json_schema()}
    assert o.max_turns == 3 and o.max_budget_usd == 0.5 and str(o.cwd) == str(tmp_path)
    assert not o.env  # FA-MAS passes no credentials to Claude Code
    blanked = cs.build_options(
        spec, environ={"GITHUB_TOKEN": "test-only-not-a-real-token", "PATH": "/bin"}
    ).env
    assert blanked == {"GITHUB_TOKEN": ""}  # FA-MAS's own secrets are not inherited by Claude Code
    spec.schema_mode = "prompt"
    assert cs.build_options(spec).output_format is None


async def test_real_sdk_round_trip(scripted, tmp_path):
    def responder(prompt):
        return scripted.reply_frames(
            text="pong", model="claude-haiku-test", prompt=prompt, total_cost_usd=0.001
        )

    factory, transports = scripted_factory(scripted, responder)
    client = client_with(factory, tmp_path)
    resp = await ask(client)
    assert resp.text == "pong" and resp.usage.model == "claude-haiku-test" and resp.usage.cost == 0.001
    t = transports[0]
    assert t.control_requests[0]["request"]["subtype"] == "initialize"
    assert t.prompts == ["ping"] and not t.is_ready()  # closed after the call


async def test_real_sdk_structured_output(scripted, tmp_path):
    def responder(prompt):
        return scripted.reply_frames(text="", model="m", structured_output={"ok": True, "word": "pong"})

    factory, _ = scripted_factory(scripted, responder)
    resp = await ask(client_with(factory, tmp_path), schema=Pong)
    assert resp.parsed == Pong(ok=True, word="pong")


async def test_real_sdk_refuses_api_key_before_prompt(scripted, tmp_path):
    factory, transports = scripted_factory(
        scripted,
        lambda p: scripted.reply_frames(text="should never be produced", model="m"),
        account={"apiProvider": "firstParty", "apiKeySource": "ANTHROPIC_API_KEY"},
    )
    with pytest.raises(LLMAuthError):
        await ask(client_with(factory, tmp_path))
    assert transports[0].prompts == []  # the user message was never written to Claude Code


async def test_real_sdk_rate_limit_event(scripted, tmp_path):
    def responder(prompt):
        return [
            scripted.rate_limit_frame(
                "s1", status="rejected", rateLimitType="five_hour", resetsAt=4102444800
            ),
            *scripted.reply_frames(text="late", model="m", session_id="s1"),
        ]

    factory, _ = scripted_factory(scripted, responder)
    client = client_with(factory, tmp_path)
    with pytest.raises(LLMUsageLimit, match="five_hour"):
        await ask(client)
    assert client.guard.snapshot()["last_event"]["status"] == "rejected"


async def test_real_sdk_overage_event(scripted, tmp_path):
    def responder(prompt):
        return [
            scripted.rate_limit_frame("s1", status="allowed", rateLimitType="five_hour", isUsingOverage=True),
            *scripted.reply_frames(text="x", model="m", session_id="s1"),
        ]

    factory, _ = scripted_factory(scripted, responder)
    with pytest.raises(LLMUsageLimit, match="extra usage"):
        await ask(client_with(factory, tmp_path))


async def test_real_sdk_error_result(scripted, tmp_path):
    def responder(prompt):
        return [
            scripted.result_frame(
                "s1", result="Claude AI usage limit reached", is_error=True, api_error_status=429
            )
        ]

    factory, _ = scripted_factory(scripted, responder)
    with pytest.raises(LLMUsageLimit):
        await ask(client_with(factory, tmp_path))


async def test_dry_run_engine_through_real_sdk(scripted, tmp_path):
    """Default dry-run wiring: real ClaudeSDKClient + scripted transport, full workflow."""
    engine = Engine(claude_settings(tmp_path, claude_dry_run=True), stats=AgentStatsStore())
    out = await engine.run(REQUEST, RunConfig(mode="live", scenario="corruption"))
    assert out.status == "completed", out.error
    assert out.metrics.recovery_rounds == 1 and out.metrics.reused_results > 0
    assert out.metrics.total_tokens > 0


def test_cli_doctor_and_smoke_offline(scripted, tmp_path, monkeypatch, capsys):
    from famas.cli import main

    monkeypatch.setenv("FAMAS_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("FAMAS_LLM_PROVIDER", "claude")
    monkeypatch.setenv("FAMAS_CLAUDE_DRY_RUN", "true")
    monkeypatch.setenv("FAMAS_CLAUDE_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    get_settings.cache_clear()
    try:
        assert main(["claude", "smoke"]) == 0
        out = capsys.readouterr().out
        assert "pong" in out and "structured" in out
        main(["claude", "doctor", "--offline-test"])
        out = capsys.readouterr().out
        assert "offline SDK round trip" in out and "no model calls" in out
        monkeypatch.setenv("FAMAS_CLAUDE_DRY_RUN", "false")
        get_settings.cache_clear()
        assert main(["claude", "smoke"]) == 2  # real calls disabled: refuses
        monkeypatch.setenv("FAMAS_CLAUDE_ALLOW_REAL_CALLS", "true")
        get_settings.cache_clear()
        assert main(["claude", "smoke"]) == 2  # enabled but not confirmed with --yes: refuses
    finally:
        get_settings.cache_clear()


# ----------------------------------------------------------------------------- individual-use guardrails
def test_profile_and_federation_env_are_refused():
    assert cs.conflicting_env({"ANTHROPIC_PROFILE": "work"}) == ["ANTHROPIC_PROFILE"]
    assert cs.conflicting_env({"ANTHROPIC_FEDERATION_RULE_ID": "r"}) == ["ANTHROPIC_FEDERATION_RULE_ID"]


def test_cors_and_host_guards(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from famas.api.app import create_app
    from famas.persistence.store import Store

    def preflight(settings, origin="https://evil.example"):
        with TestClient(create_app(settings, Store("sqlite:///:memory:")), base_url="http://localhost") as c:
            return c.options(
                "/api/runs", headers={"Origin": origin, "Access-Control-Request-Method": "POST"}
            ).headers.get("access-control-allow-origin")

    base = {"frontend_dist": str(tmp_path / "none")}
    # default: only the local UI may call the API
    assert preflight(Settings(**base)) is None
    assert preflight(Settings(**base), "http://localhost:5173") == "http://localhost:5173"
    # an explicit wildcard is honoured for mock-only servers...
    assert preflight(Settings(cors_origins="*", **base)) == "*"
    # ...but never while a live provider could spend credit or subscription usage
    for live in (
        claude_settings(tmp_path, cors_origins="*", claude_allow_real_calls=True, **base),
        Settings(cors_origins="*", llm_provider="openai", llm_api_key="sk-test", **base),
    ):
        assert preflight(live) is None
        assert preflight(live, "http://127.0.0.1:8000") == "http://127.0.0.1:8000"

    # DNS rebinding: requests addressed to a foreign Host header are rejected
    app = create_app(Settings(**base), Store("sqlite:///:memory:"))
    with TestClient(app, base_url="http://localhost") as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/health", headers={"Host": "attacker.example"}).status_code == 400
        assert c.get("/api/health", headers={"Host": "127.0.0.1:8000"}).status_code == 200


def test_serve_refuses_non_loopback_with_real_claude_calls(monkeypatch, tmp_path, capsys):
    from famas.cli import main

    monkeypatch.setenv("FAMAS_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("FAMAS_LLM_PROVIDER", "claude_agent_sdk")
    monkeypatch.setenv("FAMAS_CLAUDE_ALLOW_REAL_CALLS", "true")
    get_settings.cache_clear()
    try:
        assert main(["serve", "--host", "0.0.0.0"]) == 2
        assert "personal Claude subscription" in capsys.readouterr().out
    finally:
        get_settings.cache_clear()


# ----------------------------------------------------------------------------- validation tests A/B/C (dry run)
@pytest.mark.parametrize("test, prompts", [("a", 2), ("b", 5), ("c", 6)])
def test_validation_plans_run_offline(scripted, tmp_path, capsys, test, prompts):
    from famas.claude_validation import run_validation

    s = claude_settings(tmp_path, claude_dry_run=True)
    assert run_validation(test, s, yes=False, out_dir=str(tmp_path / "val")) == 0
    reports = list((tmp_path / "val").glob(f"*-test-{test}.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report["real_calls"] is False and report["totals"]["prompts_sent"] == prompts
    assert report["outcome"]["status"] == "completed"
    assert all(c["billed_to"] == "nothing (dry run)" for c in report["calls"])
    raw = reports[0].read_text()
    # metadata only: no prompts, no model output, no session ids
    assert "Reply with exactly" not in raw and "Research subject" not in raw and "session_id" not in raw
    usage_lines = (tmp_path / "val").glob(f"*-test-{test}.usage.jsonl")
    assert sum(len(p.read_text().splitlines()) for p in usage_lines) == prompts
    if test == "c":
        assert report["outcome"]["recovery_rounds"] == 1 and report["outcome"]["reused_results"] >= 1
    # call accounting: run metrics reconcile with the prompts that reached the provider
    acc = report["accounting"]
    if test == "a":
        assert acc is None
    else:
        assert acc["consistent"] and acc["model_calls"] == prompts
        # Test C's injected timeout is charged as one simulated call that sends no prompt
        assert acc["simulated_llm_calls"] == (1 if test == "c" else 0)
        assert acc["llm_calls"] == prompts + acc["simulated_llm_calls"]
        assert "run metrics:" in capsys.readouterr().out


def test_validation_refuses_real_calls_without_both_switches(tmp_path, capsys, no_sdk_sessions):
    from famas.claude_validation import run_validation

    out = str(tmp_path / "val")
    assert run_validation("a", claude_settings(tmp_path), yes=True, out_dir=out) == 2
    assert "real calls are disabled" in capsys.readouterr().out
    enabled = claude_settings(tmp_path, claude_allow_real_calls=True)
    assert run_validation("b", enabled, yes=False, out_dir=out) == 2
    assert "--yes" in capsys.readouterr().out
    assert not (tmp_path / "val").exists() or not list((tmp_path / "val").iterdir())


def test_validation_caps_only_tighten(tmp_path):
    from famas.claude_validation import PLANS, capped_settings

    loose = claude_settings(
        tmp_path, claude_max_calls_per_run=100, claude_max_parallel=8, claude_timeout_s=900
    )
    s = capped_settings(loose, PLANS["b"])
    assert (s.claude_max_calls_per_run, s.claude_max_parallel, s.claude_timeout_s) == (12, 2, 180.0)
    tight = claude_settings(tmp_path, claude_max_calls_per_run=3, claude_max_parallel=1)
    s = capped_settings(tight, PLANS["c"])
    assert (s.claude_max_calls_per_run, s.claude_max_parallel) == (3, 1)


async def test_usage_records_hold_metadata_only(tmp_path):
    log = tmp_path / "usage.jsonl"
    client = client_with(
        Factory(lambda spec: ok(text="secret model output")), tmp_path, claude_usage_log=str(log)
    )
    await ask(client)
    failing = client_with(
        Factory(lambda spec: [RateLimitEvent(RateLimitInfo("rejected", "five_hour", resets_at=4102444800))]),
        tmp_path,
        claude_usage_log=str(log),
    )
    with pytest.raises(LLMUsageLimit):
        await ask(failing)
    lines = [json.loads(x) for x in log.read_text().splitlines()]
    assert [r["outcome"] for r in lines] == ["ok", "LLMUsageLimit"]
    assert lines[0]["input_tokens"] == 10 and lines[0]["api_equivalent_cost_usd"] == pytest.approx(0.0123)
    assert lines[0]["cost_source"] == "claude_code_estimate" and lines[0]["prompt_sent"] is True
    assert lines[1]["rate_limit"]["status"] == "rejected"
    assert "secret model output" not in log.read_text() and "ping" not in log.read_text()
