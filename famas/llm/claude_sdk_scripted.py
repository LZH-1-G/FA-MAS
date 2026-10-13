"""Offline transport for the Claude Agent SDK: scripted responses, no CLI process, no model call.

``ScriptedTransport`` implements the SDK's public ``Transport`` interface and
speaks the same newline-delimited JSON control protocol as the Claude Code CLI:
it answers the ``initialize`` control request (including the ``account`` block
FA-MAS uses to verify the credential source) and replies to each user message
with scripted ``system`` / ``assistant`` / ``rate_limit_event`` / ``result``
frames. Plugged into ``ClaudeSDKClient(transport=...)`` it exercises the real
SDK end to end - option handling, control handshake, message parsing - while
guaranteeing that nothing leaves the machine.

It is used for:

* ``FAMAS_CLAUDE_DRY_RUN=true`` - the provider replays each component's
  deterministic mock reply through the real SDK, so the live code paths
  (LLM planner, live agents, evaluators) can be tried for free;
* the automated tests (``tests/test_claude_agent_sdk.py``), which script
  successes, usage-limit events, auth failures and malformed output.

This module imports ``claude_agent_sdk`` at import time; import it lazily.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

from claude_agent_sdk import Transport
from pydantic import BaseModel

from famas.llm.base import MockReply, estimate_tokens

# What a Claude.ai subscription login looks like in the initialize response (simulated).
DRY_RUN_ACCOUNT: dict[str, Any] = {"subscriptionType": "pro", "apiProvider": "firstParty"}

Responder = Callable[[str], list[dict[str, Any]]]
_EOF = object()


class ScriptedTransport(Transport):
    """In-memory transport that plays a script instead of running Claude Code."""

    def __init__(
        self,
        responder: Responder,
        account: dict[str, Any] | None = None,
        initialize_extra: dict[str, Any] | None = None,
    ) -> None:
        self.responder = responder
        self.account = dict(DRY_RUN_ACCOUNT) if account is None else account
        self.initialize_extra = initialize_extra or {}
        self.prompts: list[str] = []  # user messages received (for assertions)
        self.control_requests: list[dict[str, Any]] = []
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._ready = False
        self._input_ended = False

    # ------------------------------------------------------------ Transport
    async def connect(self) -> None:
        self._ready = True

    async def write(self, data: str) -> None:
        for line in data.splitlines():
            if not line.strip():
                continue
            msg = json.loads(line)
            kind = msg.get("type")
            if kind == "control_request":
                self.control_requests.append(msg)
                self._queue.put_nowait(self._control_response(msg))
            elif kind == "user":
                content = msg.get("message", {}).get("content", "")
                prompt = (
                    content
                    if isinstance(content, str)
                    else "".join(b.get("text", "") for b in content if isinstance(b, dict))
                )
                self.prompts.append(prompt)
                for frame in self.responder(prompt):
                    self._queue.put_nowait(frame)

    async def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            item = await self._queue.get()
            if item is _EOF:
                return
            yield item

    async def close(self) -> None:
        self._ready = False
        self._queue.put_nowait(_EOF)

    def is_ready(self) -> bool:
        return self._ready

    async def end_input(self) -> None:
        self._input_ended = True

    # ------------------------------------------------------------- helpers
    def _control_response(self, msg: dict[str, Any]) -> dict[str, Any]:
        request = msg.get("request", {})
        body: dict[str, Any] = {}
        if request.get("subtype") == "initialize":
            body = {"commands": [], "models": [], "output_style": "default", **self.initialize_extra}
            if self.account is not None:
                body["account"] = self.account
        return {
            "type": "control_response",
            "response": {"subtype": "success", "request_id": msg.get("request_id"), "response": body},
        }


# ------------------------------------------------------------------ frames
def _uid() -> str:
    return str(uuid.uuid4())


def assistant_frame(text: str, model: str, session_id: str, usage: dict | None = None) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {
            "id": f"msg_{uuid.uuid4().hex[:12]}",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": usage or {},
        },
        "parent_tool_use_id": None,
        "session_id": session_id,
        "uuid": _uid(),
    }


def result_frame(
    session_id: str,
    *,
    result: str | None = "",
    structured_output: Any = None,
    usage: dict | None = None,
    total_cost_usd: float | None = None,
    is_error: bool = False,
    subtype: str = "success",
    errors: list[str] | None = None,
    api_error_status: int | None = None,
    duration_ms: int = 5,
) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "type": "result",
        "subtype": subtype,
        "duration_ms": duration_ms,
        "duration_api_ms": duration_ms,
        "is_error": is_error,
        "num_turns": 1,
        "session_id": session_id,
        "total_cost_usd": total_cost_usd,
        "usage": usage or {},
        "result": result,
        "structured_output": structured_output,
        "uuid": _uid(),
    }
    if errors is not None:
        frame["errors"] = errors
    if api_error_status is not None:
        frame["api_error_status"] = api_error_status
    return frame


def rate_limit_frame(session_id: str, **info: Any) -> dict[str, Any]:
    """``info`` uses the CLI's camelCase keys, e.g. status="rejected", rateLimitType="five_hour"."""
    return {"type": "rate_limit_event", "rate_limit_info": info, "uuid": _uid(), "session_id": session_id}


def init_frame(session_id: str, model: str, cwd: str = "") -> dict[str, Any]:
    return {
        "type": "system",
        "subtype": "init",
        "session_id": session_id,
        "model": model,
        "tools": [],
        "cwd": cwd,
        "uuid": _uid(),
    }


def reply_frames(
    *,
    text: str,
    model: str,
    system: str = "",
    prompt: str = "",
    structured_output: Any = None,
    total_cost_usd: float | None = None,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """A successful single-turn exchange: init, assistant text, result."""
    sid = session_id or f"scripted-{uuid.uuid4().hex[:8]}"
    usage = {
        "input_tokens": estimate_tokens(system) + estimate_tokens(prompt),
        "output_tokens": estimate_tokens(text),
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    return [
        init_frame(sid, model),
        assistant_frame(text, model, sid, usage),
        result_frame(
            sid,
            result=text,
            structured_output=structured_output,
            usage=usage,
            total_cost_usd=total_cost_usd,
        ),
    ]


def dry_run_responder(
    *,
    component: str,
    system: str,
    model: str,
    reply: MockReply | None,
    schema_mode: str | None,
) -> Responder:
    """Replay a component's deterministic mock reply as Claude Code frames.

    ``schema_mode`` is None (plain text), "native" (the SDK's ``structured_output``)
    or "prompt" (JSON in the reply text), mirroring how the provider asked.
    """
    parsed = reply.parsed if reply is not None else None
    if isinstance(parsed, BaseModel):
        parsed = parsed.model_dump(mode="json")
    if schema_mode is not None and parsed is not None:
        text = json.dumps(parsed, ensure_ascii=False)
    elif reply is not None and reply.text:
        text = reply.text
    elif parsed is not None:
        text = json.dumps(parsed, ensure_ascii=False)
    else:
        text = f"[dry-run:{component}] acknowledged."
    native = schema_mode == "native"

    def respond(prompt: str) -> list[dict[str, Any]]:
        return reply_frames(
            text=text,
            model=f"dry-run:{model}",
            system=system,
            prompt=prompt,
            structured_output=parsed if native else None,
        )

    return respond


__all__ = [
    "DRY_RUN_ACCOUNT",
    "ScriptedTransport",
    "assistant_frame",
    "dry_run_responder",
    "init_frame",
    "rate_limit_frame",
    "reply_frames",
    "result_frame",
]
