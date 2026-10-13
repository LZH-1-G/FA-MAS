"""Provider-independent LLM interface.

Every component talks to language models through ``LLMClient.complete``. Each
call carries a ``mock`` callback that produces a deterministic reply, so the
*same* component code runs in two modes:

* live – the callback is ignored and a real model (via LangChain) is called;
* mock – the callback's reply is returned, with token counts, cost and latency
  modelled from the prompt and reply sizes. Mock mode is what makes tests and
  benchmarks reproducible.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

Tier = Literal["small", "large", "none"]


class LLMError(Exception):
    transient = False
    # A fatal error means "stop the run": retrying, reassigning or replanning cannot help
    # (provider disabled, wrong credentials, subscription usage limit reached, ...).
    fatal = False


class LLMTimeout(LLMError):
    transient = True


class LLMRateLimit(LLMError):
    transient = True


class LLMOutputError(LLMError):
    """The model answered, but the answer could not be parsed into the schema."""


class LLMFatalError(LLMError):
    """Non-recoverable provider condition. The failure analyzer aborts the run instead of replanning."""

    transient = False
    fatal = True


class LLMConfigError(LLMFatalError):
    """The provider is disabled, not installed or misconfigured."""


class LLMAuthError(LLMFatalError):
    """Missing or disallowed credentials (e.g. an API key where a subscription login is required)."""


class LLMUsageLimit(LLMFatalError):
    """A usage limit was reached, or continuing would draw on pay-as-you-go extra usage."""


class LLMBudgetExceeded(LLMFatalError):
    """A FA-MAS-side budget (e.g. maximum model calls per run) was exhausted."""


@dataclass
class MockReply:
    text: str = ""
    parsed: Any = None
    extra_ms: float = 0.0  # e.g. modelled tool latency
    extra_tokens_in: int = 0  # e.g. tool output fed back into the context


@dataclass
class LLMUsage:
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    duration_ms: float = 0.0
    model: str = ""
    calls: int = 1


@dataclass
class LLMResponse:
    text: str
    parsed: Any = None
    usage: LLMUsage = field(default_factory=LLMUsage)


MockFn = Callable[[], MockReply]

_CJK = re.compile(r"[　-鿿가-힯]")


def estimate_tokens(text: str) -> int:
    """Cheap tokenizer-free estimate: ~4 chars/token for Latin text, 1 token per CJK char."""
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    return max(1, (len(text) - cjk) // 4 + cjk)


def to_text(obj: Any) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, BaseModel):
        return obj.model_dump_json()
    return json.dumps(obj, ensure_ascii=False, default=str)


def extract_json(text: str) -> Any:
    """Parse the first JSON object/array in a model reply (handles ```json fences)."""
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    candidate = fence.group(1) if fence else text
    start = min([i for i in (candidate.find("{"), candidate.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise LLMOutputError("no JSON found in model output")
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(candidate[start:])
    return obj


class LLMClient(ABC):
    is_mock: bool = False
    name: str = "llm"

    @abstractmethod
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
    ) -> LLMResponse: ...

    def model_for(self, tier: Tier) -> str:
        return self.name

    @property
    def live_tools(self) -> bool:
        """Whether agents should use live external tools (GitHub API, real code timing).

        True for real models; False for the mock model and for scripted dry runs, which
        use the offline knowledge fixtures so they never touch the network.
        """
        return not self.is_mock

    async def preflight(self) -> None:
        """Cheap checks run once before a run starts (no model calls). Raise ``LLMError`` to refuse."""
        return None

    def describe(self) -> dict[str, Any]:
        """Non-secret description of the client for health endpoints and logs."""
        return {"name": self.name, "mock": self.is_mock}
