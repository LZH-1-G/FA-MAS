"""Claude Agent SDK provider: FA-MAS agents backed by Claude Code and a Claude subscription login.

Every ``LLMClient.complete`` call becomes one short, tool-less Claude Code
session driven through the official ``claude-agent-sdk`` package
(``ClaudeSDKClient``). LangGraph, the DAG dispatcher and incremental
replanning are untouched: this module only replaces *where completions come
from*.

Billing and authentication safety (see docs/claude-agent-sdk.md):

* **Off by default.** No model is called unless ``FAMAS_CLAUDE_ALLOW_REAL_CALLS``
  is true. ``FAMAS_CLAUDE_DRY_RUN`` replays deterministic replies through the
  real SDK with an offline transport instead (no process, no network).
* **Subscription only.** FA-MAS never reads, stores or forwards credentials;
  Claude Code authenticates itself with the login the user created through
  Anthropic's own ``/login`` flow. Before a session starts, the provider
  refuses to run if any environment variable would make Claude Code bill an
  API key or a cloud provider instead (``ANTHROPIC_API_KEY``,
  ``ANTHROPIC_AUTH_TOKEN``, ``ANTHROPIC_BASE_URL``, ``CLAUDE_CODE_USE_*``).
  After the CLI starts - but *before the prompt is sent* - it checks the
  ``account`` block of the initialize handshake and refuses if Claude Code
  reports an API-key credential, a non-Anthropic backend, or no subscription.
* **No extra usage.** Rate-limit events are watched on every call. A rejected
  request, or any sign that requests are being served from pay-as-you-go extra
  usage ("overage"), stops the call and halts the provider process-wide; the
  run then aborts instead of replanning. FA-MAS never changes account or
  billing settings.
* **Bounded.** Per-run call budget, process-wide concurrency limit, per-call
  timeout, per-call turn limit and the SDK's own per-call budget cap.

The SDK is an optional dependency (``pip install 'fa-mas[claude]'``); it is
imported lazily so mock mode and the other providers never need it.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import time
import weakref
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from famas.config import CLAUDE_PROVIDER, Settings
from famas.llm.base import (
    LLMAuthError,
    LLMBudgetExceeded,
    LLMClient,
    LLMConfigError,
    LLMError,
    LLMOutputError,
    LLMResponse,
    LLMTimeout,
    LLMUsage,
    LLMUsageLimit,
    MockFn,
    MockReply,
    Tier,
    estimate_tokens,
    extract_json,
)
from famas.llm.pricing import cost_of

# Environment variables that make Claude Code authenticate with something other than the
# user's Claude subscription login (API billing, a gateway, a Console/federation profile or a
# cloud provider account). See "Authentication precedence" in the Claude Code docs.
BILLING_OVERRIDE_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_PROFILE",
    "ANTHROPIC_FEDERATION_RULE_ID",
)
CLOUD_PROVIDER_ENV = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
# Long-lived subscription tokens created by the user with `claude setup-token` (Anthropic's flow).
SUBSCRIPTION_TOKEN_SOURCES = ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR")
INSTALL_HINT = "install the optional dependency: pip install 'fa-mas[claude]'"
LOGIN_HINT = "run `claude` in a terminal and sign in with /login using your Claude account"


# --------------------------------------------------------------------------- environment checks
def _truthy(value: str | None) -> bool:
    return bool(value) and value.strip().lower() not in ("0", "false", "no", "off")


def conflicting_env(environ: Mapping[str, str] | None = None) -> list[str]:
    """Names (never values) of variables that would bypass the subscription login."""
    env = os.environ if environ is None else environ
    names = [k for k in BILLING_OVERRIDE_ENV if (env.get(k) or "").strip()]
    names += [k for k in CLOUD_PROVIDER_ENV if _truthy(env.get(k))]
    return names


def sdk_installed() -> bool:
    return importlib.util.find_spec("claude_agent_sdk") is not None


def sdk_version() -> str | None:
    if not sdk_installed():
        return None
    try:
        from importlib.metadata import version

        return version("claude-agent-sdk")
    except Exception:  # noqa: BLE001 - metadata missing in unusual installs
        try:
            import claude_agent_sdk

            return getattr(claude_agent_sdk, "__version__", None)
        except Exception:  # noqa: BLE001
            return None


def provider_status(settings: Settings, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Configuration state of the provider. Makes no model call and starts no process."""
    conflicts = conflicting_env(environ)
    installed = sdk_installed()
    if settings.claude_auth != "subscription":
        available, reason = (
            False,
            f"FAMAS_CLAUDE_AUTH={settings.claude_auth!r} is not supported; only 'subscription' "
            "(use FAMAS_LLM_PROVIDER=anthropic for API-key billing)",
        )
    elif not installed:
        available, reason = False, f"claude-agent-sdk is not installed ({INSTALL_HINT})"
    elif settings.claude_dry_run:
        available, reason = True, "dry run: scripted Claude Agent SDK responses, no model calls"
    elif not settings.claude_allow_real_calls:
        available, reason = (
            False,
            "real Claude calls are disabled; set FAMAS_CLAUDE_DRY_RUN=true to try the integration "
            "offline, or FAMAS_CLAUDE_ALLOW_REAL_CALLS=true once you approve real calls",
        )
    elif conflicts:
        available, reason = (
            False,
            f"{', '.join(conflicts)} is set: Claude Code would not use your subscription; unset it",
        )
    else:
        available, reason = True, "real calls enabled: Claude Code with your Claude subscription login"
    guard = USAGE_GUARD.snapshot()
    if available and guard["halted"]:
        available, reason = False, f"halted: {guard['reason']}"
    return {
        "provider": CLAUDE_PROVIDER,
        "live_available": available,
        "reason": reason,
        "dry_run": settings.claude_dry_run,
        "real_calls_allowed": settings.claude_allow_real_calls and not settings.claude_dry_run,
        "auth": settings.claude_auth,
        "sdk_installed": installed,
        "conflicting_env": conflicts,
        "models": {"large": settings.claude_model, "small": settings.claude_small_model},
        "limits": {
            "max_calls_per_run": settings.claude_max_calls_per_run,
            "max_parallel": settings.claude_max_parallel,
            "max_turns": settings.claude_max_turns,
            "timeout_s": settings.claude_timeout_s,
            "max_budget_usd_per_call": settings.claude_max_budget_usd_per_call,
        },
        "usage_guard": guard,
    }


