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


class Settings(BaseModel):
    # LLM provider: mock | openai | anthropic. "openai" covers every OpenAI-compatible
    # endpoint (OpenAI, Azure-compatible gateways, DeepSeek, Qwen/DashScope, vLLM, Ollama...).
    llm_provider: str = "mock"
    llm_model: str = "gpt-4o"
    llm_small_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_temperature: float = 0.2
    llm_timeout_s: float = 120.0
    price_in_per_1m: float | None = None
    price_out_per_1m: float | None = None

    github_token: str | None = None
    github_api_url: str = "https://api.github.com"

    database_url: str = "sqlite:///./data/famas.db"
    # LangGraph checkpointer: none | memory (or pass any checkpointer to Engine(checkpointer=...))
    checkpointer: str = "none"

    default_mode: str = "mock"
    ui_time_scale: float = 0.08  # pacing for mock runs started from the UI
    cors_origins: str = "*"
    frontend_dist: str = "./frontend/dist"

    @property
    def live_available(self) -> bool:
        return self.llm_provider != "mock" and bool(self.llm_api_key or self.llm_base_url)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_dotenv(Path(os.environ.get("FAMAS_ENV_FILE", ".env")))
    env = os.environ
    provider = env.get("FAMAS_LLM_PROVIDER", "mock").lower()
    api_key = env.get("FAMAS_LLM_API_KEY")
    if not api_key:
        api_key = env.get("ANTHROPIC_API_KEY") if provider == "anthropic" else env.get("OPENAI_API_KEY")

    def _f(name: str) -> float | None:
        v = env.get(name)
        return float(v) if v else None

    return Settings(
        llm_provider=provider,
        llm_model=env.get("FAMAS_LLM_MODEL", "claude-sonnet-4-5" if provider == "anthropic" else "gpt-4o"),
        llm_small_model=env.get("FAMAS_LLM_SMALL_MODEL") or None,
        llm_base_url=env.get("FAMAS_LLM_BASE_URL") or None,
        llm_api_key=api_key,
        llm_temperature=float(env.get("FAMAS_LLM_TEMPERATURE", "0.2")),
        llm_timeout_s=float(env.get("FAMAS_LLM_TIMEOUT_S", "120")),
        price_in_per_1m=_f("FAMAS_PRICE_IN_PER_1M"),
        price_out_per_1m=_f("FAMAS_PRICE_OUT_PER_1M"),
        github_token=env.get("GITHUB_TOKEN") or None,
        github_api_url=env.get("FAMAS_GITHUB_API_URL", "https://api.github.com"),
        database_url=env.get("FAMAS_DATABASE_URL", "sqlite:///./data/famas.db"),
        checkpointer=env.get("FAMAS_CHECKPOINTER", "none"),
        default_mode=env.get("FAMAS_DEFAULT_MODE", "mock"),
        ui_time_scale=float(env.get("FAMAS_UI_TIME_SCALE", "0.08")),
        cors_origins=env.get("FAMAS_CORS_ORIGINS", "*"),
        frontend_dist=env.get("FAMAS_FRONTEND_DIST", "./frontend/dist"),
    )
