"""Tests for slice loading, group gates and small-sample verdicts.

The slice file, the flaky detector and the statistical tests all decide whether
a change is reported as an improvement.  If any of them is wrong the result is
not a bad number, it is a confidently wrong conclusion, so each is pinned here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.generation_gates import (
    check_run_comparability,
    detect_flaky_questions,
    evaluate_group_gates,
    mcnemar_exact,
    wilcoxon_signed_rank,
)
from src.generation_slices import (
    CASE_TYPE_VALUES,
    RISK_LEVEL_VALUES,
    SliceError,
    group_metric_values,
    load_slices,
    validate_against_contract,
)

ROOT = Path(__file__).resolve().parent.parent
SLICE_PATH = ROOT / "data" / "generation_eval_slices.v1.json"
CONTRACT_PATH = ROOT / "data" / "generation_eval.v2.json"


def contract_cases() -> list[dict]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))["cases"]


# --- slice file -----------------------------------------------------------
def test_shipped_slice_file_loads_and_matches_the_contract():
    slices = load_slices(SLICE_PATH)
    validate_against_contract(slices, contract_cases())
    assert len(slices["by_id"]) == len(contract_cases())


def test_every_slice_declares_a_known_case_type_and_risk_level():
    slices = load_slices(SLICE_PATH)
    for entry in slices["by_id"].values():
        assert entry["case_type"] in CASE_TYPE_VALUES
        assert entry["risk_level"] in RISK_LEVEL_VALUES


def test_high_risk_slices_exist_and_are_the_policy_questions():
    """Risk levels must be meaningful, not decoration."""
    slices = load_slices(SLICE_PATH)
    high = {
        sid for sid, entry in slices["by_id"].items()
        if entry["risk_level"] == "high"
    }
    assert high == {"q12_miss", "q17_ambiguous"}


def test_missing_slice_definition_is_rejected(tmp_path):
    payload = json.loads(SLICE_PATH.read_text(encoding="utf-8"))
    payload["slices"] = [s for s in payload["slices"] if s["id"] != "q05_hit"]
    broken = tmp_path / "slices.json"
    broken.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SliceError, match="缺失"):
        validate_against_contract(load_slices(broken), contract_cases())


def test_unknown_case_type_is_rejected_at_load_time(tmp_path):
    payload = json.loads(SLICE_PATH.read_text(encoding="utf-8"))
    payload["slices"][0]["case_type"] = "made_up"
    broken = tmp_path / "slices.json"
    broken.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SliceError, match="case_type"):
        load_slices(broken)


def test_flaky_field_must_be_a_boolean(tmp_path):
    payload = json.loads(SLICE_PATH.read_text(encoding="utf-8"))
    payload["slices"][0]["flaky"] = "no"
    broken = tmp_path / "slices.json"
    broken.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SliceError, match="flaky"):
        load_slices(broken)


def test_group_metric_values_always_reports_its_denominator():
    slices = load_slices(SLICE_PATH)
    rows = [
        {"question_id": "q01_hit", "audit": {"required_term_recall": 1.0}},
        {"question_id": "q02_hit", "audit": {"required_term_recall": 0.5}},
        {"question_id": "q03_hit", "audit": {"required_term_recall": None}},
    ]
    grouped = group_metric_values(rows, slices, by="case_type", metric="required_term_recall")
    numeric = grouped["fact_numeric"]
    assert numeric["sample_size"] == 2, "None 不得计入分母"
    assert numeric["mean"] == 0.75


def test_group_metric_values_respects_applicability_flags():
    """Boundary questions must not enter citation statistics."""
    slices = load_slices(SLICE_PATH)
    rows = [
        {"question_id": "q01_hit", "audit": {"citation_validity": True,
                                             "citation_metric_applicable": True}},
        {"question_id": "q12_miss", "audit": {"citation_validity": True,
                                              "citation_metric_applicable": False}},
    ]
    grouped = group_metric_values(
        rows, slices, by="case_type", metric="citation_validity",
        applicable_key="citation_metric_applicable",
    )
    assert "refusal" not in grouped, "不适用题不应出现在引用统计里"
    assert grouped["fact_numeric"]["sample_size"] == 1


# --- statistics -----------------------------------------------------------
def test_wilcoxon_reports_no_evidence_when_all_pairs_agree():
    result = wilcoxon_signed_rank([1.0, 1.0, 1.0], [1.0, 1.0, 1.0])
    assert result["n_pairs"] == 0
    assert result["significant"] is False


def test_wilcoxon_detects_a_uniform_shift_at_small_n():
    """Six consistent improvements are enough to be significant, and that is
    the point of using a paired test rather than comparing two means."""
    result = wilcoxon_signed_rank([1.0] * 6, [0.0] * 6)
    assert result["significant"] is True
    assert result["small_sample"] is True


def test_wilcoxon_does_not_call_mixed_signal_significant():
    result = wilcoxon_signed_rank([1, 0, 1, 0, 1, 0], [0, 1, 1, 0, 0, 1])
    assert result["significant"] is False


def test_mcnemar_uses_the_exact_test_at_small_discordant_counts():
    result = mcnemar_exact([True] * 8, [False] * 8)
    assert result["method"] == "exact"
    assert result["small_sample"] is True
    assert result["significant"] is True


def test_mcnemar_ignores_symmetric_discordance():
    result = mcnemar_exact([True, False, True, False], [False, True, False, True])
    assert result["discordant"] == 4
    assert result["significant"] is False


def test_mcnemar_reports_no_discordance_without_failing():
    result = mcnemar_exact([True, True], [True, True])
    assert result["discordant"] == 0
    assert result["significant"] is False


# --- flaky detection ------------------------------------------------------
def test_flaky_detection_flags_a_question_that_flips_verdicts():
    result = detect_flaky_questions(
        {"q_stable": [True, True, True], "q_flips": [True, False, True]},
        metric="ambiguity_safety",
    )
    assert result["flaky_question_ids"] == ["q_flips"]
    assert result["details"][0]["flip_pairs"] == 2


def test_flaky_detection_ignores_questions_with_too_few_runs():
    result = detect_flaky_questions({"q_once": [True]}, metric="ambiguity_safety")
    assert result["flaky_question_ids"] == []


def test_flaky_detection_skips_not_applicable_verdicts():
    """A question that was never applicable has no verdict to flip."""
    result = detect_flaky_questions(
        {"q_na": [None, None, True]}, metric="ambiguity_safety"
    )
    assert result["flaky_question_ids"] == []


# --- comparability --------------------------------------------------------
def test_runs_within_tolerance_are_comparable():
    result = check_run_comparability([{"x": 0.90}, {"x": 0.95}], keys=["x"])
    assert result["comparable"] is True


def test_runs_beyond_tolerance_are_refused():
    result = check_run_comparability([{"x": 0.90}, {"x": 0.60}], keys=["x"])
    assert result["comparable"] is False
    assert result["unstable_metrics"][0]["metric"] == "x"


# --- group gates ----------------------------------------------------------
GROUPS = {
    "retrieval": {"metrics": ["retrieval_calls"], "allowed_change": 0.0, "enforcement": "hard"},
    "quality": {"metrics": ["span_recall"], "allowed_change": 0.05, "enforcement": "gate"},
    "usability": {"metrics": ["timeout_rate"], "direction": "lower_is_better",
                  "allowed_change": 0.0, "enforcement": "hard"},
}


def test_undeclared_group_may_not_move_at_all():
    """The declared group moves within tolerance; the undeclared one must not
    move even by a hair, because that is a side effect nobody asked for."""
    metrics = {"span_recall": {"baseline": 0.80, "current": 0.83, "delta": 0.03},
               "retrieval_calls": {"baseline": 0, "current": 1, "delta": 1}}
    result = evaluate_group_gates(GROUPS, metrics, declared_changes=["quality"])
    assert result["groups"]["quality"]["passed"] is True
    assert result["groups"]["retrieval"]["passed"] is False
    assert result["all_groups_passed"] is False


def test_undeclared_group_fails_on_an_improvement_too():
    """An undeclared group improving still means the change was not scoped."""
    metrics = {"span_recall": {"baseline": 0.80, "current": 0.82, "delta": 0.02},
               "timeout_rate": {"baseline": 0.20, "current": 0.05, "delta": -0.15}}
    result = evaluate_group_gates(GROUPS, metrics, declared_changes=["quality"])
    assert result["groups"]["usability"]["passed"] is False


def test_declared_group_may_move_within_tolerance_only():
    metrics = {"span_recall": {"baseline": 0.80, "current": 0.90, "delta": 0.10}}
    result = evaluate_group_gates(GROUPS, metrics, declared_changes=["quality"])
    assert result["groups"]["quality"]["passed"] is False


def test_declared_group_may_not_fall_past_tolerance():
    """``allowed_change`` is a band, not just a ceiling.

    Reading it as a ceiling let a declared group fall arbitrarily far and still
    pass -- "quality may fluctuate by 0.05" was accepted as cover for a 0.17
    drop.  A tolerance that only limits gains is not a tolerance.
    """
    metrics = {"span_recall": {"baseline": 1.0, "current": 0.833, "delta": -0.167}}
    result = evaluate_group_gates(GROUPS, metrics, declared_changes=["quality"])
    assert result["groups"]["quality"]["passed"] is False
    detail = result["groups"]["quality"]["metrics"][0]
    assert detail["verdict"] == "breach"
    assert detail["breach_reason"] == "beyond_tolerance"


def test_declared_group_passes_just_inside_the_band_on_a_drop():
    metrics = {"span_recall": {"baseline": 1.0, "current": 0.97, "delta": -0.03}}
    result = evaluate_group_gates(GROUPS, metrics, declared_changes=["quality"])
    assert result["groups"]["quality"]["passed"] is True


def test_lower_is_better_group_regresses_on_a_rise():
    metrics = {"timeout_rate": {"baseline": 0.0, "current": 0.1, "delta": 0.1}}
    result = evaluate_group_gates(GROUPS, metrics, declared_changes=["timeout_rate"])
    assert result["groups"]["usability"]["passed"] is False


def test_lower_is_better_group_tolerates_an_improvement_when_declared():
    """Once declared, a lower-is-better metric may fall without limit."""
    metrics = {"timeout_rate": {"baseline": 0.2, "current": 0.0, "delta": -0.2}}
    result = evaluate_group_gates(GROUPS, metrics, declared_changes=["usability"])
    assert result["groups"]["usability"]["passed"] is True


def test_gate_reports_missing_baseline_rather_than_guessing():
    metrics = {"span_recall": {"baseline": None, "current": 0.9, "delta": None}}
    result = evaluate_group_gates(GROUPS, metrics, declared_changes=["quality"])
    assert result["groups"]["quality"]["metrics"][0]["verdict"] == "no_baseline"
