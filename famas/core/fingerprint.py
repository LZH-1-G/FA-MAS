"""Content fingerprints used for result reuse and invalidation.

A task's *input fingerprint* is a Merkle-style hash of its own specification
and of the output fingerprints of its dependencies. A stored result is valid
for reuse if and only if its recorded input fingerprint equals the task's
current input fingerprint. This is the same rule build systems (Make, Bazel,
Shake) use, and it gives "early cut-off" for free: if an upstream task is
re-executed but produces an identical output, downstream fingerprints do not
change and downstream results remain reusable.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_hash(obj: Any, length: int = 16) -> str:
    payload = json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def spec_fingerprint(task) -> str:
    """Hash of everything that defines *what* a task must produce."""
    return stable_hash(
        {
            "id": task.id,
            "type": task.task_type.value,
            "description": task.description,
            "inputs": task.inputs,
            "acceptance": task.acceptance_criteria,
            "covers": sorted(task.covers),
            "feedback": task.feedback,
            "revision": task.revision,
        }
    )


def output_fingerprint(content: str, data: dict[str, Any]) -> str:
    visible = {k: v for k, v in data.items() if not str(k).startswith("_")}
    return stable_hash({"content": content, "data": visible})
