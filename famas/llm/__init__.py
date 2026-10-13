"""LLM abstraction: deterministic mock client, LangChain-backed live client, Claude Agent SDK client."""

from __future__ import annotations

from famas.config import CLAUDE_PROVIDER, Settings, get_settings
from famas.llm.base import LLMClient, LLMResponse, LLMUsage, MockReply  # noqa: F401
from famas.llm.mock import MockLLM


def create_llm(mode: str, settings: Settings | None = None, time_scale: float = 0.0) -> LLMClient:
    """Return a mock client for ``mode='mock'`` or the configured live client."""
    settings = settings or get_settings()
    if mode == "mock" or settings.llm_provider == "mock":
        return MockLLM(time_scale=time_scale)
    if settings.llm_provider == CLAUDE_PROVIDER:
        from famas.llm.claude_agent_sdk_client import ClaudeAgentSDKLLM

        return ClaudeAgentSDKLLM(settings)
    from famas.llm.langchain_client import LangChainLLM

    return LangChainLLM(settings)
