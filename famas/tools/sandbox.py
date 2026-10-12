"""Minimal subprocess sandbox for agent-generated Python.

Runs code in a separate, isolated (``python -I``) interpreter with a timeout,
a scrubbed environment (no proxy variables or API keys) and, on POSIX, CPU and
memory rlimits. This contains accidents, not adversaries: for untrusted code
in production, run the coding agent inside the provided Docker image or a
dedicated sandbox service.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import time
from dataclasses import dataclass

RUNNER = r"""
import json, sys
_src = sys.stdin.readline()
_data = json.loads(sys.stdin.read() or "{}")
_ns = {"__name__": "__sandbox__"}
exec(compile(json.loads(_src), "<agent-code>", "exec"), _ns)
if "main" not in _ns:
    raise SystemExit("code must define main(data)")
_out = _ns["main"](_data)
sys.stdout.write("\n__FAMAS_RESULT__" + json.dumps(_out, default=str))
"""


@dataclass
class SandboxResult:
    ok: bool
    result: object = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    error: str | None = None


def _limits() -> None:  # pragma: no cover - runs in the child process
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
    except Exception:
        pass


class CodeSandbox:
    def __init__(self, timeout_s: float = 15.0, python: str | None = None) -> None:
        self.timeout_s = timeout_s
        self.python = python or sys.executable

    async def run(self, code: str, data: dict | None = None) -> SandboxResult:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONHASHSEED": "0", "LANG": "C.UTF-8"}
        t0 = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                self.python,
                "-I",
                "-c",
                RUNNER,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                preexec_fn=_limits if os.name == "posix" else None,
            )
        except OSError as e:
            return SandboxResult(ok=False, error=f"could not start sandbox: {e}")
        payload = (json.dumps(code) + "\n" + json.dumps(data or {})).encode()
        try:
            out, err = await asyncio.wait_for(proc.communicate(payload), timeout=self.timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return SandboxResult(ok=False, error="timeout", duration_ms=(time.perf_counter() - t0) * 1000)
        duration = (time.perf_counter() - t0) * 1000
        stdout = out.decode(errors="replace")
        stderr = err.decode(errors="replace")
        if proc.returncode != 0 or "__FAMAS_RESULT__" not in stdout:
            tail = stderr.strip().splitlines()[-1] if stderr.strip() else f"exit code {proc.returncode}"
            return SandboxResult(ok=False, stdout=stdout, stderr=stderr, duration_ms=duration, error=tail)
        printed, _, result_json = stdout.rpartition("__FAMAS_RESULT__")
        return SandboxResult(
            ok=True,
            result=json.loads(result_json),
            stdout=printed.strip(),
            stderr=stderr,
            duration_ms=duration,
        )