# --------------------------------------------------------------------------- account verification
def verify_subscription_account(server_info: Mapping[str, Any] | None) -> dict[str, Any]:
    """Check the initialize handshake's ``account`` block before any prompt is sent.

    Returns a non-secret summary. Raises ``LLMAuthError`` (fatal) when Claude Code
    would bill something other than the user's Claude subscription. Fails closed:
    a CLI that does not report its credential source is refused.
    """
    account = (server_info or {}).get("account")
    if not isinstance(account, Mapping):
        raise LLMAuthError(
            "Claude Code did not report which credentials it uses, so FA-MAS cannot confirm that "
            "your subscription (not an API key) would be used; upgrade claude-agent-sdk"
        )
    provider = account.get("apiProvider")
    if provider != "firstParty":
        raise LLMAuthError(
            f"Claude Code is configured for the {provider or 'unknown'} backend, not your Claude "
            "subscription; unset CLAUDE_CODE_USE_* / gateway settings"
        )
    key_source = account.get("apiKeySource")
    if key_source not in (None, "", "none"):
        raise LLMAuthError(
            f"Claude Code would authenticate with an API key (source: {key_source}), which is billed "
            "at API rates; FA-MAS subscription mode refuses. Remove the key "
            "(env var, apiKeyHelper or Console login) and use your Claude.ai login"
        )
    plan = account.get("subscriptionType")
    token_source = account.get("tokenSource")
    if not plan and token_source not in SUBSCRIPTION_TOKEN_SOURCES:
        detail = f" (token source: {token_source})" if token_source else ""
        raise LLMAuthError(f"Claude Code is not signed in with a Claude subscription{detail}; {LOGIN_HINT}")
    return {
        "subscription_type": plan,
        "token_source": token_source or "claude.ai login",
        "api_provider": provider,
    }


