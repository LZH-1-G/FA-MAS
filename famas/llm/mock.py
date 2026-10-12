"""Deterministic mock LLM with a token/latency/cost model."""

from __future__ import annotations

import asyncio
import hashlib

from pydantic import BaseModel

from famas.llm.base import LLMClient, LLMResponse, LLMUsage, MockFn, Tier, estimate_tokens, to_text
from famas.llm.pricing import cost_of

# Latency model (milliseconds): base + per output token + per input token.
LATENCY = {
    "small": {"base": 350.0, "out": 9.0, "in": 0.02},
    "large": {"base": 700.0, "out": 22.0, "in": 0.04},
    "none": {"base": 0.0, "out": 0.0, "in": 0.0},
}


class MockLLM(LLMClient):
    is_mock = True
    name = "mock"

    def __init__(self, time_scale: float = 0.0) -> None:
        # time_scale > 0 makes calls actually sleep (duration * scale) — used to pace UI demos.
        self.time_scale = time_scale

    def model_for(self, tier: Tier) -> str:
        return "mock-small" if tier == "small" else "mock-large"

    @staticmethod
    def _jitter(component: str, prompt: str) -> float:
        h = int(hashlib.sha256(f"{component}|{prompt[:512]}".encode()).hexdigest()[:8], 16)
        return 0.9 + (h % 2000) / 10000.0  # deterministic factor in [0.9, 1.1)

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
        reply = mock() if mock is not None else None
        if reply is None:
            from famas.llm.base import MockReply

            reply = MockReply(text=f"[mock:{component}] acknowledged.")
        parsed = reply.parsed
        if parsed is not None and schema is not None and not isinstance(parsed, BaseModel):
            parsed = schema.model_validate(parsed)
        text = reply.text or to_text(parsed)
        tokens_in = estimate_tokens(system) + estimate_tokens(prompt) + reply.extra_tokens_in
        tokens_out = estimate_tokens(text)
        lat = LATENCY.get(tier, LATENCY["large"])
        duration = (lat["base"] + lat["out"] * tokens_out + lat["in"] * tokens_in) * self._jitter(
            component, prompt
        )
        duration += reply.extra_ms
        model = self.model_for(tier)
        usage = LLMUsage(
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=cost_of(model, tokens_in, tokens_out) if tier != "none" else 0.0,
            duration_ms=round(duration, 2),
            model=model,
        )
        if self.time_scale > 0:
            await asyncio.sleep(duration * self.time_scale / 1000.0)
        return LLMResponse(text=text, parsed=parsed, usage=usage)
