"""The admission gate must fire, and must show its numbers when it passes.

A gate that never fires and a gate that always passes produce the same summary
line.  These tests make the gate fail on purpose, so the passing case means
something.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.interleaved_generation_rounds import PROVIDER_GATE, provider_gate  # noqa: E402


def _rows(rounds: int, per_round: int, status: str = "completed"):
    rows = []
    for index in range(rounds):
        for slot in range(per_round):
            rows.append({"round": index, "status": status, "question_id": f"q{slot}"})
    return rows


def test_a_healthy_run_passes_and_still_reports_its_numbers():
    """Passing must not mean "nothing to see".

    The observed values are reported whether or not the gate fires, because a
    summary that only says `passed` cannot be distinguished from one that never
    ran the check.
    """
    gate = provider_gate(_rows(3, 22), rounds=3, paired=60, cells=66)
    assert gate["passed"] is True
    assert gate["failed"] == []
    assert gate["observed"]["provider_error_rate"] == 0.0
    assert gate["observed"]["paired_cell_share"] == round(60 / 66, 3)
    assert gate["thresholds"] == PROVIDER_GATE


def test_a_round_with_no_completions_fails_the_gate():
    """The round-3 collapse of the first interleaved run.

    It contributed no quality data at all, and the run looked unremarkable in
    aggregate -- which is why it is checked on its own.
    """
    rows = _rows(2, 22) + [
        {"round": 2, "status": "provider_error", "question_id": f"q{i}"}
        for i in range(22)
    ]
    gate = provider_gate(rows, rounds=3, paired=11, cells=33)
    assert gate["empty_rounds"] == [2]
    assert "rounds_with_no_completions" in gate["failed"]
    assert gate["passed"] is False


def test_a_half_failed_run_fails_on_the_error_rate():
    """The measured shape of the first interleaved run: ~50% refused."""
    rows = (
        _rows(1, 11)
        + [{"round": 1, "status": "provider_error", "question_id": f"q{i}"} for i in range(11)]
        + [{"round": 2, "status": "provider_error", "question_id": f"q{i}"} for i in range(11)]
    )
    gate = provider_gate(rows, rounds=3, paired=11, cells=33)
    assert gate["observed"]["provider_error_rate"] > PROVIDER_GATE["provider_error_rate"]
    assert "provider_error_rate" in gate["failed"]
    assert gate["passed"] is False


def test_too_few_paired_cells_fails_even_when_the_provider_is_healthy():
    """Availability and comparability are different things.

    A run can complete every call and still not support an arm comparison if the
    pairs do not line up, so this is gated separately rather than inferred.
    """
    gate = provider_gate(_rows(3, 22), rounds=3, paired=10, cells=66)
    assert gate["observed"]["provider_error_rate"] == 0.0
    assert gate["observed"]["rounds_with_no_completions"] == 0
    assert gate["failed"] == ["paired_cell_share"]
    assert gate["passed"] is False


def test_the_thresholds_are_stated_not_implied():
    """A reader must be able to see what the gate demanded."""
    assert PROVIDER_GATE["provider_error_rate"] == 0.10
    assert PROVIDER_GATE["provider_timeout_rate"] == 0.10
    assert PROVIDER_GATE["rounds_with_no_completions"] == 0
    assert PROVIDER_GATE["paired_cell_share"] == 0.60
