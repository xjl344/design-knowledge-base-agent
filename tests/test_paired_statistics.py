"""Pin the paired-statistics convention: one question is one observation.

Why this test file exists
-------------------------
``summarise_pairs`` used to divide ``stdev(diffs)`` by ``sqrt(n_pairs)``, where a
pair was ``(case_set, question, round)``.  One question therefore contributed as
many "independent" observations as it had rounds, and the standard error came out
too small -- on this project's data by a factor of 1.41, which moved the reported
minimum detectable effect from 0.197 to 0.140.

Nothing failed.  The number was simply wrong, and wrong in the direction that
makes an effect look better established than it is.  That is the kind of defect
only an explicit test catches, so these tests assert the *arithmetic* and, more
importantly, a case where the two conventions reach **opposite conclusions**.

The data here is synthetic on purpose: the point is the formula, and a fixture
whose expected values are computed by hand cannot be quietly re-baselined.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

from src.paired_statistics import (
    COMPLETED,
    PAIRED_STATISTICS_VERSION,
    paired_rows,
    summarise_pairs,
)

ROOT = Path(__file__).resolve().parent.parent


def pair(
    case_set: str, question_id: str, round_index: int, first: float, second: float
) -> dict:
    return {
        "case_set": case_set,
        "question_id": question_id,
        "round": round_index,
        "old_hop_recall": first,
        "new_hop_recall": second,
    }


def row(case_set: str, question_id: str, round_index: int, arm: str, recall: float) -> dict:
    return {
        "case_set": case_set,
        "question_id": question_id,
        "round": round_index,
        "arm": arm,
        "status": COMPLETED,
        "audit": {"hop_recall": recall},
    }


# --- the arithmetic -------------------------------------------------------

def test_statistics_count_units_not_pairs():
    """Three rounds of one question are one unit, not three."""
    pairs = [
        pair("s", "q1", 0, 0.0, 1.0),
        pair("s", "q1", 1, 0.0, 1.0),
        pair("s", "q1", 2, 0.0, 1.0),
    ]
    summary = summarise_pairs(pairs, ["old", "new"])
    assert summary["comparable_pairs"] == 3
    assert summary["independent_units"] == 1


def test_standard_error_is_stdev_of_unit_means_over_sqrt_units():
    """Hand-computed: unit diffs are [+1, 0] -> mean 0.5, SD 0.7071, SE 0.5."""
    pairs = [
        pair("s", "q1", 0, 0.0, 1.0),
        pair("s", "q1", 1, 0.0, 1.0),
        pair("s", "q1", 2, 0.0, 1.0),
        pair("s", "q2", 0, 0.0, 0.0),
        pair("s", "q2", 1, 0.0, 0.0),
        pair("s", "q2", 2, 0.0, 0.0),
    ]
    summary = summarise_pairs(pairs, ["old", "new"])

    assert summary["independent_units"] == 2
    assert summary["mean_difference"] == pytest.approx(0.5)
    # Sample SD (ddof=1) of [1.0, 0.0] is sqrt(0.5).
    assert summary["stdev"] == pytest.approx(round(math.sqrt(0.5), 4))
    assert summary["standard_error"] == pytest.approx(0.5)
    assert summary["mde"] == pytest.approx(0.98)
    assert summary["ci95"] == pytest.approx((-0.48, 1.48))


def test_two_conventions_disagree_on_whether_the_interval_excludes_zero():
    """The regression that matters: the pair-level convention reports an effect.

    Same six pairs as above.  Pooling all six as independent gives n=6 and
    SE=0.2236, so the interval excludes zero; collapsing to the two questions
    first gives n=2 and SE=0.5, so it does not.  The point estimate is 0.5 under
    both conventions -- only the amount of independent evidence is in dispute,
    which is exactly what the old formula overstated.
    """
    pairs = [
        pair("s", "q1", 0, 0.0, 1.0),
        pair("s", "q1", 1, 0.0, 1.0),
        pair("s", "q1", 2, 0.0, 1.0),
        pair("s", "q2", 0, 0.0, 0.0),
        pair("s", "q2", 1, 0.0, 0.0),
        pair("s", "q2", 2, 0.0, 0.0),
    ]
    diffs = [1.0, 1.0, 1.0, 0.0, 0.0, 0.0]
    pair_se = math.sqrt(sum((d - 0.5) ** 2 for d in diffs) / 5) / math.sqrt(6)
    assert pair_se == pytest.approx(0.2236, abs=1e-4)

    pair_level_ci = (0.5 - 1.96 * pair_se, 0.5 + 1.96 * pair_se)
    assert pair_level_ci[0] > 0, "前提变了：旧口径本应把 0 排除在外"

    unit_level_ci = summarise_pairs(pairs, ["old", "new"])["ci95"]
    assert unit_level_ci[0] < 0 < unit_level_ci[1]

    # And the point estimate is the same either way -- the dispute is about n.
    assert summarise_pairs(pairs, ["old", "new"])["mean_difference_by_pair"] == pytest.approx(0.5)


def test_units_are_keyed_by_case_set_as_well_as_question():
    """The same question id in two case sets is two units, not one."""
    pairs = [
        pair("s1", "q1", 0, 0.0, 1.0),
        pair("s2", "q1", 0, 0.0, 1.0),
    ]
    summary = summarise_pairs(pairs, ["old", "new"])
    assert summary["independent_units"] == 2


def test_one_unit_has_no_interval_rather_than_a_zero_width_one():
    """With a single unit there is no SD to compute, and that must not look like certainty."""
    summary = summarise_pairs([pair("s", "q1", 0, 0.0, 1.0)], ["old", "new"])
    assert summary["independent_units"] == 1
    assert summary["standard_error"] is None
    assert summary["ci95"] is None
    assert summary["mde"] is None


def test_no_comparable_pairs_does_not_raise():
    summary = summarise_pairs([], ["old", "new"])
    assert summary["comparable_pairs"] == 0
    assert summary["independent_units"] == 0
    assert summary["mean_difference"] is None


def test_pairs_missing_one_arm_are_dropped_from_the_statistics():
    incomplete = pair("s", "q1", 0, 0.0, 1.0)
    incomplete["new_hop_recall"] = None
    summary = summarise_pairs([incomplete], ["old", "new"])
    assert summary["comparable_pairs"] == 0


def test_the_second_arm_minus_the_first_arm_is_the_sign_convention():
    """Arm order must control the sign, or a regression reads as an improvement."""
    pairs = [pair("s", "q1", 0, 1.0, 0.0)]
    assert summarise_pairs(pairs, ["old", "new"])["mean_difference"] == pytest.approx(-1.0)
    assert summarise_pairs(pairs, ["new", "old"])["mean_difference"] == pytest.approx(1.0)


# --- the pairing key, and why the defect existed --------------------------

def test_paired_rows_keys_on_round_so_pairs_outnumber_units():
    """Documents the origin of the defect, so nobody "simplifies" the key back."""
    rows = [
        row("s", "q1", index, arm, value)
        for index in (0, 1, 2)
        for arm, value in (("old", 0.0), ("new", 1.0))
    ]
    pairs, dropped = paired_rows(rows, ["old", "new"])
    assert len(pairs) == 3, "配对按 (题, 轮) 展开"
    assert dropped == 0
    assert summarise_pairs(pairs, ["old", "new"])["independent_units"] == 1


def test_paired_rows_counts_the_cell_lost_when_one_arm_fails():
    """A dropped cell is the honest denominator for coverage, not for evidence."""
    rows = [
        row("s", "q1", 0, "old", 0.0),
        row("s", "q1", 0, "new", 1.0),
        row("s", "q1", 1, "old", 0.0),
        {**row("s", "q1", 1, "new", 1.0), "status": "provider_timeout"},
    ]
    pairs, dropped = paired_rows(rows, ["old", "new"])
    assert len(pairs) == 1
    assert dropped == 1


# --- the version, and the dependency boundary -----------------------------

def test_statistics_version_is_not_the_audit_version():
    """Two different things must not share one version number.

    ``AUDIT_VERSION`` versions *how one answer is scored*; this versions *how the
    scores are aggregated*.  Sharing a number would make it impossible to tell,
    after the fact, which of the two changed.
    """
    from src.frozen_evidence import AUDIT_VERSION

    assert PAIRED_STATISTICS_VERSION != AUDIT_VERSION
    assert PAIRED_STATISTICS_VERSION == "paired-unit.v1"


def test_the_harness_re_exports_the_moved_names():
    """``scripts/interleaved_generation_rounds.py`` re-exports, so callers keep working.

    Asserted on the source rather than by importing: that module imports the model
    SDK at module level, and this test file must stay collectible offline.
    """
    source = (ROOT / "scripts" / "interleaved_generation_rounds.py").read_text(
        encoding="utf-8"
    )
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module == "src.paired_statistics":
            imported.update(alias.name for alias in node.names)
    assert imported >= {
        "COMPLETED",
        "PAIRED_STATISTICS_VERSION",
        "paired_rows",
        "summarise_pairs",
    }, f"interleaved_generation_rounds 不再转发这些名字：{imported}"


def test_the_moved_definitions_are_not_duplicated_in_the_harness():
    """One implementation, not two.  A leftover copy would shadow the import."""
    tree = ast.parse(
        (ROOT / "scripts" / "interleaved_generation_rounds.py").read_text(encoding="utf-8")
    )
    defined = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "paired_rows" not in defined
    assert "summarise_pairs" not in defined