# --------------------------------------------------------------------------- usage guard
@dataclass
class UsageGuard:
    """Process-wide circuit breaker fed by Claude Code's rate-limit events.

    Once tripped, every call fails fast (without starting Claude Code) until the
    reported reset time; overage trips it until the process restarts or
    ``reset()`` is called.
    """

    halted_reason: str | None = None
    halted_until: float | None = None  # unix time; None + reason = until reset()
    last_event: dict[str, Any] | None = None
    events_seen: int = 0

    def check(self) -> None:
        if self.halted_reason is None:
            return
        if self.halted_until is not None and time.time() >= self.halted_until:
            self.reset()
            return
        raise LLMUsageLimit(self.halted_reason)

    def halt(self, reason: str, until: float | None) -> None:
        self.halted_reason, self.halted_until = reason, until

    def reset(self) -> None:
        self.halted_reason = None
        self.halted_until = None

    def observe(self, info: Any, stop_on_warning: bool = False) -> None:
        """Record a ``RateLimitInfo``; raise ``LLMUsageLimit`` if the run must stop."""
        raw = dict(getattr(info, "raw", None) or {})
        status = getattr(info, "status", None) or raw.get("status")
        limit_type = getattr(info, "rate_limit_type", None) or raw.get("rateLimitType")
        resets_at = getattr(info, "resets_at", None) or raw.get("resetsAt")
        utilization = getattr(info, "utilization", None)
        if utilization is None:
            utilization = raw.get("utilization")
        overage = (
            raw.get("isUsingOverage") is True
            or raw.get("overageInUse") is True
            or (isinstance(limit_type, str) and "overage" in limit_type)
        )
        self.events_seen += 1
        self.last_event = {
            "status": status,
            "rate_limit_type": limit_type,
            "utilization": utilization,
            "resets_at": resets_at,
            "overage": overage,
            "seen_at": time.time(),
        }
        if overage:
            reason = (
                "Claude reported that requests are being served from extra usage (billed separately); "
                "FA-MAS stopped. Keep extra usage turned off in your Claude settings"
            )
            self.halt(reason, None)
            raise LLMUsageLimit(reason)
        if status == "rejected":
            when = (
                f" (resets at {time.strftime('%Y-%m-%d %H:%M', time.localtime(resets_at))})"
                if resets_at
                else ""
            )
            reason = f"Claude subscription usage limit reached ({limit_type or 'unknown window'}){when}"
            self.halt(reason, float(resets_at) if resets_at else time.time() + 300)
            raise LLMUsageLimit(reason)
        if status == "allowed_warning" and stop_on_warning:
            reason = (
                f"Claude reports you are close to your usage limit ({limit_type or 'unknown window'}); "
                "stopping because FAMAS_CLAUDE_STOP_ON_LIMIT_WARNING is set"
            )
            self.halt(reason, float(resets_at) if resets_at else None)
            raise LLMUsageLimit(reason)

    def snapshot(self) -> dict[str, Any]:
        return {
            "halted": self.halted_reason is not None,
            "reason": self.halted_reason,
            "halted_until": self.halted_until,
            "last_event": self.last_event,
            "events_seen": self.events_seen,
        }


USAGE_GUARD = UsageGuard()


def reset_usage_guard() -> None:
    USAGE_GUARD.reset()
    USAGE_GUARD.last_event = None
    USAGE_GUARD.events_seen = 0


# --------------------------------------------------------------------------- error mapping
_ASSISTANT_ERRORS: dict[str, Callable[[str], LLMError]] = {
    "authentication_failed": lambda m: LLMAuthError(f"Claude authentication failed: {m}; {LOGIN_HINT}"),
    "billing_error": lambda m: LLMUsageLimit(f"Claude reported a billing error: {m}"),
    "rate_limit": lambda m: LLMUsageLimit(f"Claude usage/rate limit: {m}"),
    "invalid_request": lambda m: LLMError(f"Claude rejected the request: {m}"),
}


def _transient(msg: str) -> LLMError:
    err = LLMError(msg)
    err.transient = True
    return err


