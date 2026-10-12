"""Simulation ground truth.

The oracle knows which outputs were deliberately corrupted. The system under
test never reads it for decisions; it is used to (1) model a downstream agent
noticing a contradiction with a configurable probability, and (2) score runs:
final-output correctness and *invalid reuse* (a preserved result that was in
fact derived from corrupted data).
"""

from __future__ import annotations

from famas.core.models import TaskResult
from famas.simulation.injection import Fault, seeded_uniform

CORRUPTION_SUFFIX = (
    " In practice, however, it provides no mechanism for persisting state, retrying work or resuming after "
    "failures, and all agents must run strictly sequentially."
)


class SimulationOracle:
    def __init__(self, seed: int = 0, detection_probability: float = 1.0) -> None:
        self.seed = seed
        self.p_detect = detection_probability

    @staticmethod
    def _gt(result: TaskResult) -> dict:
        return result.meta.setdefault("gt", {})

    def corrupted(self, result: TaskResult) -> bool:
        return bool(result.meta.get("gt", {}).get("corrupted"))

    def tainted(self, result: TaskResult | None) -> bool:
        if result is None:
            return False
        gt = result.meta.get("gt", {})
        return bool(gt.get("corrupted") or gt.get("tainted_lineage"))

    def propagate(self, result: TaskResult, inputs: dict[str, TaskResult]) -> None:
        if any(self.tainted(r) for r in inputs.values()):
            self._gt(result)["tainted_lineage"] = True

    def apply_output_fault(self, result: TaskResult, fault: Fault) -> TaskResult:
        units = dict(result.units)
        if fault.kind == "invalid_format":
            result.content = (
                '{"findings": [{"aspect": "archit' + "\n<<model output truncated: malformed JSON>>"
            )
            result.data = {"_raw": result.content}
        elif fault.kind in ("incomplete", "too_complex"):
            for u in fault.units:
                units.pop(u, None)
            result.data = {**result.data, "units": units, "_partial": True}
            findings = result.data.get("findings")
            if isinstance(findings, dict):
                keep = {u.rsplit(":", 1)[-1] for u in units if u.startswith("research:")}
                result.data["findings"] = {a: f for a, f in findings.items() if a in keep}
            result.content += "\n\n_(output ended early: some requested aspects were not covered)_"
        elif fault.kind == "silent_corruption":
            target = next((u for u in fault.units if u in units), next(iter(units), None))
            if target:
                units[target] = units[target] + CORRUPTION_SUFFIX
                result.data = {**result.data, "units": units}
                findings = result.data.get("findings")
                if isinstance(findings, dict):
                    aspect = target.rsplit(":", 1)[-1]
                    if aspect in findings:
                        findings[aspect] = {
                            **findings[aspect],
                            "summary": findings[aspect]["summary"] + CORRUPTION_SUFFIX,
                        }
                result.content += "\n" + CORRUPTION_SUFFIX.strip()
            self._gt(result)["corrupted"] = True
            self._gt(result)["corrupted_unit"] = target
        return result

    def detects(self, consumer_task_id: str, consumer_attempt: int, upstream: TaskResult) -> bool:
        """Does a consumer notice that an upstream result is corrupted? (probabilistic, seeded)."""
        if not self.corrupted(upstream):
            return False
        if self.p_detect >= 1.0:
            return True
        return (
            seeded_uniform(
                self.seed,
                "detect",
                consumer_task_id,
                consumer_attempt,
                upstream.task_id,
                upstream.attempt,
                upstream.output_fingerprint,  # content hash, not the random result id -> reproducible
            )
            < self.p_detect
        )
