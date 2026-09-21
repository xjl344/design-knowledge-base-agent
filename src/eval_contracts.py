"""Deterministic contracts for evaluation counters and degradation semantics."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any


class DegradationReason(str, Enum):
    NONE = "none"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    INFRA_TIMEOUT = "infra_timeout"
    CITATION_INVALID = "citation_invalid"
    CALCULATION_INVALID = "calculation_invalid"


@dataclass
class CallCounter:
    scheduled: int = 0
    attempted: int = 0
    completed: int = 0
    timeout: int = 0
    failed: int = 0
    skipped: int = 0
    cancelled: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)

    def validate(self) -> None:
        if self.scheduled != self.attempted + self.skipped:
            raise AssertionError(f"scheduled invariant failed: {self.as_dict()}")
        if self.attempted != self.completed + self.timeout + self.failed + self.cancelled:
            raise AssertionError(f"attempted invariant failed: {self.as_dict()}")


def counter_from_tool_calls(tool_calls: list[dict[str, Any]], name: str) -> CallCounter:
    calls = [call for call in tool_calls if call.get("tool") == name]
    counter = CallCounter(scheduled=len(calls), attempted=len(calls))
    for call in calls:
        if call.get("success"):
            counter.completed += 1
            continue
        error = str(call.get("error") or "").lower()
        if any(token in error for token in ("timeout", "超时", "超过", "deadline", "timed out")):
            counter.timeout += 1
        else:
            counter.failed += 1
    return counter


def classify_degradation(
    counters: dict[str, CallCounter],
    audit_result: str | None,
    timeout_stage: str | None,
    deliverable: bool = False,
) -> DegradationReason:
    if deliverable:
        return DegradationReason.NONE
    if timeout_stage or any(
        counter.timeout or counter.failed or counter.cancelled
        for counter in counters.values()
    ):
        return DegradationReason.INFRA_TIMEOUT
    planner = counters.get("planner", CallCounter())
    retrieval = counters.get("retrieval", CallCounter())
    if (
        planner.attempted > 0
        and planner.completed > 0
        and planner.timeout == planner.failed == planner.cancelled == 0
        and retrieval.attempted > 0
        and retrieval.completed > 0
        and retrieval.timeout == retrieval.failed == retrieval.cancelled == 0
        and audit_result == "insufficient_evidence"
    ):
        return DegradationReason.EVIDENCE_INSUFFICIENT
    return DegradationReason.INFRA_TIMEOUT


def reserve_budget(total_deadline: float, reserved_seconds: float, now: float) -> tuple[float, float]:
    """Return local deadline and fallback deadline from an absolute deadline."""
    fallback_deadline = total_deadline
    local_deadline = max(now, total_deadline - max(0.0, reserved_seconds))
    return local_deadline, fallback_deadline
