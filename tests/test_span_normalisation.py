"""The declared spans must be matchable, and the matcher must not invent credit.

Two opposite failures are guarded here, and the second is the dangerous one:

* a span that **no answer can match** -- the target destroyed before comparison;
* a matcher loose enough to credit an answer that never brought the fact out,
  which would raise the score while looking like diligence.

The audit (`data/span_audit.v1.json`) found 22 real paraphrases and 11 real
misses in the same shape, so widening the regex to catch paraphrases credits
both.  The resolution is that paraphrases are **declared by the contract**, which
is what `expected_span_alternatives` is for.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_span_normalisation import collect_spans, inspect  # noqa: E402
from src.frozen_evidence import (  # noqa: E402
    _normalise_text,
    _span_matches,
    _strip_span_noise,
)

TAPER_SPAN = "V = π × h × (D1² + D1 × D2 + D2²) / 12 / 1000"

# Spans that still lose tokens to normalisation.  All of these lose them on
# *both* sides identically, which is harmless; the set is pinned so a new one
# shows up as a diff to be judged rather than as a silent change in what the
# metric can measure.
KNOWN_TOKEN_LOSS = {
    ("generation_eval.multihop.real.v1.json", "2", "h1", ("mL", "mm")),
}


def _all_spans():
    return collect_spans(
        sorted(Path(p) for p in glob.glob(str(ROOT / "data" / "generation_eval*.json")))
    )


# ---------------------------------------------------------------------------
# No declared span may be unmatchable.
# ---------------------------------------------------------------------------


def test_no_declared_span_is_unmatchable():
    """An answer quoting the contract verbatim must be credited.

    This is the criterion that failed before v7: `_SPAN_BARE_LABEL_RE` deleted
    `D1`/`D2` as if they were table labels, and `_normalise_text` deleted the
    operators so `/ 12 / 1000` fused into `121000`.
    """
    broken = [
        (item["source"], item["question_id"], item["hop_id"])
        for item in _all_spans()
        if not inspect(item["span"])["matches_itself"]
    ]
    assert broken == []


def test_the_taper_formula_keeps_its_variables():
    """The specific span that was destroyed, checked end to end."""
    report = inspect(TAPER_SPAN)
    assert report["matches_itself"] is True
    assert report["tokens_lost"] == []
    for variable in ("d1", "d2"):
        assert variable in report["normalised"]


def test_superscript_and_latex_exponents_are_the_same_formula():
    """`D1²` and `D_1^2` are one formula; a LaTeX-rendering model writes the second."""
    assert _normalise_text("D1²") == _normalise_text("D_1^2")
    assert _normalise_text("内径²(mm)") == _normalise_text("内径^2(mm)")


def test_token_loss_is_pinned():
    found = set()
    for item in _all_spans():
        report = inspect(item["span"])
        if report["tokens_lost"]:
            found.add(
                (
                    item["source"],
                    item["question_id"],
                    str(item["hop_id"]),
                    tuple(report["tokens_lost"]),
                )
            )
    assert found == KNOWN_TOKEN_LOSS


# ---------------------------------------------------------------------------
# The answer keeps its parentheses.
# ---------------------------------------------------------------------------


def test_answer_parentheses_are_not_stripped():
    """An answer's brackets hold facts; a contract's hold units.

    `mh03 h2` scored zero with `未成年人（4～17岁）人体尺寸` plainly in the text,
    because the same regex that removes `(mL)` from a contract also removed the
    age range from the answer.
    """
    answer = "GB/T 26158—2010 覆盖中国未成年人（4～17岁）人体尺寸，分为五个年龄组"
    assert _span_matches("4岁~17岁", answer) is True


def test_only_the_contract_loses_its_parentheses():
    """The brackets go on both sides; only the *content* is protected.

    `_normalise_text` still deletes the bracket characters themselves -- it
    deletes every symbol.  What v7 stopped doing is deleting what is *inside*
    them on the answer side.
    """
    assert "4~17岁" in _normalise_text(_strip_span_noise("未成年人（4～17岁）", parens=False))
    assert "4~17岁" not in _normalise_text(_strip_span_noise("未成年人（4～17岁）"))


def test_a_contracts_unit_annotation_may_still_be_dropped():
    """`内径(mm)` and `内径` are the same requirement."""
    assert _span_matches("内径(mm)", "按内径计算") is True
    assert _span_matches("有效液高(mm)", "有效液高为 120 mm") is True


def test_number_fusion_is_still_unfixed():
    """A known remaining defect, pinned rather than quietly tolerated.

    `_normalise_text` deletes the operators that separate numbers, so `12 / 1000`
    becomes the token `121000`, which appears in no answer.  It is not fixed here
    because the numeric fallback usually recovers the case, and changing
    number extraction would move scores far beyond the spans this version
    targets.
    """
    assert _normalise_text("12 / 1000") == "121000"
    assert _normalise_text("12 × 1000") == "121000"


# ---------------------------------------------------------------------------
# Alternatives are declared, reviewed decisions -- not a looser regex.
# ---------------------------------------------------------------------------


def test_a_declared_alternative_credits_a_reviewed_paraphrase():
    answer = "若反推得到的有效液高使总高度过大，应检查重心是否过高、倾倒风险是否增加。"
    assert _span_matches("检查高度是否导致重心过高", answer) is False
    assert (
        _span_matches("检查高度是否导致重心过高", answer, alternatives=["重心是否过高"])
        is True
    )


def test_an_alternative_does_not_excuse_an_unrelated_answer():
    """The guard against alternatives becoming a way to credit everything.

    The audit found real misses next to real paraphrases; an alternative that
    matched on a topic word alone would credit both.
    """
    assert (
        _span_matches(
            "检查高度是否导致重心过高", "座高应为 400~440 mm。", alternatives=["重心是否过高"]
        )
        is False
    )


def test_alternatives_are_empty_by_default():
    """A hop that declares none must behave exactly as before."""
    answer = "应检查重心是否过高"
    assert _span_matches("检查高度是否导致重心过高", answer) is False
    assert _span_matches("检查高度是否导致重心过高", answer, alternatives=()) is False


# ---------------------------------------------------------------------------
# Known limitation, pinned so it cannot be mistaken for a fix.
# ---------------------------------------------------------------------------


def test_a_latex_rendered_formula_still_needs_a_declared_alternative():
    """`\\frac{...}{...}` inserts letters between the symbols.

    The variables now survive, so the span is no longer destroyed -- but a LaTeX
    rendering still does not contain the linear form, and fixing that is a
    contract decision (`expected_span_alternatives`) rather than a regex one.
    """
    answer = (
        "按锥台近似公式：\\[ V=\\frac{\\pi h(D_1^2+D_1D_2+D_2^2)}{12\\times1000} \\]"
        " 得 332.3 mL"
    )
    assert _span_matches(TAPER_SPAN, answer) is False
    # With the rendering declared, the same answer is credited.
    assert (
        _span_matches(TAPER_SPAN, answer, alternatives=["D_1^2+D_1D_2+D_2^2"]) is True
    )


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


# ---------------------------------------------------------------------------
# A term must survive normalisation, or it can never be satisfied.
#
# `P5`, `P50` and `P95` all match `[A-Za-z]\d{1,2}` -- the bare-label strip that
# exists to remove table labels from *spans*.  Applied to a declared *term* it
# deleted all three, and the matcher rejects an empty needle, so `p08 h2` was a
# permanent false negative: no answer could ever satisfy it.
# ---------------------------------------------------------------------------


def test_percentile_terms_are_not_deleted_by_the_label_strip():
    from src.frozen_evidence import _normalise_text, _strip_span_noise

    for term, expected in (("P5", "p5"), ("P50", "p50"), ("P95", "p95")):
        assert _normalise_text(_strip_span_noise(term, labels=False)) == expected
        # The old behaviour is kept as a contrast: this is what broke it.
        assert _normalise_text(_strip_span_noise(term)) == ""


def test_a_percentile_term_is_findable_in_an_answer_that_names_it():
    """The whole point: the needle has to survive into the comparison.

    This mirrors what the term matcher does -- normalise the term without the
    label strip, normalise the answer, look for the needle.
    """
    from src.frozen_evidence import _normalise_text, _strip_span_noise

    answer = "该标准覆盖 4～6、7～10 岁分组及 P5、P50、P95 等百分位数据。"
    haystack = _normalise_text(
        _strip_span_noise(answer, parens=False, labels=False)
    )
    for term in ("P5", "P50", "P95"):
        needle = _normalise_text(_strip_span_noise(term, labels=False))
        assert needle, f"{term} 规范化后为空，永远匹配不上"
        assert needle in haystack, f"{term} 在答案里找不到"


def test_the_builder_rejects_a_term_that_normalises_to_nothing():
    """A contract bug must fail the build, not score zero forever.

    A term that can never match does not look like a defect -- it looks like the
    hop being hard, which is how `p08 h2` stayed broken.
    """
    import json as _json
    import tempfile
    from pathlib import Path as _Path

    import scripts.build_multihop_snapshot as builder

    spec = {
        "id": "c1",
        "question": "q",
        "sources": ["s"],
        "hops": [
            {
                "hop_id": "h1",
                "from_question": "s",
                "chunk_id": "c",
                "expected_span": "x",
                # A term that is only a bracketed unit annotation normalises
                # to nothing once the parentheses come off, so no answer can
                # ever satisfy it.
                "required_terms": [["(mL)"]],
            },
            {
                "hop_id": "h2",
                "from_question": "s",
                "chunk_id": "c",
                "expected_span": "x",
                "required_terms": [["座高"]],
            },
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = _Path(tmp) / "specs.json"
        path.write_text(
            _json.dumps({"version": builder.SPEC_VERSION, "cases": [spec]}),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit, match="规范化后为空"):
            builder.load_specs(path)