def classify_failure(text: str, status: int | None = None, subtype: str | None = None) -> LLMError:
    """Map a Claude Code error (result frame or process error) onto FA-MAS error classes."""
    t = (text or "").lower()
    if status in (401, 403) or any(
        k in t
        for k in ("not logged in", "/login", "invalid api key", "authentication", "unauthorized", "oauth")
    ):
        return LLMAuthError(f"Claude Code could not authenticate: {text[:300]}; {LOGIN_HINT}")
    if "credit balance" in t or "billing" in t or "extra usage" in t:
        return LLMUsageLimit(f"Claude refused the request for billing reasons: {text[:300]}")
    if status == 429 or any(k in t for k in ("usage limit", "rate limit", "limit reached", "rate_limit")):
        return LLMUsageLimit(f"Claude usage limit: {text[:300]}")
    if subtype == "error_max_budget_usd":
        return LLMError(f"call exceeded FAMAS_CLAUDE_MAX_BUDGET_USD_PER_CALL: {text[:200]}")
    if subtype == "error_max_turns":
        return LLMOutputError(f"Claude used up FAMAS_CLAUDE_MAX_TURNS without a final answer: {text[:200]}")
    if "structuredoutput" in t or "structured output" in t:
        return LLMOutputError(
            f"structured output failed ({text[:200]}); try FAMAS_CLAUDE_STRUCTURED_OUTPUT=prompt"
        )
    if status is not None and (status >= 500 or status == 529) or "overloaded" in t:
        return _transient(f"Claude API temporarily unavailable: {text[:300]}")
    return _transient(f"Claude Code error: {text[:300]}")


def _map_exception(exc: BaseException) -> LLMError:
    name = type(exc).__name__
    if isinstance(exc, LLMError):
        return exc
    if name == "CLINotFoundError":
        return LLMConfigError(f"Claude Code CLI not found ({exc}); {INSTALL_HINT}")
    if name == "ResultError":
        data = getattr(exc, "data", {}) or {}
        return classify_failure(
            "; ".join(getattr(exc, "errors", []) or []) or getattr(exc, "result", None) or str(exc),
            getattr(exc, "api_error_status", None) or data.get("api_error_status"),
            getattr(exc, "subtype", None),
        )
    if name == "ProcessError":
        return classify_failure(f"{exc} {getattr(exc, 'stderr', '') or ''}")
    if name in ("CLIJSONDecodeError", "MessageParseError", "CLIConnectionError"):
        return _transient(f"{name}: {exc}")
    return _transient(f"{name}: {exc}")


# --------------------------------------------------------------------------- sessions
@dataclass
class CallSpec:
    """Everything one Claude Code session needs. Contains no credentials."""

    component: str
    system: str
    prompt: str
    model: str
    tier: str
    json_schema: dict[str, Any] | None
    schema_mode: str | None  # None | "native" | "prompt"
    max_turns: int
    max_budget_usd: float | None
    cwd: str
    cli_path: str | None
    mock_reply: MockReply | None = None  # only used by dry runs


class ClaudeSession(Protocol):
    async def connect(self) -> Mapping[str, Any] | None: ...  # returns initialize server info

    async def send(self, prompt: str) -> None: ...

    def receive(self) -> AsyncIterator[Any]: ...

    async def close(self) -> None: ...


SessionFactory = Callable[[CallSpec], ClaudeSession]


# Secrets FA-MAS itself may hold that Claude Code never needs. The SDK passes the parent environment
# to the CLI; these are blanked for the child process (the session has no tools, this is defence in depth).
UNNEEDED_SECRET_ENV = ("GITHUB_TOKEN", "FAMAS_LLM_API_KEY", "OPENAI_API_KEY")


def build_options(spec: CallSpec, environ: Mapping[str, str] | None = None) -> Any:
    """``ClaudeAgentOptions`` for a tool-less, settings-isolated completion session."""
    from claude_agent_sdk import ClaudeAgentOptions

    env = os.environ if environ is None else environ

    return ClaudeAgentOptions(
        system_prompt=spec.system,  # replaces Claude Code's default system prompt
        model=spec.model,
        tools=[],  # no built-in tools: no shell, no file edits, no web access
        allowed_tools=[],
        permission_mode="dontAsk",  # anything not pre-approved is denied, never prompted
        setting_sources=[],  # ignore user/project settings files (hooks, CLAUDE.md, apiKeyHelper)
        skills=[],
        max_turns=spec.max_turns,
        max_budget_usd=spec.max_budget_usd,
        output_format=(
            {"type": "json_schema", "schema": spec.json_schema}
            if spec.json_schema is not None and spec.schema_mode == "native"
            else None
        ),
        cwd=spec.cwd,
        cli_path=spec.cli_path,
        env={name: "" for name in UNNEEDED_SECRET_ENV if env.get(name)},
    )


