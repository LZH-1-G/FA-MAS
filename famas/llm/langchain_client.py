"""Live LLM client backed by LangChain chat models.

Supports any OpenAI-compatible endpoint (``provider=openai`` + optional
``base_url``: OpenAI, DeepSeek, Qwen/DashScope, vLLM, Ollama, ...) and
Anthropic (``provider=anthropic``). Structured output uses tool/function
calling where available and falls back to JSON-in-text parsing.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from pydantic import BaseModel, ValidationError

from famas.config import Settings
from famas.llm.base import (
    LLMClient,
    LLMError,
    LLMOutputError,
    LLMRateLimit,
    LLMResponse,
    LLMTimeout,
    LLMUsage,
    MockFn,
    Tier,
    estimate_tokens,
    extract_json,
)
from famas.llm.pricing import cost_of


def _map_error(exc: Exception) -> LLMError:
    name = type(exc).__name__.lower()
    if "ratelimit" in name or "429" in str(exc):
        return LLMRateLimit(str(exc))
    if "timeout" in name or isinstance(exc, asyncio.TimeoutError):
        return LLMTimeout(str(exc))
    err = LLMError(f"{type(exc).__name__}: {exc}")
    err.transient = any(k in name for k in ("connection", "apierror", "serviceunavailable", "internalserver"))
    return err


class LangChainLLM(LLMClient):
    is_mock = False

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.provider = settings.llm_provider
        self.name = settings.llm_model
        self._models: dict[str, Any] = {}
        self.structured_method = os.environ.get("FAMAS_STRUCTURED_METHOD", "function_calling")

    def model_for(self, tier: Tier) -> str:
        if tier == "small" and self.settings.llm_small_model:
            return self.settings.llm_small_model
        return self.settings.llm_model

    def _chat_model(self, tier: Tier):
        model_name = self.model_for(tier)
        if model_name in self._models:
            return self._models[model_name]
        s = self.settings
        if self.provider == "anthropic":
            try:
                from langchain_anthropic import ChatAnthropic
            except ImportError as e:  # pragma: no cover - optional dependency
                raise LLMError("install fa-mas[anthropic] to use the Anthropic provider") from e
            model = ChatAnthropic(
                model=model_name,
                api_key=s.llm_api_key,
                temperature=s.llm_temperature,
                timeout=s.llm_timeout_s,
                max_tokens=4096,
            )
        else:
            try:
                from langchain_openai import ChatOpenAI
            except ImportError as e:  # pragma: no cover - optional dependency
                raise LLMError("install fa-mas[openai] to use OpenAI-compatible providers") from e
            kwargs: dict[str, Any] = {
                "model": model_name,
                "api_key": s.llm_api_key or "not-needed",
                "temperature": s.llm_temperature,
                "timeout": s.llm_timeout_s,
                "max_retries": 1,
            }
            if s.llm_base_url:
                kwargs["base_url"] = s.llm_base_url
            model = ChatOpenAI(**kwargs)
        self._models[model_name] = model
        return model

    @staticmethod
    def _usage(message: Any) -> tuple[int, int]:
        meta = getattr(message, "usage_metadata", None) or {}
        return int(meta.get("input_tokens", 0) or 0), int(meta.get("output_tokens", 0) or 0)

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
        from langchain_core.messages import HumanMessage, SystemMessage

        model = self._chat_model(tier)
        model_name = self.model_for(tier)
        messages = [SystemMessage(content=system), HumanMessage(content=prompt)]
        t0 = time.perf_counter()
        parsed: Any = None
        text = ""
        tin = tout = 0
        try:
            if schema is not None:
                try:
                    structured = model.with_structured_output(
                        schema, include_raw=True, method=self.structured_method
                    )
                    out = await structured.ainvoke(messages)
                    raw = out.get("raw")
                    parsed = out.get("parsed")
                    tin, tout = self._usage(raw)
                    text = raw.content if isinstance(getattr(raw, "content", None), str) else ""
                    if parsed is None:
                        raise LLMOutputError(str(out.get("parsing_error")))
                except (LLMOutputError, NotImplementedError, ValidationError, ValueError, TypeError):
                    # Fallback: ask for raw JSON and parse it ourselves.
                    json_prompt = (
                        prompt
                        + "\n\nRespond ONLY with a JSON object matching this JSON schema:\n"
                        + str(schema.model_json_schema())
                    )
                    msg = await model.ainvoke(
                        [SystemMessage(content=system), HumanMessage(content=json_prompt)]
                    )
                    a, b = self._usage(msg)
                    tin, tout = tin + a, tout + b
                    text = msg.content if isinstance(msg.content, str) else str(msg.content)
                    try:
                        parsed = schema.model_validate(extract_json(text))
                    except (ValidationError, ValueError) as e:
                        raise LLMOutputError(f"could not parse {schema.__name__}: {e}") from e
            else:
                msg = await model.ainvoke(messages)
                tin, tout = self._usage(msg)
                content = msg.content
                text = (
                    content
                    if isinstance(content, str)
                    else "".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
                )
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001 - provider SDKs raise many types
            raise _map_error(e) from e
        if not tin:
            tin = estimate_tokens(system) + estimate_tokens(prompt)
        if not tout:
            tout = estimate_tokens(
                text or (parsed.model_dump_json() if isinstance(parsed, BaseModel) else "")
            )
        duration = (time.perf_counter() - t0) * 1000
        usage = LLMUsage(
            tokens_in=tin,
            tokens_out=tout,
            cost=cost_of(
                model_name, tin, tout, self.settings.price_in_per_1m, self.settings.price_out_per_1m
            ),
            duration_ms=round(duration, 2),
            model=model_name,
        )
        if not text and isinstance(parsed, BaseModel):
            text = parsed.model_dump_json()
        return LLMResponse(text=text, parsed=parsed, usage=usage)
