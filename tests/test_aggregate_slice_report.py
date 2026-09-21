"""Tests for the aggregator's slice-report integration.

``aggregate_generation_replays`` used to produce one flat summary.  It now also
attaches a slice breakdown, flaky detection and (when a baseline is supplied)
group gates.  These tests pin that wiring, since a report that silently omits a
section would look like "nothing to report".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.gates_runner import build_slice_report, comparability_report, paired_comparison
from src.generation_slices import load_slices

ROOT = Path(__file__).resolve().parent.parent
SLICES = ROOT / "data" / "generation_eval_slices.v1.json"
CONTRACT = ROOT / "data" / "generation_eval.v2.json"


def contract_cases() -> list[dict]:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))["cases"]


def _row(question_id: str, **audit):
    base = {
        "required_term_recall": 1.0,
        "citation_validity": True,
        "citation_metric_applicable": True,
        "refusal_correctness": None,
        "ambiguity_safety": None,
    }
    base.update(audit)
    return {"question_id": question_id, "status": "completed", "audit": base}


def all_question_ids() -> list[str]:
    return [c["id"] for c in contract_cases()]


def test_slice_report_covers_every_dimension_and_metric():
    rows = [
        _row(qid, required_term_recall=1.0, citation_validity=True)
        for qid in all_question_ids()
    ]
    report = build_slice_report(
        rows=rows, slices_path=SLICES, contract_cases=contract_cases()
    )
    keys = report["slice_breakdown"]
    for dimension in ("case_type", "risk_level"):
        for metric in (
            "required_term_recall", "citation_validity",
            "refusal_correctness", "ambiguity_safety",
        ):
            assert f"{dimension}:{metric}" in keys


def test_slice_report_keeps_boundary_questions_out_of_citation_stats():
    rows = [
        _row(qid, citation_metric_applicable=(qid not in ("q12_miss", "q17_ambiguous")))
        for qid in all_question_ids()
    ]
    report = build_slice_report(
        rows=rows, slices_path=SLICES, contract_cases=contract_cases()
    )
    citation = report["slice_breakdown"]["case_type:citation_validity"]
    assert "refusal" not in citation
    assert "ambiguous" not in citation
    assert citation["fact_numeric"]["sample_size"] == 6


def test_slice_report_detects_a_question_that_flips_between_pass_and_fail():
    """Two runs of the same system disagreeing on one question is a metric
    problem, and the question must leave the regression denominator."""
    rows = [_row(qid) for qid in all_question_ids()]
    rows += [_row(qid) for qid in all_question_ids()]
    # q17 flips: True in the first run, False in the second.
    for row in rows:
        if row["question_id"] == "q17_ambiguous":
            row["audit"]["ambiguity_safety"] = True
    for row in rows[len(all_question_ids()):]:
        if row["question_id"] == "q17_ambiguous":
            row["audit"]["ambiguity_safety"] = False

    report = build_slice_report(
        rows=rows, slices_path=SLICES, contract_cases=contract_cases()
    )
    assert "q17_ambiguous" in report["flaky_detection"]["ambiguity_safety"]["flaky_question_ids"]
    assert "q17_ambiguous" in report["excluded_question_ids"]


def test_declared_flaky_questions_are_excluded_from_the_denominator(tmp_path):
    payload = json.loads(SLICES.read_text(encoding="utf-8"))
    for entry in payload["slices"]:
        if entry["id"] == "q01_hit":
            entry["flaky"] = True
    custom = tmp_path / "slices.json"
    custom.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    rows = [_row(qid) for qid in all_question_ids()]
    report = build_slice_report(
        rows=rows, slices_path=custom, contract_cases=contract_cases()
    )
    assert "q01_hit" in report["excluded_question_ids"]
    numeric = report["slice_breakdown"]["case_type:required_term_recall"]["fact_numeric"]
    assert "q01_hit" not in numeric["question_ids"]


def test_group_gate_is_only_produced_when_a_baseline_is_supplied():
    rows = [_row(qid) for qid in all_question_ids()]
    without = build_slice_report(
        rows=rows, slices_path=SLICES, contract_cases=contract_cases()
    )
    assert without["group_gate"] is None

    with_baseline = build_slice_report(
        rows=rows,
        slices_path=SLICES,
        contract_cases=contract_cases(),
        declared_changes=["quality"],
        baseline_summary={"required_term_recall_mean": 1.0,
                          "answer_span_recall_mean": 1.0},
        current_summary={"required_term_recall_mean": 1.0,
                         "answer_span_recall_mean": 1.0},
    )
    assert with_baseline["group_gate"] is not None
    assert with_baseline["group_gate"]["declared_changes"] == ["quality"]


def test_group_gate_flags_an_undeclared_group_that_moved():
    rows = [_row(qid) for qid in all_question_ids()]
    report = build_slice_report(
        rows=rows,
        slices_path=SLICES,
        contract_cases=contract_cases(),
        declared_changes=["quality"],
        baseline_summary={"answer_span_recall_mean": 1.0,
                          "provider_timeout_rate": 0.0},
        # Usability moved even though only quality was declared.
        current_summary={"answer_span_recall_mean": 1.0,
                         "provider_timeout_rate": 0.25},
    )
    assert report["group_gate"]["all_groups_passed"] is False
    assert report["group_gate"]["groups"]["usability"]["passed"] is False


def test_paired_comparison_pairs_by_question_not_by_mean():
    base = [_row(qid, required_term_recall=0.5) for qid in all_question_ids()]
    cur = [_row(qid, required_term_recall=1.0) for qid in all_question_ids()]
    result = paired_comparison(base, cur, metric="required_term_recall")
    assert result["comparable_pairs"] == len(all_question_ids())
    assert result["significant"] is True


def test_paired_comparison_uses_the_exact_test_for_binary_metrics():
    base = [_row(qid, refusal_correctness=None) for qid in all_question_ids()]
    cur = [_row(qid, refusal_correctness=None) for qid in all_question_ids()]
    for row in base[:8]:
        row["audit"]["recall_at_1"] = 0.0
    for row in cur[:8]:
        row["audit"]["recall_at_1"] = 1.0
    result = paired_comparison(
        base, cur, metric="recall_at_1", kind="binary"
    )
    assert result["test"] == "mcnemar_exact"


def test_paired_comparison_reports_when_there_is_nothing_to_pair():
    result = paired_comparison([], [], metric="required_term_recall")
    assert result["comparable_pairs"] == 0


def test_comparability_refuses_to_trend_incomparable_runs():
    stable = comparability_report(
        [{"required_term_recall_mean": 1.0}, {"required_term_recall_mean": 0.95}]
    )
    assert stable["comparable"] is True

    unstable = comparability_report(
        [{"required_term_recall_mean": 1.0}, {"required_term_recall_mean": 0.5}]
    )
    assert unstable["comparable"] is False
    assert unstable["unstable_metrics"][0]["metric"] == "required_term_recall_mean"


def test_aggregator_attaches_slice_report_to_its_payload(tmp_path, monkeypatch):
    """End-to-end: the CLI path must actually attach the slice section."""
    import aggregate_generation_replays as aggregator

    rows = [
        {
            "question_id": qid,
            "status": "completed",
            "generation_latency_seconds": 1.0,
            "audit": {
                "required_term_recall": 1.0,
                "citation_validity": True,
                "citation_metric_applicable": True,
                "answer_length": 10,
            },
        }
        for qid in all_question_ids()
    ]
    run_files = []
    for index, repetition in enumerate((1, 2), start=1):
        path = tmp_path / f"run_{index}.json"
        path.write_text(
            json.dumps(
                {
                    "model": "m", "snapshot_sha256": "s", "evaluation_sha256": "e",
                    "prompt_version": "p", "audit_version": "a",
                    "repetition_index": repetition, "retrieval_calls": 0,
                    "dry_run": False, "rows": rows,
                    "generation_max_retries": 0,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        run_files.append(path)

    output = tmp_path / "agg.json"
    monkeypatch.setattr(
        "sys.argv",
        ["aggregate", "--output", str(output), *[str(p) for p in run_files]],
    )
    assert aggregator.main() == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert "slice_report" in payload
    assert "run_comparability" in payload
    assert payload["slice_report"]["slice_breakdown"]
