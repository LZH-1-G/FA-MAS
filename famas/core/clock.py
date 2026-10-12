"""Clocks.

FA-MAS supports two timing modes:

* ``RealClock`` – wall-clock time, used for live LLM execution and paced demos.
* ``SimClock`` – a virtual clock driven by the dispatcher's discrete-event
  simulation. Mock agents report a modelled duration instead of sleeping, so
  benchmark runs are fast *and* their latency numbers are deterministic and
  reflect the true critical path of the schedule (including parallelism and
  recovery stalls).
"""

from __future__ import annotations

import time


class Clock:
    simulated: bool = False

    def now(self) -> float:  # milliseconds since run start
        raise NotImplementedError

    def advance(self, ms: float) -> None:
        """Account for work done outside the dispatcher (planning, recovery)."""

    def set(self, ms: float) -> None:
        """Move a simulated clock forward to an event time (no-op for real clocks)."""


class RealClock(Clock):
    simulated = False

    def __init__(self) -> None:
        self._t0 = time.perf_counter()

    def now(self) -> float:
        return (time.perf_counter() - self._t0) * 1000.0


class SimClock(Clock):
    simulated = True

    def __init__(self) -> None:
        self._now = 0.0

    def now(self) -> float:
        return self._now

    def advance(self, ms: float) -> None:
        self._now += max(0.0, float(ms))

    def set(self, ms: float) -> None:
        self._now = max(self._now, float(ms))
