"""Bridge aggregation output to slice-level reporting and group gates.

Kept separate from ``aggregate_generation_replays`` so the aggregator stays a
pure function of its inputs: it computes numbers, this module interprets them.
Interpretation needs the slice file and a baseline, neither of which belongs in
the aggregator's signature.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.generation_gates import (
    check_run_comparability,
    detect_flaky_questions,
    evaluate_group_gates,
    mcnemar_exact,
    wilcoxon_signed_rank,
)
from src.generation_slices import (
    SliceError,
    behaviour_metric_series,
    group_metric_values,
    load_slices,
    validate_against_contract,
)

# Metrics that describe whether the system worked at all.  These are gated
# before quality is discussed, because a 60% success rate makes every quality
# number a statement about the surviving subset rather than about the system.
USABILITY_METRICS = (
    "provider_timeout_rate",
    "provider_hard_error_rate",
)

# Behaviour metrics are binary per question, so they get an exact paired test.
BINARY_SAFETY_METRICS = {
    "refusal_correctness": "refusal_correctness_rate",
    "ambiguity_safety": "ambiguity_safety_rate",
}

QUALITY_METRICS = (
    "answer_span_recall_mean",
    "required_term_recall_mean",
)

CITATION_METRICS = (
    "citation_validity_rate",
    "citation_id_usage_ratio_mean",
)


def _per_question_verdicts(
    rows: list[dict[str, Any]], metric: str
) -> dict[str, list[bool | None]]:
    """Raw metric series, for callers that supply their own applicability rule.

    Kept untyped about which questions *should* carry the metric; prefer
    ``behaviour_metric_series`` when the slice file is available, because a
    refusal question's ``None`` ambiguity verdict is not a failed verdict.
    """
    out: dict[str, list[bool | None]] = {}
    for row in rows:
        value = (row.get("audit") or {}).get(metric)
        out.setdefault(str(row.get("question_id")), []).append(value)
    return out


def _paired_values(
    rows: list[dict[str, Any]], metric: str, applicable_key: str | None = None
) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for row in rows:
        audit = row.get("audit") or {}
        if applicable_key and not audit.get(applicable_key):
            continue
        value = audit.get(metric)
        if value is None:
            continue
        out.setdefault(str(row.get("question_id")), []).append(float(value))
    return out


def build_slice_report(
    *,
    rows: list[dict[str, Any]],
    slices_path: str | Path,
    contract_cases: list[dict[str, Any]],
    declared_changes: list[str] | None = None,
    baseline_summary: dict[str, Any] | None = None,
    current_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Produce the slice breakdown, flaky list and group-gate verdicts.

    ``rows`` must be the completed rows of a single run, so that a question's
    verdict is recorded once per run; mixing runs here would turn a run-to-run
    difference into a within-run one.
    """
    slices = load_slices(slices_path)
    validate_against_contract(slices, contract_cases)

    flagged = {
        metric: detect_flaky_questions(
            behaviour_metric_series(rows, slices, metric), metric=metric
        )
        for metric in BINARY_SAFETY_METRICS
    }
    flaky_ids = sorted({
        qid
        for report in flagged.values()
        for qid in report["flaky_question_ids"]
    })

    # Questions declared flaky in the slice file are excluded from the
    # regression denominator, and so are ones this run discovered to be flaky.
    # Either way the number must not silently include an unstable sample.
    excluded = set(flaky_ids) | {
        sid for sid, entry in slices["by_id"].items() if entry.get("flaky")
    }

    slice_breakdown = {}
    for dimension in ("case_type", "risk_level"):
        for metric, applicable_key in (
            ("required_term_recall", None),
            ("citation_validity", "citation_metric_applicable"),
            ("refusal_correctness", None),
            ("ambiguity_safety", None),
        ):
            key = f"{dimension}:{metric}"
            grouped = group_metric_values(
                rows, slices, by=dimension, metric=metric, applicable_key=applicable_key
            )
            for entry in grouped.values():
                entry["question_ids"] = [
                    qid for qid in entry["question_ids"] if qid not in excluded
                ]
                entry["question_count"] = len(entry["question_ids"])
            slice_breakdown[key] = grouped

    gate = None
    if baseline_summary is not None and current_summary is not None:
        deltas: dict[str, dict[str, Any]] = {}
        for metric in (
            *QUALITY_METRICS, *CITATION_METRICS, *USABILITY_METRICS,
            "refusal_correctness_rate", "ambiguity_safety_rate",
        ):
            base = baseline_summary.get(metric)
            cur = current_summary.get(metric)
            deltas[metric] = {
                "baseline": base,
                "current": cur,
                "delta": (
                    round(float(cur) - float(base), 4)
                    if base is not None and cur is not None else None
                ),
            }
        gate = evaluate_group_gates(
            slices.get("metric_groups") or {},
            deltas,
            declared_changes=list(declared_changes or []),
        )

    return {
        "slice_file": str(Path(slices_path).resolve()),
        "excluded_question_ids": sorted(excluded),
        "flaky_detection": flagged,
        "slice_breakdown": slice_breakdown,
        "group_gate": gate,
        "note": (
            "样本量小（12 题），任何单题翻转都会显著改变百分比；"
            "因此每个切片都带 sample_size，且 flaky 题不进入回归分母。"
        ),
    }


def paired_comparison(
    baseline_rows: list[dict[str, Any]],
    current_rows: list[dict[str, Any]],
    *,
    metric: str,
    applicable_key: str | None = None,
    kind: str = "continuous",
) -> dict[str, Any]:
    """Compare two runs on the same questions, question by question.

    Pairing matters at this sample size: comparing two means discards which
    questions moved, and with 12 questions that is most of the signal.
    """
    base = _paired_values(baseline_rows, metric, applicable_key)
    cur = _paired_values(current_rows, metric, applicable_key)
    shared = sorted(set(base) & set(cur))
    if not shared:
        return {"metric": metric, "comparable_pairs": 0,
                "note": "两次运行没有共同的可用样本，无法配对比较"}

    a = [base[qid][0] for qid in shared]
    b = [cur[qid][0] for qid in shared]
    result = (
        mcnemar_exact([bool(x) for x in a], [bool(x) for x in b])
        if kind == "binary"
        else wilcoxon_signed_rank(a, b)
    )
    result.update({
        "metric": metric,
        "comparable_pairs": len(shared),
        "baseline_values": a,
        "current_values": b,
        "question_ids": shared,
    })
    return result


def comparability_report(
    run_summaries: list[dict[str, Any]],
    *,
    keys: tuple[str, ...] = (*QUALITY_METRICS, *CITATION_METRICS),
    tolerance: float = 0.10,
) -> dict[str, Any]:
    """Check that repeated runs of one configuration agree before trending."""
    return check_run_comparability(
        [dict(summary) for summary in run_summaries], keys=list(keys), tolerance=tolerance
    )


__all__ = [
    "SliceError",
    "build_slice_report",
    "comparability_report",
    "paired_comparison",
]