class SDKSession:
    """One ``ClaudeSDKClient`` connection: handshake, one prompt, one response."""

    def __init__(self, spec: CallSpec, transport: Any = None) -> None:
        try:
            from claude_agent_sdk import ClaudeSDKClient
        except ImportError as e:
            raise LLMConfigError(f"claude-agent-sdk is not installed; {INSTALL_HINT}") from e
        self.transport = transport
        self.client = ClaudeSDKClient(options=build_options(spec), transport=transport)

    async def connect(self) -> Mapping[str, Any] | None:
        await self.client.connect()  # starts Claude Code and runs the initialize handshake only
        return await self.client.get_server_info()

    async def send(self, prompt: str) -> None:
        await self.client.query(prompt)

    def receive(self) -> AsyncIterator[Any]:
        return self.client.receive_response()

    async def close(self) -> None:
        await self.client.disconnect()


def dry_run_session(spec: CallSpec) -> SDKSession:
    """Real SDK client wired to the offline scripted transport."""
    try:
        from famas.llm.claude_sdk_scripted import ScriptedTransport, dry_run_responder
    except ImportError as e:
        raise LLMConfigError(f"claude-agent-sdk is not installed; {INSTALL_HINT}") from e
    responder = dry_run_responder(
        component=spec.component,
        system=spec.system,
        model=spec.model,
        reply=spec.mock_reply,
        schema_mode=spec.schema_mode if spec.json_schema is not None else None,
    )
    return SDKSession(spec, transport=ScriptedTransport(responder))


_SEMAPHORES: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[int, asyncio.Semaphore]] = (
    weakref.WeakKeyDictionary()
)


def _semaphore(limit: int) -> asyncio.Semaphore:
    """Process-wide (per event loop) cap on concurrent Claude Code sessions."""
    loop = asyncio.get_running_loop()
    per_loop = _SEMAPHORES.setdefault(loop, {})
    if limit not in per_loop:
        per_loop[limit] = asyncio.Semaphore(max(1, limit))
    return per_loop[limit]


# --------------------------------------------------------------------------- diagnostics
def bundled_cli_path() -> str | None:
    """Path of the Claude Code CLI shipped inside the claude-agent-sdk wheel, if any."""
    spec = importlib.util.find_spec("claude_agent_sdk")
    if spec is None or not spec.origin:
        return None
    for name in ("claude", "claude.exe"):
        candidate = Path(spec.origin).parent / "_bundled" / name
        if candidate.exists():
            return str(candidate)
    return None


def user_settings_warnings(home: Path | None = None) -> list[str]:
    """Look (names only) for credential overrides in Claude Code's user settings file.

    FA-MAS sessions ignore settings files (``setting_sources=[]``), but these entries
    would still change how *your* interactive Claude Code bills, so they are reported.
    """
    path = (home or Path.home()) / ".claude" / "settings.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return [f"could not parse {path}"]
    warnings = []
    if isinstance(data, dict):
        if data.get("apiKeyHelper"):
            warnings.append(f"{path} defines apiKeyHelper (API-key billing in interactive Claude Code)")
        env = data.get("env") if isinstance(data.get("env"), dict) else {}
        for name in conflicting_env({k: str(v) for k, v in env.items()}):
            warnings.append(f"{path} sets {name} in its env block")
    return warnings


async def inspect_login(settings: Settings, session_factory: SessionFactory | None = None) -> dict[str, Any]:
    """Start Claude Code, complete the initialize handshake, read the account block, disconnect.

    No prompt is sent, so no model request is made. Raises ``LLMAuthError`` if the
    login is not a Claude subscription, ``LLMConfigError`` if the SDK is missing.
    """
    conflicts = conflicting_env()
    if conflicts and session_factory is None:
        raise LLMAuthError(f"{', '.join(conflicts)} is set; Claude Code would not use your subscription")
    workdir = Path(settings.claude_workdir).expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    spec = CallSpec(
        component="doctor",
        system="",
        prompt="",
        model=settings.claude_small_model,
        tier="small",
        json_schema=None,
        schema_mode=None,
        max_turns=1,
        max_budget_usd=settings.claude_max_budget_usd_per_call,
        cwd=str(workdir),
        cli_path=settings.claude_cli_path,
    )
    session = session_factory(spec) if session_factory is not None else SDKSession(spec)
    try:
        async with asyncio.timeout(60):
            info = await session.connect()
    except LLMError:
        raise
    except Exception as e:  # noqa: BLE001
        raise _map_exception(e) from e
    finally:
        await session.close()
    return verify_subscription_account(info)


