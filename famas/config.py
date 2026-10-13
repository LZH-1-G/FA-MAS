"""Process-level settings, read from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


CLAUDE_PROVIDER = "claude_agent_sdk"
LOCAL_UI_ORIGINS = "http://localhost:8000,http://127.0.0.1:8000,http://localhost:5173,http://127.0.0.1:5173"
_PROVIDER_ALIASES = {
    "claude": CLAUDE_PROVIDER,
    "claude-agent-sdk": CLAUDE_PROVIDER,
    "claude_sdk": CLAUDE_PROVIDER,
}


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


class Settings(BaseModel):
    # LLM provider: mock | openai | anthropic | claude_agent_sdk. "openai" covers every
    # OpenAI-compatible endpoint (OpenAI, Azure-compatible gateways, DeepSeek, Qwen/DashScope,
    # vLLM, Ollama...). "claude_agent_sdk" runs agents through the Claude Agent SDK (Claude Code)
    # with the user's own Claude subscription login; see docs/claude-agent-sdk.md.
    llm_provider: str = "mock"
    llm_model: str = "gpt-4o"
    llm_small_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_temperature: float = 0.2
    llm_timeout_s: float = 120.0
    price_in_per_1m: float | None = None
    price_out_per_1m: float | None = None

    # --- Claude Agent SDK provider (FAMAS_CLAUDE_*). Safe by default: no real calls. ---
    claude_allow_real_calls: bool = False  # must be switched on explicitly before any model call
    claude_dry_run: bool = False  # scripted SDK responses through the real SDK; never calls a model
    claude_auth: str = "subscription"  # only "subscription" is supported (API keys are refused)
    claude_model: str = "sonnet"  # Claude Code model alias or full model id (large-tier agents)
    claude_small_model: str = "haiku"  # small-tier agents, judges and analyzers
    claude_max_turns: int = 3  # per call; >1 leaves room for the structured-output tool turn
    claude_max_calls_per_run: int = 40  # FA-MAS-side budget; exceeding it aborts the run
    claude_max_parallel: int = 2  # concurrent Claude Code processes per server process
    claude_timeout_s: float = 300.0
    claude_max_budget_usd_per_call: float | None = 1.0  # SDK-side cap on the (estimated) cost of one call
    claude_structured_output: str = "native"  # native (SDK output_format) | prompt (JSON in the reply)
    claude_stop_on_limit_warning: bool = False  # also stop when Claude reports approaching a limit
    claude_cli_path: str | None = None  # default: the CLI bundled with claude-agent-sdk
    claude_workdir: str = "./data/claude-workdir"  # empty working directory for Claude Code sessions
    # JSONL file with one metadata record per Claude call (tokens, model, timing, API-equivalent cost
    # estimate, rate-limit status). Never contains prompts, outputs or credentials. None = off.
    claude_usage_log: str | None = None

    github_token: str | None = None
    github_api_url: str = "https://api.github.com"

    database_url: str = "sqlite:///./data/famas.db"
    # LangGraph checkpointer: none | memory (or pass any checkpointer to Engine(checkpointer=...))
    checkpointer: str = "none"

    default_mode: str = "mock"
    ui_time_scale: float = 0.08  # pacing for mock runs started from the UI
    # Browser origins allowed to call the API (CORS). Default: only the local UI (built UI on :8000,
    # Vite dev server on :5173). "*" is honoured only while the provider is "mock".
    cors_origins: str = LOCAL_UI_ORIGINS
    # Host headers the API answers to (protects the local API against DNS-rebinding attacks).
    allowed_hosts: str = "localhost,127.0.0.1,::1,[::1]"
    # Live mode only: execute Python written by the model in the code sandbox. Off by default because
    # the sandbox limits CPU/memory/files but does not isolate the network or your home directory.
    allow_generated_code: bool = False
    frontend_dist: str = "./frontend/dist"

    def live_status(self) -> tuple[bool, str]:
        """Whether live mode can be used, and a human-readable reason (never contains secrets)."""
        if self.llm_provider == "mock":
            return False, "FAMAS_LLM_PROVIDER is 'mock' (see .env.example)"
        if self.llm_provider == CLAUDE_PROVIDER:
            from famas.llm.claude_agent_sdk_client import provider_status

            status = provider_status(self)
            return status["live_available"], status["reason"]
        if self.llm_api_key or self.llm_base_url:
            return True, f"{self.llm_provider} provider configured"
        return False, f"{self.llm_provider} provider needs an API key or base URL (see .env.example)"

    @property
    def live_available(self) -> bool:
        return self.live_status()[0]

    @property
    def claude_real_calls_enabled(self) -> bool:
        """True when this process may spend the user's Claude subscription (not a dry run)."""
        return (
            self.llm_provider == CLAUDE_PROVIDER and self.claude_allow_real_calls and not self.claude_dry_run
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_dotenv(Path(os.environ.get("FAMAS_ENV_FILE", ".env")))
    env = os.environ
    provider = env.get("FAMAS_LLM_PROVIDER", "mock").strip().lower()
    provider = _PROVIDER_ALIASES.get(provider, provider)
    api_key = env.get("FAMAS_LLM_API_KEY")
    if not api_key and provider in ("anthropic", "openai"):
        # The Claude Agent SDK provider never reads API keys: Claude Code authenticates itself.
        api_key = env.get("ANTHROPIC_API_KEY") if provider == "anthropic" else env.get("OPENAI_API_KEY")

    def _f(name: str) -> float | None:
        v = env.get(name)
        return float(v) if v else None

    return Settings(
        llm_provider=provider,
        llm_model=env.get(
            "FAMAS_LLM_MODEL",
            "claude-sonnet-4-5"
            if provider == "anthropic"
            else env.get("FAMAS_CLAUDE_MODEL", "sonnet")
            if provider == CLAUDE_PROVIDER
            else "gpt-4o",
        ),
        llm_small_model=env.get("FAMAS_LLM_SMALL_MODEL") or None,
        llm_base_url=env.get("FAMAS_LLM_BASE_URL") or None,
        llm_api_key=api_key,
        llm_temperature=float(env.get("FAMAS_LLM_TEMPERATURE", "0.2")),
        llm_timeout_s=float(env.get("FAMAS_LLM_TIMEOUT_S", "120")),
        price_in_per_1m=_f("FAMAS_PRICE_IN_PER_1M"),
        price_out_per_1m=_f("FAMAS_PRICE_OUT_PER_1M"),
        claude_allow_real_calls=_bool(env.get("FAMAS_CLAUDE_ALLOW_REAL_CALLS")),
        claude_dry_run=_bool(env.get("FAMAS_CLAUDE_DRY_RUN")),
        claude_auth=env.get("FAMAS_CLAUDE_AUTH", "subscription").strip().lower(),
        claude_model=env.get("FAMAS_CLAUDE_MODEL", "sonnet"),
        claude_small_model=env.get("FAMAS_CLAUDE_SMALL_MODEL", "haiku"),
        claude_max_turns=int(env.get("FAMAS_CLAUDE_MAX_TURNS", "3")),
        claude_max_calls_per_run=int(env.get("FAMAS_CLAUDE_MAX_CALLS_PER_RUN", "40")),
        claude_max_parallel=int(env.get("FAMAS_CLAUDE_MAX_PARALLEL", "2")),
        claude_timeout_s=float(env.get("FAMAS_CLAUDE_TIMEOUT_S", "300")),
        claude_max_budget_usd_per_call=(
            None
            if env.get("FAMAS_CLAUDE_MAX_BUDGET_USD_PER_CALL", "1.0").strip().lower() in ("", "none", "off")
            else float(env.get("FAMAS_CLAUDE_MAX_BUDGET_USD_PER_CALL", "1.0"))
        ),
        claude_structured_output=env.get("FAMAS_CLAUDE_STRUCTURED_OUTPUT", "native").strip().lower(),
        claude_stop_on_limit_warning=_bool(env.get("FAMAS_CLAUDE_STOP_ON_LIMIT_WARNING")),
        claude_cli_path=env.get("FAMAS_CLAUDE_CLI_PATH") or None,
        claude_workdir=env.get("FAMAS_CLAUDE_WORKDIR", "./data/claude-workdir"),
        claude_usage_log=(
            None
            if env.get("FAMAS_CLAUDE_USAGE_LOG", "").strip().lower() in ("none", "off", "false")
            else env.get("FAMAS_CLAUDE_USAGE_LOG") or "./data/claude-usage.jsonl"
        ),
        github_token=env.get("GITHUB_TOKEN") or None,
        github_api_url=env.get("FAMAS_GITHUB_API_URL", "https://api.github.com"),
        database_url=env.get("FAMAS_DATABASE_URL", "sqlite:///./data/famas.db"),
        checkpointer=env.get("FAMAS_CHECKPOINTER", "none"),
        default_mode=env.get("FAMAS_DEFAULT_MODE", "mock"),
        ui_time_scale=float(env.get("FAMAS_UI_TIME_SCALE", "0.08")),
        cors_origins=env.get("FAMAS_CORS_ORIGINS", LOCAL_UI_ORIGINS),
        allowed_hosts=env.get("FAMAS_ALLOWED_HOSTS", "localhost,127.0.0.1,::1,[::1]"),
        allow_generated_code=_bool(env.get("FAMAS_ALLOW_GENERATED_CODE")),
        frontend_dist=env.get("FAMAS_FRONTEND_DIST", "./frontend/dist"),
    )
