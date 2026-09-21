"""Tests for the Markdown report renderer.

The report is the artefact a reviewer actually reads, and the failure mode that
matters is not a crash -- it is a number shown without its denominator, or a
caveat that was dropped.  A report that silently omits the credibility section
reads exactly like a report where the runs were comparable.  These tests pin the
caveats, not the layout.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.eval_report import render_report, write_report

ROOT = Path(__file__).resolve().parent.parent
SLICES = ROOT / "data" / "generation_eval_slices.v1.json"
CONTRACT = ROOT / "data" / "generation_eval.v2.json"


def contract_cases() -> list[dict]:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))["cases"]


def _payload(**overrides):
    """Minimal aggregation payload, shaped like ``aggregate()`` output."""
    base = {
        "experiment": "generation-replay-p0-matrix",
        "run_count": 2,
        "repetition_indices": [1, 2],
        "model": "gpt-5.6",
        "prompt_version": "generator-v2-20260919-r2",
        "audit_version": "soft-audit-behaviour-v3",
        "snapshot_sha256": "a" * 64,
        "evaluation_sha256": "b" * 64,
        "generation_max_retries": 0,
        "retrieval_calls": 0,
        "total_question_runs": 24,
        "completed_question_runs": 15,
        "generation_success_rate": 0.625,
        "provider_timeout_rate": 0.375,
        "provider_hard_error_rate": 0.0,
        "provider_rate_limit_rate": 0.0,
        "provider_stability_gate_threshold": 0.10,
        "provider_stability_gate_passed": False,
        "first_try_success_count": 15,
        "retried_success_count": 0,
        "retried_success_question_ids": [],
        "generation_latency_seconds": {"p50": 9.7, "p95": 17.5, "max": 33.5},
        "per_attempt_latency_seconds": {
            "p50": 9.7, "p95": 17.5, "max": 33.5, "sample_size": 15,
        },
        "latency_ceiling_seconds": 60,
        "latency_headroom_seconds": 26.55,
        "answer_span_recall_mean": 0.909,
        "span_metric_sample_size": 11,
        "required_term_recall_mean": 0.955,
        "required_term_metric_sample_size": 11,
        "citation_validity_rate": 1.0,
        "citation_id_usage_ratio_mean": 0.264,
        "source_coverage_mean": 1.0,
        "citation_metric_sample_size": 11,
        "unsupported_number_count_mean": 0.267,
        "answer_length_mean": 196.333,
        "refusal_correctness_rate": 1.0,
        "refusal_metric_sample_size": 2,
        "ambiguity_safety_rate": 1.0,
        "ambiguity_metric_sample_size": 2,
        "per_question": {
            "q01_hit": {"completed": 2, "runs": 2, "success_rate": 1.0,
                        "required_term_recall_mean": 1.0},
            "q04_hit": {"completed": 0, "runs": 2, "success_rate": 0.0,
                        "required_term_recall_mean": None},
        },
        "run_comparability": {
            "comparable": True,
            "tolerance": 0.10,
            "unstable_metrics": [],
            "verdict": "同配置多次运行的差异在容差内，可作为对照",
        },
    }
    base.update(overrides)
    return base


def _slice_report(**overrides):
    base = {
        "slice_file": str(SLICES),
        "excluded_question_ids": [],
        "flaky_detection": {},
        "slice_breakdown": {
            "case_type:required_term_recall": {
                "fact_numeric": {
                    "sample_size": 12, "question_count": 6,
                    "mean": 0.583,
                    "question_ids": [f"q{i:02d}_hit" for i in range(1, 7)],
                },
            },
            "case_type:citation_validity": {
                "fact_numeric": {
                    "sample_size": 7, "question_count": 4, "mean": 1.0,
                    "question_ids": ["q01_hit", "q02_hit", "q03_hit", "q05_hit"],
                },
            },
            "case_type:refusal_correctness": {
                "refusal": {
                    "sample_size": 2, "question_count": 1, "mean": 1.0,
                    "question_ids": ["q12_miss"],
                },
            },
            "case_type:ambiguity_safety": {
                "ambiguous": {
                    "sample_size": 2, "question_count": 1, "mean": 1.0,
                    "question_ids": ["q17_ambiguous"],
                },
            },
        },
        "group_gate": None,
        "note": "",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------
def test_report_renders_all_five_sections_in_dependency_order():
    text = render_report(_payload(slice_report=_slice_report()))
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert [h.split("：")[0] for h in headings[:5]] == [
        "## 一、实验可信度",
        "## 二、系统可用性",
        "## 三、回答质量",
        "## 四、安全行为",
        "## 五、引用质量",
    ]


def test_quality_section_states_its_denominator():
    """A mean without its sample size is the defect this section exists to prevent."""
    text = render_report(_payload(slice_report=_slice_report()))
    quality = text.split("## 三、回答质量")[1].split("## 四、")[0]
    assert "n=11" in quality


def test_span_recall_is_rendered_not_dropped():
    """The aggregator once omitted this field entirely; the report showed an em dash."""
    text = render_report(_payload(slice_report=_slice_report()))
    quality = text.split("## 三、回答质量")[1].split("## 四、")[0]
    assert "0.909" in quality


def test_safety_section_distinguishes_question_count_from_observation_count():
    """1 question scored twice is not the same denominator as 2 questions once."""
    text = render_report(_payload(slice_report=_slice_report()))
    safety = text.split("## 四、安全行为")[1].split("## 五、")[0]
    assert "q12_miss" in safety
    # question_count comes from the slice report, observation count from the summary
    assert "| 1 |" in safety or "| 1 " in safety
    assert "| 2 |" in safety or "| 2 " in safety


def test_citation_section_notes_only_factual_questions_are_counted():
    text = render_report(_payload(slice_report=_slice_report()))
    citation = text.split("## 五、引用质量")[1].split("## 闸门")[0]
    assert "只对事实题统计" in citation


# ---------------------------------------------------------------------------
# Caveats must survive: each one is a claim the reader would otherwise make
# ---------------------------------------------------------------------------
def test_incomparable_runs_suppress_trend_reading_in_section_one():
    payload = _payload(
        slice_report=_slice_report(),
        run_comparability={
            "comparable": False,
            "tolerance": 0.10,
            "unstable_metrics": [{
                "metric": "answer_span_recall_mean",
                "values": [1.0, 0.833], "spread": 0.167, "tolerance": 0.10,
            }],
            "verdict": "同配置多次运行差异超出容差，不应对这些指标出趋势结论",
        },
    )
    text = render_report(payload)
    credibility = text.split("## 一、实验可信度")[1].split("## 二、")[0]
    assert "不应被读作趋势" in credibility
    assert "answer_span_recall_mean" in credibility

    baseline = text.split("## 附录 B：与基线对比")[1]
    assert "不可作为趋势结论" in baseline

def test_failed_usability_gate_warns_that_quality_is_about_survivors():
    text = render_report(_payload(slice_report=_slice_report()))
    usability = text.split("## 二、系统可用性")[1].split("## 三、")[0]
    assert "未通过" in usability
    assert "只描述幸存样本" in usability


def test_passing_usability_gate_omits_the_survivor_warning():
    text = render_report(_payload(
        slice_report=_slice_report(),
        provider_timeout_rate=0.0,
        provider_stability_gate_passed=True,
    ))
    usability = text.split("## 二、系统可用性")[1].split("## 三、")[0]
    assert "通过" in usability
    assert "只描述幸存样本" not in usability


def test_missing_comparability_says_so_instead_of_implying_stability():
    payload = _payload(slice_report=_slice_report())
    payload.pop("run_comparability")
    text = render_report(payload)
    credibility = text.split("## 一、实验可信度")[1].split("## 二、")[0]
    assert "无从判断运行间稳定性" in credibility


def test_single_run_report_does_not_claim_comparability_is_fine():
    payload = _payload(slice_report=_slice_report(), run_count=1)
    payload.pop("run_comparability")
    text = render_report(payload)
    assert "不足以支撑趋势判断" in text


def test_flaky_questions_are_named_in_the_safety_section():
    text = render_report(_payload(slice_report=_slice_report(
        excluded_question_ids=["q17_ambiguous"],
    )))
    safety = text.split("## 四、安全行为")[1].split("## 五、")[0]
    assert "q17_ambiguous" in safety
    assert "flaky" in safety


def test_negative_latency_headroom_is_labelled_as_a_stall():
    """A negative headroom means calls hit the ceiling, which is a hang, not slowness."""
    text = render_report(_payload(
        slice_report=_slice_report(),
        latency_headroom_seconds=-2.5,
    ))
    usability = text.split("## 二、系统可用性")[1].split("## 三、")[0]
    assert "卡死" in usability


# ---------------------------------------------------------------------------
# Group gate and appendices
# ---------------------------------------------------------------------------
def test_absent_baseline_explains_the_gate_rather_than_omitting_the_section():
    text = render_report(_payload(slice_report=_slice_report()))
    gate = text.split("## 闸门结论")[1].split("## 附录 A")[0]
    assert "未做分组闸门比较" in gate
    assert "未声明的指标组必须完全不动" in gate


def test_group_gate_breach_is_reported_with_its_reason():
    text = render_report(_payload(slice_report=_slice_report(
        group_gate={
            "declared_changes": ["quality"],
            "all_groups_passed": False,
            "groups": {
                "quality": {"declared": True, "passed": True, "metrics": []},
                "citation": {
                    "declared": False, "passed": False,
                    "metrics": [{
                        "metric": "citation_validity_rate",
                        "verdict": "breach", "delta": -0.06,
                        "breach_reason": "out_of_scope",
                    }],
                },
            },
        },
    )))
    gate = text.split("## 闸门结论")[1].split("## 附录 A")[0]
    assert "存在违规" in gate
    assert "citation" in gate
    assert "out_of_scope" in gate


def test_undeclared_group_scope_rule_is_stated():
    """The rule is counter-intuitive -- an undeclared improvement also fails."""
    text = render_report(_payload(slice_report=_slice_report(
        group_gate={
            "declared_changes": [],
            "all_groups_passed": True,
            "groups": {"quality": {"declared": False, "passed": True, "metrics": []}},
        },
    )))
    gate = text.split("## 闸门结论")[1].split("## 附录 A")[0]
    assert "改进也不行" in gate


def test_gate_section_warns_that_baselines_must_cover_the_same_runs():
    """A run-count mismatch fakes a breach on any volume-dependent metric.

    Measured: citation_id_usage_ratio_mean is 0.225/0.225/0.305 across three
    identical runs.  Comparing a two-run subset (0.225) against the three-run
    baseline (0.252) made the gate report an out-of-scope citation breach —
    a denominator effect, not a regression.  A reader who only sees the table
    would take that breach at face value, so the caveat ships with it.
    """
    text = render_report(_payload(slice_report=_slice_report(
        group_gate={
            "declared_changes": ["quality"],
            "all_groups_passed": False,
            "groups": {"citation": {"declared": False, "passed": False, "metrics": []}},
        },
    )))
    gate = text.split("## 闸门结论")[1].split("## 附录 A")[0]
    assert "基线必须覆盖同一批运行" in gate
    assert "分母" in gate


def test_slice_appendix_reports_both_sample_size_and_question_count():
    text = render_report(_payload(slice_report=_slice_report()))
    appendix = text.split("## 附录 A：分切片明细")[1].split("## 附录 B")[0]
    assert "样本数 | 题数" in appendix
    assert "按题型" in appendix
    assert "按风险等级" in appendix


def test_per_question_appendix_locates_failed_rows():
    text = render_report(_payload(slice_report=_slice_report()))
    appendix = text.split("## 附录 C：逐题状态")[1]
    assert "q04_hit" in appendix
    assert "0/2" in appendix


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def test_write_report_creates_parent_directory(tmp_path: Path):
    output = tmp_path / "nested" / "report.md"
    write_report(_payload(slice_report=_slice_report()), output)
    assert output.exists()
    assert output.read_text(encoding="utf-8").startswith("# 生成评测报告")


def test_report_without_slice_report_still_renders_every_section():
    """A report with no slice file must say so, not silently lose sections."""
    payload = _payload()
    payload.pop("per_question")
    text = render_report(payload)
    for heading in ("一、实验可信度", "二、系统可用性", "三、回答质量",
                    "四、安全行为", "五、引用质量"):
        assert f"## {heading}" in text
    assert "未提供切片报告" in text
    assert "未提供逐题明细" in text
    assert "未做 flaky 检测" in text