# --------------------------------------------------------------------------- the client
@dataclass
class _Collected:
    text_parts: list[str] = field(default_factory=list)
    model: str | None = None
    result: Any = None
    prompt_sent: bool = False


class ClaudeAgentSDKLLM(LLMClient):
    """``LLMClient`` that runs each completion as a Claude Code session via the Agent SDK."""

    is_mock = False

    def __init__(
        self,
        settings: Settings,
        *,
        session_factory: SessionFactory | None = None,
        environ: Mapping[str, str] | None = None,
        guard: UsageGuard | None = None,
    ) -> None:
        self.settings = settings
        self.dry_run = settings.claude_dry_run
        self.name = f"claude-agent-sdk:{settings.claude_model}" + (" (dry run)" if self.dry_run else "")
        self._injected_factory = session_factory
        self._environ = environ
        self.guard = guard or USAGE_GUARD
        self.calls_made = 0  # per client = per run (Engine creates one client per run)
        self.account: dict[str, Any] | None = None
        self.records: list[dict[str, Any]] = []  # per-call usage metadata (see _record)
        if settings.claude_structured_output not in ("native", "prompt"):
            raise LLMConfigError("FAMAS_CLAUDE_STRUCTURED_OUTPUT must be 'native' or 'prompt'")

    # ------------------------------------------------------------ config
    @property
    def live_tools(self) -> bool:
        return not self.dry_run  # dry runs stay fully offline (fixtures instead of the GitHub API)

    def model_for(self, tier: Tier) -> str:
        return self.settings.claude_small_model if tier == "small" else self.settings.claude_model

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "mock": False,
            "dry_run": self.dry_run,
            "calls_made": self.calls_made,
            "account": self.account,
        }

    def _check_allowed(self) -> None:
        s = self.settings
        if s.claude_auth != "subscription":
            raise LLMConfigError(
                f"FAMAS_CLAUDE_AUTH={s.claude_auth!r} is not supported; this provider only uses a Claude "
                "subscription login (use FAMAS_LLM_PROVIDER=anthropic for API-key billing)"
            )
        if self._injected_factory is not None or self.dry_run:
            return  # scripted sessions: no process, no network, no model
        if not s.claude_allow_real_calls:
            raise LLMConfigError(
                "real Claude calls are disabled. Set FAMAS_CLAUDE_DRY_RUN=true to exercise the "
                "integration offline, or FAMAS_CLAUDE_ALLOW_REAL_CALLS=true to approve real calls "
                "(they count against your Claude subscription usage limits)"
            )
        conflicts = conflicting_env(self._environ)
        if conflicts:
            raise LLMAuthError(
                f"{', '.join(conflicts)} is set, so Claude Code would not use your Claude subscription "
                "(API-key or cloud billing). Unset it before running FA-MAS with this provider"
            )
        if not sdk_installed():
            raise LLMConfigError(f"claude-agent-sdk is not installed; {INSTALL_HINT}")

    async def preflight(self) -> None:
        self._check_allowed()
        if self.dry_run and self._injected_factory is None and not sdk_installed():
            raise LLMConfigError(f"dry run needs claude-agent-sdk; {INSTALL_HINT}")
        self.guard.check()

    def _session(self, spec: CallSpec) -> ClaudeSession:
        if self._injected_factory is not None:
            return self._injected_factory(spec)
        if self.dry_run:
            return dry_run_session(spec)
        return SDKSession(spec)

    def _workdir(self) -> str:
        path = Path(self.settings.claude_workdir).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    # ------------------------------------------------------------ completion
    async def complete(
        self,
        *,
        component: str,
        system: str,
        prompt: str,
        schema: type[BaseModel] | None = None,
        tier: Tier = "large",
        mock: MockFn | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        if tier == "none" and mock is not None:  # deterministic system step, no model needed
            reply = mock()
            return LLMResponse(text=reply.text, parsed=reply.parsed, usage=LLMUsage(model="none", calls=0))
        self._check_allowed()
        self.guard.check()
        if self.calls_made >= self.settings.claude_max_calls_per_run:
            raise LLMBudgetExceeded(
                f"FA-MAS reached FAMAS_CLAUDE_MAX_CALLS_PER_RUN={self.settings.claude_max_calls_per_run} "
                "Claude calls in this run; stopping instead of using more of your subscription"
            )

        model = self.model_for(tier)
        schema_mode = self.settings.claude_structured_output if schema is not None else None
        json_schema = schema.model_json_schema() if schema is not None else None
        full_prompt = prompt
        if schema is not None and schema_mode == "prompt":
            full_prompt = (
                prompt + "\n\nRespond ONLY with a JSON object that conforms to this JSON schema "
                "(no prose, no code fences):\n" + json.dumps(json_schema)
            )
        spec = CallSpec(
            component=component,
            system=system,
            prompt=full_prompt,
            model=model,
            tier=tier,
            json_schema=json_schema,
            schema_mode=schema_mode,
            max_turns=max(1, self.settings.claude_max_turns),
            max_budget_usd=self.settings.claude_max_budget_usd_per_call,
            cwd=self._workdir(),
            cli_path=self.settings.claude_cli_path,
            mock_reply=mock() if (self.dry_run and mock is not None) else None,
        )
        t0 = time.perf_counter()
        collected = _Collected()
        try:
            async with _semaphore(self.settings.claude_max_parallel):
                self.guard.check()  # another call may have tripped the guard while we waited
                try:
                    async with asyncio.timeout(self.settings.claude_timeout_s):
                        await self._run_session(spec, collected)
                except TimeoutError as e:
                    raise LLMTimeout(
                        f"Claude call for {component} exceeded "
                        f"FAMAS_CLAUDE_TIMEOUT_S={self.settings.claude_timeout_s}"
                    ) from e
            response = self._to_response(collected, spec, schema, (time.perf_counter() - t0) * 1000)
        except LLMError as e:
            self._record(spec, collected, None, e, (time.perf_counter() - t0) * 1000)
            raise
        self._record(spec, collected, response, None, response.usage.duration_ms)
        return response

    # ------------------------------------------------------------ usage records
    def _record(
        self,
        spec: CallSpec,
        collected: _Collected,
        response: LLMResponse | None,
        error: LLMError | None,
        duration_ms: float,
    ) -> None:
        """Keep one metadata record per call (never prompts, outputs, tokens or session ids)."""
        result = collected.result
        usage = (getattr(result, "usage", None) or {}) if result is not None else {}
        reported_cost = getattr(result, "total_cost_usd", None) if result is not None else None
        record: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "component": spec.component,
            "tier": spec.tier,
            "requested_model": spec.model,
            "model": collected.model,
            "dry_run": self.dry_run,
            "outcome": "ok" if error is None else type(error).__name__,
            "error": None if error is None else str(error)[:300],
            "prompt_sent": collected.prompt_sent,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
            "num_turns": getattr(result, "num_turns", None),
            "duration_ms": round(duration_ms, 1),
            "duration_api_ms": getattr(result, "duration_api_ms", None),
            # Claude Code's client-side estimate at API list prices. NOT a charge on a subscription.
            "api_equivalent_cost_usd": (
                reported_cost if reported_cost is not None else (response.usage.cost if response else None)
            ),
            "cost_source": (
                "claude_code_estimate"
                if reported_cost is not None
                else ("famas_price_table" if response else None)
            ),
            "billed_to": "nothing (dry run)" if self.dry_run else "Claude subscription usage limits",
            "subscription_type": (
                "simulated (dry run)" if self.dry_run else (self.account or {}).get("subscription_type")
            ),
            "rate_limit": self.guard.last_event,
        }
        self.records.append(record)
        path = self.settings.claude_usage_log
        if path:
            try:
                target = Path(path).expanduser()
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, default=str) + "\n")
            except OSError:  # usage logging must never break a run
                pass

    async def _run_session(self, spec: CallSpec, out: _Collected) -> _Collected:
        session = self._session(spec)
        try:
            server_info = await session.connect()  # handshake only: no prompt has been sent yet
            self.account = verify_subscription_account(server_info)
            self.calls_made += 1
            await session.send(spec.prompt)
            out.prompt_sent = True
            stream = session.receive()
            try:
                await self._consume(stream, out)
            finally:
                aclose = getattr(stream, "aclose", None)
                if aclose is not None:
                    await aclose()
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001 - SDK and process errors are mapped, not leaked
            raise _map_exception(e) from e
        finally:
            await session.close()
        if out.result is None:
            raise _transient("Claude Code ended the session without a result")
        return out

    async def _consume(self, stream: AsyncIterator[Any], out: _Collected) -> None:
        """Fold SDK messages into ``out``; stop at the result or at the first stop condition."""
        async for msg in stream:
            kind = type(msg).__name__
            if hasattr(msg, "rate_limit_info"):  # RateLimitEvent
                self.guard.observe(msg.rate_limit_info, self.settings.claude_stop_on_limit_warning)
            elif kind == "AssistantMessage" or (hasattr(msg, "content") and hasattr(msg, "model")):
                err = getattr(msg, "error", None)
                if err:
                    text = "".join(getattr(b, "text", "") or "" for b in msg.content)
                    maker = _ASSISTANT_ERRORS.get(err)
                    raise maker(text or err) if maker else classify_failure(text or err)
                out.model = getattr(msg, "model", None) or out.model
                for block in msg.content or []:
                    text = getattr(block, "text", None)
                    if isinstance(text, str):
                        out.text_parts.append(text)
            elif kind == "ResultMessage" or hasattr(msg, "is_error"):
                if msg.is_error:
                    errors = getattr(msg, "errors", None) or []
                    detail = "; ".join(e for e in errors if isinstance(e, str)) or (msg.result or "")
                    raise classify_failure(
                        detail or msg.subtype,
                        getattr(msg, "api_error_status", None),
                        msg.subtype,
                    )
                out.result = msg
                return

    def _to_response(
        self, c: _Collected, spec: CallSpec, schema: type[BaseModel] | None, duration_ms: float
    ) -> LLMResponse:
        result = c.result
        text = result.result if isinstance(result.result, str) and result.result else "".join(c.text_parts)
        parsed: Any = None
        if schema is not None:
            raw = getattr(result, "structured_output", None)
            try:
                if raw is not None:
                    parsed = schema.model_validate(raw)
                else:
                    parsed = schema.model_validate(extract_json(text))
            except (ValidationError, ValueError) as e:
                raise LLMOutputError(f"could not parse {schema.__name__} from Claude's reply: {e}") from e
            if not text:
                text = parsed.model_dump_json()
        usage = getattr(result, "usage", None) or {}
        tokens_in = int(usage.get("input_tokens") or 0)
        tokens_in += int(usage.get("cache_creation_input_tokens") or 0)
        tokens_in += int(usage.get("cache_read_input_tokens") or 0)
        tokens_out = int(usage.get("output_tokens") or 0)
        if not tokens_in:
            tokens_in = estimate_tokens(spec.system) + estimate_tokens(spec.prompt)
        if not tokens_out:
            tokens_out = estimate_tokens(text)
        # Claude Code reports an API-list-price *estimate*. On a subscription this is not what you are
        # charged; FA-MAS keeps it as an API-equivalent cost so runs stay comparable across providers.
        cost = getattr(result, "total_cost_usd", None)
        if cost is None:
            cost = cost_of(spec.model, tokens_in, tokens_out)
        return LLMResponse(
            text=text,
            parsed=parsed,
            usage=LLMUsage(
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost=float(cost),
                duration_ms=round(duration_ms, 2),
                model=c.model or spec.model,
            ),
        )


__all__ = [
    "BILLING_OVERRIDE_ENV",
    "CallSpec",
    "ClaudeAgentSDKLLM",
    "SDKSession",
    "USAGE_GUARD",
    "UsageGuard",
    "build_options",
    "bundled_cli_path",
    "inspect_login",
    "user_settings_warnings",
    "classify_failure",
    "conflicting_env",
    "dry_run_session",
    "provider_status",
    "reset_usage_guard",
    "sdk_installed",
    "sdk_version",
    "verify_subscription_account",
]
