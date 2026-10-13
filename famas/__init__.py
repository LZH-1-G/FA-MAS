"""FA-MAS: Failure-Aware Adaptive Multi-Agent System.

Public entry points:

>>> from famas import Engine, RunConfig
>>> outcome = Engine().run_sync("Compare the architectures of LangGraph and CrewAI", RunConfig())
"""

from famas.core.models import RunConfig, SystemName  # noqa: F401

__version__ = "0.1.0b1"  # v0.1.0-beta.1


def __getattr__(name: str):  # lazy import keeps `import famas` cheap
    if name == "Engine":
        from famas.orchestration.engine import Engine

        return Engine
    raise AttributeError(name)
