"""The declared spans must survive normalisation with their content intact.

`hop_recall` compares an answer against `expected_span` only after normalising
both.  Normalisation is lossy, and a *formula* is exactly the shape it damages:
`_SPAN_BARE_LABEL_RE` cannot tell a formula variable (`D1`) from a table label
(`T1`), and `_normalise_text` deletes the operators that separate numbers, so
`/ 12 / 1000` fuses into `121000`.

A span that has been destroyed this way is not scored harshly -- it cannot be
scored at all.  These tests pin the damage so it cannot grow silently, and pin
the mechanism so a future fix is recognised as a fix rather than a regression.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_span_normalisation import collect_spans, inspect  # noqa: E402
from src.frozen_evidence import _normalise_text, _span_matches  # noqa: E402

# The spans the guard currently reports as damaged by label-stripping.  Every
# one of these declares a formula whose variables look like table labels.
# Adding a span to this list is a decision, not a convenience: it means a hop
# can no longer be credited no matter how well it is answered.
KNOWN_LABEL_DAMAGED = {
    ("generation_eval.multihop.real.v1.json", "4", "h1"),
    ("generation_eval.multihop.real.complete.v2.json", "4", "h1"),
    ("generation_eval.multihop.real.partial.v2.json", "2", "h2"),
}

# Reported by the guard but *not* failed: the regex cannot tell a unit in
# brackets from content in brackets, so these need a human to accept them.
KNOWN_PARENS_ONLY = {
    ("generation_eval.multihop.real.v1.json", "2", "h1"),
    ("generation_eval.multihop.real.complete.v2.json", "2", "h1"),
    ("generation_eval.multihop.real.partial.v2.json", "0", "h3"),
    ("generation_eval.multihop.real.partial.v2.json", "5", "h1"),
}

TAPER_SPAN = "V = π × h × (D1² + D1 × D2 + D2²) / 12 / 1000"


def test_label_stripping_deletes_formula_variables():
    """The mechanism: `D1`/`D2` are removed as if they were table labels."""
    report = inspect(TAPER_SPAN)
    assert report["labels_removed"] == ["D1", "D2"]
    assert "D1" not in report["after_labels"]
    assert "D2" not in report["after_labels"]


def test_number_separators_fuse_after_normalisation():
    """`/ 12 / 1000` loses its slashes and becomes the token `121000`."""
    assert inspect(TAPER_SPAN)["normalised"] == "vh121000"
    # Nothing can contain this: it is an artefact of deleting the operators.
    assert "121000" not in "v=pi*h*(d1^2+d1*d2+d2^2)/12/1000"


def test_a_correctly_rendered_formula_still_fails_to_match():
    """The defect, end to end: the model writes the formula and scores zero.

    This is the LaTeX rendering taken from `data/runs/mhreal_r3.json` (r19).
    It is the same formula; only the notation differs.  If this test ever starts
    passing, the matcher has been fixed -- and that fix changes every historical
    score, so `AUDIT_VERSION` must move with it.
    """
    answer = (
        "按锥台近似公式计算：  \\[ V=\\frac{\\pi h(D_1^2+D_1D_2+D_2^2)}"
        "{12\\times1000} \\] 代入： V≈332.3 mL"
    )
    assert _span_matches(TAPER_SPAN, answer) is False


def test_a_plain_span_without_labels_is_untouched():
    """The guard must not cry wolf: ordinary spans pass through unchanged."""
    span = "本标准适用于扶手椅、靠背椅、折叠椅的设计和生产"
    report = inspect(span)
    assert report["labels_removed"] == []
    assert report["parens_removed"] == []
    assert report["normalised"] == _normalise_text(span)


def test_parenthetical_removal_is_reported_separately_from_label_damage():
    """`(mL)` is harmless; `(4岁~17岁)` is content.  The regex cannot tell them
    apart, so the guard reports both and fails only on label damage."""
    unit = inspect("有效容量 V(mL) = π × 内径² / 4 / 1000")
    assert unit["parens_removed"] == ["(mL)"]
    assert unit["labels_damage"] is False
    assert unit["parens_damage"] is True

    age = inspect("本标准给出了未成年人(4岁~17岁)72项人体尺寸")
    assert age["parens_removed"] == ["(4岁~17岁)"]
    assert age["labels_damage"] is False


@pytest.mark.parametrize("path", sorted({name for name, _, _ in KNOWN_LABEL_DAMAGED}))
def test_committed_contracts_contain_only_the_known_damaged_spans(path):
    """A newly introduced formula span must fail here, not in a scored run."""
    spans = collect_spans([ROOT / "data" / path])
    damaged = {
        (item["source"], item["question_id"], str(item["hop_id"]))
        for item in spans
        if inspect(item["span"])["labels_damage"]
    }
    expected = {
        (name, question_id, hop_id)
        for name, question_id, hop_id in KNOWN_LABEL_DAMAGED
        if name == path
    }
    assert damaged == expected


def test_parens_only_damage_is_pinned_too():
    """Parenthetical removal must not grow silently either.

    It does not fail the guard, because removing `(mL)` is right and removing
    `(4岁~17岁)` is wrong and the regex sees them identically.  Pinning the set
    means a new one shows up as a diff to be accepted, not as a quiet change in
    what the metric can measure.
    """
    found = set()
    for name, _, _ in KNOWN_LABEL_DAMAGED | KNOWN_PARENS_ONLY:
        for item in collect_spans([ROOT / "data" / name]):
            report = inspect(item["span"])
            if report["parens_damage"] and not report["labels_damage"]:
                found.add((item["source"], item["question_id"], str(item["hop_id"])))
    assert found == KNOWN_PARENS_ONLY


# ---------------------------------------------------------------------------
# The sensitivity analysis must keep bracketing the answer.
# ---------------------------------------------------------------------------


def _write_run(tmp_path, hops):
    """A minimal replay-shaped run whose single question carries `hops`."""
    row = {
        "question_id": "q",
        "status": "completed",
        "answer": "answer",
        "audit": {
            "hop_metric_applicable": True,
            "hop_results": [
                {"hop_id": f"h{index}", "matched": matched, "expected_span": span}
                for index, (span, matched) in enumerate(hops)
            ],
        },
    }
    path = tmp_path / "run.json"
    path.write_text(json.dumps({"rows": [row]}, ensure_ascii=False), encoding="utf-8")
    return path


def test_a_damaged_hop_is_removed_from_the_clean_denominator(tmp_path):
    """The upper bound must drop the unmeasurable observation, not the hit."""
    from scripts.quantify_span_defect import measure

    path = _write_run(tmp_path, [(TAPER_SPAN, False), ("干净跨段", True)])
    result = measure(path, {TAPER_SPAN})
    assert result["hops_total"] == 2
    assert result["hops_matched"] == 1
    assert result["hops_on_damaged_span"] == 1
    assert result["hops_on_clean_span"] == 1
    assert result["ratio_lower"] == 0.5
    assert result["ratio_upper"] == 1.0


def test_the_upper_bound_is_never_below_the_lower_bound(tmp_path):
    """Removing observations can only shrink the denominator.

    If this inverts, the bracket is meaningless -- and a meaningless bracket is
    worse than no bracket, because it looks like a result.
    """
    from scripts.quantify_span_defect import measure

    for hops in (
        [(TAPER_SPAN, False)],
        [(TAPER_SPAN, True)],
        [(TAPER_SPAN, False), ("干净", False)],
        [("干净", True), ("干净2", True)],
        [],
    ):
        result = measure(_write_run(tmp_path, hops), {TAPER_SPAN})
        lower, upper = result["ratio_lower"], result["ratio_upper"]
        if lower is None or upper is None:
            continue
        assert upper >= lower, f"{hops} -> {lower} > {upper}"


def test_a_clean_span_failure_is_not_excused_by_the_analysis(tmp_path):
    """A hop that fails on a *clean* span must stay in the denominator.

    This is the guard against the analysis quietly becoming a way to write off
    every failure as a metric problem.
    """
    from scripts.quantify_span_defect import measure

    path = _write_run(tmp_path, [("干净但答错了", False)])
    result = measure(path, {TAPER_SPAN})
    assert result["hops_on_damaged_span"] == 0
    assert result["ratio_lower"] == 0.0
    assert result["ratio_upper"] == 0.0
