"""Tests for the interleaved A/B latency probe.

The point of the probe is that a block-of-time comparison is untrustworthy:
provider latency drifted 2.3x within one session, so running all of arm A and
then all of arm B charges that drift to arm B.  What has to hold is that the
*pairing* removes drift from the difference, and that a failure's duration --
which is set by the timeout ceiling, not the provider -- stays out of the
latency statistics.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ab_latency_probe import _parse_arm, summarise  # noqa: E402


def _observation(round_index, question_id, arm, latency, status="completed"):
    return {
        "round": round_index,
        "question_id": question_id,
        "arm": arm,
        "status": status,
        "latency_seconds": latency,
        "context_chars": 3000 if arm == "small" else 12800,
    }


# --- arm parsing ----------------------------------------------------------


def test_arm_spec_parses_a_character_budget():
    assert _parse_arm("compressed:99:300") == {
        "name": "compressed",
        "max_items": 99,
        "max_chars_per_item": 300,
    }


def test_arm_spec_accepts_no_budget():
    assert _parse_arm("small:5:none")["max_chars_per_item"] is None


def test_a_malformed_arm_spec_is_rejected():
    with pytest.raises(SystemExit, match="name:max_items:max_chars"):
        _parse_arm("small:5")


# --- paired comparison ----------------------------------------------------


def test_pairing_removes_a_global_drift():
    """The property the whole probe exists for.

    Both arms get slower by the same amount each round.  A block comparison
    would report the drift as an arm difference; the paired difference stays at
    the true 30s gap.
    """
    observations = []
    for round_index in range(3):
        drift = round_index * 20
        for question_id in ("q1", "q2"):
            observations.append(_observation(round_index, question_id, "small", 40 + drift))
            observations.append(_observation(round_index, question_id, "large", 70 + drift))

    result = summarise(observations, ["small", "large"])
    # Unpaired means are contaminated by the drift...
    assert result["latency_by_arm"]["small"]["mean"] == 60.0
    assert result["latency_by_arm"]["large"]["mean"] == 90.0
    # ...but every pair shows exactly the real difference.
    assert result["paired_difference"]["mean"] == 30.0
    assert result["paired_difference"]["median"] == 30.0
    assert result["paired_difference"]["large_slower_pairs"] == 6
    assert result["paired_difference"]["large_faster_pairs"] == 0


def test_pairs_are_formed_within_a_round_not_across_rounds():
    """A round-0 A must never be paired with a round-1 B.

    Those two calls are separated by whatever the provider did in between,
    which is exactly the confound the probe removes.
    """
    observations = [
        _observation(0, "q1", "small", 40),
        _observation(0, "q1", "large", 70),
        # A second round where the pairing is deliberately extreme.
        _observation(1, "q1", "small", 400),
        _observation(1, "q1", "large", 410),
    ]
    result = summarise(observations, ["small", "large"])
    assert result["paired_difference"]["pairs"] == 2
    # Pairing across rounds would give one 370s difference; within a round the
    # two differences are 30 and 10.
    assert result["paired_difference"]["mean"] == 20.0


def test_failures_are_excluded_from_latency_but_still_counted():
    """A timeout's duration is the ceiling, not the provider's speed."""
    observations = [
        _observation(0, "q1", "small", 40),
        _observation(0, "q1", "large", 70),
        _observation(0, "q2", "small", 45),
        _observation(0, "q2", "large", 182, status="provider_timeout"),
    ]
    result = summarise(observations, ["small", "large"])
    assert result["failed_calls"] == 1
    assert result["failure_statuses"] == ["provider_timeout"]
    assert result["latency_by_arm"]["large"]["n"] == 1
    assert result["latency_by_arm"]["large"]["max"] == 70
    # Only the complete pair contributes a difference.
    assert result["paired_difference"]["pairs"] == 1


def test_per_question_differences_are_reported():
    """A single headline number would hide that one question behaves unlike the rest."""
    observations = []
    for round_index in range(2):
        observations.append(_observation(round_index, "q1", "small", 40))
        observations.append(_observation(round_index, "q1", "large", 70))
        observations.append(_observation(round_index, "q2", "small", 40))
        observations.append(_observation(round_index, "q2", "large", 45))

    result = summarise(observations, ["small", "large"])
    assert result["paired_difference"]["per_question_mean"] == {"q1": 30.0, "q2": 5.0}


def test_direction_is_stated_so_a_negative_delta_is_not_misread():
    """The delta definition must travel with the number."""
    result = summarise(
        [_observation(0, "q1", "small", 70), _observation(0, "q1", "large", 40)],
        ["small", "large"],
    )
    assert "large - small" in result["paired_difference"]["definition"]
    assert result["paired_difference"]["mean"] == -30.0
    assert result["paired_difference"]["large_faster_pairs"] == 1


def test_context_sizes_are_recorded_per_arm():
    """The measurement is meaningless without proof the arms differed."""
    result = summarise(
        [_observation(0, "q1", "small", 40), _observation(0, "q1", "large", 70)],
        ["small", "large"],
    )
    assert result["context_chars_by_arm"] == {"small": [3000], "large": [12800]}


def test_answer_length_is_reported_per_arm():
    """Output length is the most likely innocent cause of a latency shift.

    An earlier probe measured one arm slower and could not rule this out,
    because answer length was not recorded.  It has to travel with the result.
    """
    observations = [
        {**_observation(0, "q1", "small", 40), "answer_chars": 500},
        {**_observation(0, "q1", "large", 70), "answer_chars": 800},
        {**_observation(0, "q2", "small", 45), "answer_chars": 600},
        {**_observation(0, "q2", "large", 75), "answer_chars": 900},
    ]
    result = summarise(observations, ["small", "large"])
    assert result["answer_chars_by_arm"]["small"] == {"n": 2, "mean": 550.0, "median": 550.0}
    assert result["answer_chars_by_arm"]["large"] == {"n": 2, "mean": 850.0, "median": 850.0}


def test_missing_answer_length_does_not_break_the_summary():
    """Runs recorded before the field existed must still summarise."""
    result = summarise(
        [_observation(0, "q1", "small", 40), _observation(0, "q1", "large", 70)],
        ["small", "large"],
    )
    assert result["answer_chars_by_arm"]["small"] == {"n": 0, "mean": None, "median": None}
