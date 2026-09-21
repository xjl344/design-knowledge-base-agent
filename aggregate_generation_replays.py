"""Aggregate independent generation replay runs without rerunning the model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.eval_report import write_report
from src.gates_runner import build_slice_report, comparability_report
from src.generation_status import (
    COMPLETED_STATUS,
    PROVIDER_FAILURE_CLASSES,
    PROVIDER_HARD_ERROR_CLASSES,
    PROVIDER_LATENCY_CLASSES,
    RATE_LIMIT_STATUS,
)

ROOT = Path(__file__).resolve().parent


def _metric_view(payload: dict[str, Any]) -> dict[str, Any]:
    """Flatten a run or aggregate payload into ``{metric: value}``.

    Two shapes reach the gates: this script's own output keeps metrics at the
    top level, while a single-run file nests them under ``summary``.  Reading
    only ``["summary"]`` (as an earlier version did) produced ``None`` for this
    script's own output, so the group gate received no baseline and silently
    never ran -- a gate that never fires looks exactly like a gate that passed.
    """
    nested = payload.get("summary")
    if isinstance(nested, dict) and nested:
        return dict(nested)
    return {
        key: value
        for key, value in payload.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def percentile(values: list[float], value: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((value / 100) * (len(ordered) - 1)))))
    return round(ordered[index], 3)


def mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def aggregate(paths: list[Path]) -> dict[str, Any]:
    runs = [json.loads(path.read_text(encoding="utf-8-sig")) for path in paths]
    if not runs:
        raise ValueError("至少需要一个回放结果文件")
    invariant_keys = ("model", "snapshot_sha256", "evaluation_sha256", "prompt_version", "audit_version")
    for key in invariant_keys:
        values = {str(run.get(key)) for run in runs}
        if len(values) != 1:
            raise ValueError(f"P0 运行的 {key} 不一致：{sorted(values)}")
    # Retry policy is part of the experiment identity: a run with retries on
    # has a different success distribution than one with retries off, so they
    # must never be averaged together.
    retry_settings = {
        str(run.get("generation_max_retries")) for run in runs
    }
    if len(retry_settings) != 1:
        raise ValueError(
            f"P0 运行的 generation_max_retries 不一致：{sorted(retry_settings)}。"
            "不同重试策略的成功率不可比。"
        )
    if any(run.get("retrieval_calls") != 0 or run.get("dry_run") for run in runs):
        raise ValueError("聚合只接受 retrieval_calls=0 且 dry_run=false 的真实回放")
    repetitions = [int(run.get("repetition_index", 0)) for run in runs]
    if len(set(repetitions)) != len(repetitions):
        raise ValueError(f"repetition_index 重复：{repetitions}")

    # The per-question timeout ceiling is recorded by the producer. Older runs
    # predate that field, so it degrades to None rather than failing.
    latency_ceiling = runs[0].get("llm_timeout_seconds")
    generation_max_retries = runs[0].get("generation_max_retries")

    rows = [row for run in runs for row in run.get("rows", [])]
    completed = [row for row in rows if row.get("status") == COMPLETED_STATUS]
    # Failure accounting is split by remedy: a timeout means the latency
    # ceiling is too tight, a hard error means the service misbehaved.
    # Merging them (as an earlier version did via startswith("provider_"))
    # produced one number that could not tell the two apart.
    #
    # The class names come from ``src.generation_status`` rather than being
    # repeated here: the producer and this counter must agree on them, and two
    # copies of the literals would drift the moment one side gains a status.
    provider_timeouts = [
        row for row in rows if row.get("status") in PROVIDER_LATENCY_CLASSES
    ]
    provider_rate_limits = [
        row for row in rows if row.get("status") == RATE_LIMIT_STATUS
    ]
    provider_hard_errors = [
        row for row in rows if row.get("status") in PROVIDER_HARD_ERROR_CLASSES
    ]
    provider_failures = [
        row for row in rows
        if row.get("status") in PROVIDER_FAILURE_CLASSES
    ]
    latencies = [float(row["generation_latency_seconds"]) for row in completed if row.get("generation_latency_seconds") is not None]
    audits = [row.get("audit") or {} for row in completed]

    def audit_values(key: str, applicable_key: str | None = None) -> list[float]:
        return [
            float(audit[key])
            for audit in audits
            if audit.get(key) is not None and (not applicable_key or audit.get(applicable_key))
        ]

    # Every mean-style metric is reported with the number of observations behind
    # it.  At 12 questions a mean without its denominator reads as precise when
    # it is not: one failed row moves it by several points.
    span_values = audit_values("expected_answer_span_recall", "span_metric_applicable")
    term_values = audit_values("required_term_recall")
    citation_values = audit_values("citation_validity", "citation_metric_applicable")
    citation_ratio_values = audit_values("citation_id_usage_ratio", "citation_metric_applicable")
    source_coverage_values = audit_values("source_coverage", "citation_metric_applicable")
    unsupported_values = audit_values("unsupported_number_count")
    answer_length_values = audit_values("answer_length")
    # Behaviour metrics are None on every question outside their own case type,
    # so filtering on "not None" is what keeps a refusal question's None
    # ambiguity verdict from entering the ambiguity denominator.
    refusal_values = [
        float(audit["refusal_correctness"])
        for audit in audits if audit.get("refusal_correctness") is not None
    ]
    ambiguity_values = [
        float(audit["ambiguity_safety"])
        for audit in audits if audit.get("ambiguity_safety") is not None
    ]
    # Multi-hop coverage applies only to questions that declare hops.  Filtering
    # on the applicable flag (not on "not None") keeps single-hop rows, which
    # report no hop verdict at all, out of the denominator.
    hop_values = audit_values("hop_recall", "hop_metric_applicable")

    by_question: dict[str, dict[str, Any]] = {}
    for row in rows:
        question_id = str(row.get("question_id"))
        item = by_question.setdefault(question_id, {"runs": 0, "completed": 0, "statuses": [], "required_term_recall": []})
        item["runs"] += 1
        item["statuses"].append(row.get("status"))
        if row.get("status") == COMPLETED_STATUS:
            item["completed"] += 1
            value = (row.get("audit") or {}).get("required_term_recall")
            if value is not None:
                item["required_term_recall"].append(value)
    for item in by_question.values():
        item["success_rate"] = round(item["completed"] / item["runs"], 3) if item["runs"] else None
        item["required_term_recall_mean"] = mean([float(value) for value in item.pop("required_term_recall")])

    total = len(rows) or 0

    def _rate(subset: list) -> float | None:
        return round(len(subset) / total, 3) if total else None

    status_counts = {
        status: sum(row.get("status") == status for row in rows)
        for status in sorted({str(row.get("status")) for row in rows})
    }
    timeout_rate = _rate(provider_timeouts)
    hard_error_rate = _rate(provider_hard_errors)
    # Retry dependence across the whole matrix. A high count means the provider
    # is unstable even when the headline success rate looks healthy.
    retried_success_rows = [
        row for row in completed
        if (row.get("audit") or {}).get("failed_attempt_count")
    ]
    # Per-attempt latency, not per-row latency: a retried row's total includes
    # the failed attempt's full timeout, which would make the headroom negative
    # and hide the real distribution of individual calls. Runs recorded before
    # attempt bookkeeping existed have no ``attempts``, so their row latency is
    # already a single call and is used directly.
    per_attempt_latencies = [
        float(item["latency_seconds"])
        for row in completed
        for item in ((row.get("audit") or {}).get("attempts") or [])
        if item.get("status") == COMPLETED_STATUS and item.get("latency_seconds") is not None
    ]
    if not per_attempt_latencies:
        per_attempt_latencies = [
            float(row["generation_latency_seconds"])
            for row in completed
            if row.get("generation_latency_seconds") is not None
        ]
    return {
        "experiment": "generation-replay-p0-matrix",
        "run_count": len(runs),
        "repetition_indices": sorted(repetitions),
        "source_files": [str(path.resolve()) for path in paths],
        **{key: runs[0].get(key) for key in invariant_keys},
        "generation_max_retries": generation_max_retries,
        "retrieval_calls": 0,
        "total_question_runs": len(rows),
        "completed_question_runs": len(completed),
        "generation_success_rate": round(len(completed) / total, 3) if total else None,
        "first_try_success_count": len(completed) - len(retried_success_rows),
        "retried_success_count": len(retried_success_rows),
        "retried_success_question_ids": [
            row.get("question_id") for row in retried_success_rows
        ],
        # Combined figure is kept for backward comparison only. Prefer the two
        # split rates below when diagnosing or gating.
        "provider_error_rate": _rate(provider_failures),
        "provider_failure_rate": _rate(provider_failures),
        "provider_timeout_rate": timeout_rate,
        "provider_rate_limit_rate": _rate(provider_rate_limits),
        "provider_hard_error_rate": hard_error_rate,
        "status_counts": status_counts,
        # Gates are separate so the remedy is unambiguous: a failing timeout
        # gate means raise the ceiling, a failing hard-error gate means retry
        # or switch provider.
        "provider_stability_gate_threshold": 0.10,
        "provider_timeout_gate_passed": timeout_rate is not None and timeout_rate <= 0.10,
        "provider_hard_error_gate_passed": hard_error_rate is not None and hard_error_rate <= 0.10,
        "provider_stability_gate_passed": (
            timeout_rate is not None and timeout_rate <= 0.10
            and hard_error_rate is not None and hard_error_rate <= 0.10
        ),
        # Per-row latency (includes retries) and per-call latency (one model
        # call). The latter is what the ceiling acts on.
        "generation_latency_seconds": {"p50": percentile(latencies, 50), "p95": percentile(latencies, 95), "max": max(latencies) if latencies else None},
        "per_attempt_latency_seconds": {
            "p50": percentile(per_attempt_latencies, 50),
            "p95": percentile(per_attempt_latencies, 95),
            "max": max(per_attempt_latencies) if per_attempt_latencies else None,
            "sample_size": len(per_attempt_latencies),
        },
        # Distance between the slowest successful *single call* and the
        # configured ceiling. Measured per call, because a retried row's total
        # includes a full failed timeout and would make this negative.
        # A large negative value means calls are hitting the ceiling, which is
        # the signature of stalls rather than slow-but-completing generation.
        "latency_ceiling_seconds": latency_ceiling,
        "latency_headroom_seconds": (
            round(float(latency_ceiling) - max(per_attempt_latencies), 3)
            if per_attempt_latencies and latency_ceiling is not None else None
        ),
        # Mean-style quality and citation metrics.  Each is paired with the
        # number of scored observations, because the denominator differs by
        # metric: span and citation metrics only apply to the questions that
        # declare them applicable, and behaviour metrics only to their case type.
        "answer_span_recall_mean": mean(span_values),
        "span_metric_sample_size": len(span_values),
        "required_term_recall_mean": mean(term_values),
        "required_term_metric_sample_size": len(term_values),
        "citation_validity_rate": mean(citation_values),
        "citation_id_usage_ratio_mean": mean(citation_ratio_values),
        "source_coverage_mean": mean(source_coverage_values),
        "citation_metric_sample_size": len(citation_values),
        "unsupported_number_count_mean": mean(unsupported_values),
        "unsupported_number_metric_sample_size": len(unsupported_values),
        "answer_length_mean": mean(answer_length_values),
        "answer_length_metric_sample_size": len(answer_length_values),
        # Behaviour metrics: the mean is over questions of the matching case
        # type only, so this denominator is 1 or 2 on the 12-question set, not
        # the run size.  Reporting the run size here would be a misstatement.
        "refusal_correctness_rate": mean(refusal_values),
        "refusal_metric_sample_size": len(refusal_values),
        "ambiguity_safety_rate": mean(ambiguity_values),
        "ambiguity_metric_sample_size": len(ambiguity_values),
        # Multi-hop coverage: the denominator is the number of multi-hop
        # questions answered, not the run size.  Zero for a single-hop-only run,
        # which is why the sample size ships beside it.
        "hop_recall_mean": mean(hop_values),
        "hop_metric_sample_size": len(hop_values),
        "per_question": by_question,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="聚合多次独立生成回放")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--slices",
        type=Path,
        default=ROOT / "data" / "generation_eval_slices.v1.json",
        help="分切片定义文件；省略时不产出切片报告。",
    )
    parser.add_argument(
        "--evaluation",
        type=Path,
        default=ROOT / "data" / "generation_eval.v2.json",
        help="用于校验切片覆盖的评测契约。",
    )
    parser.add_argument(
        "--declared-changes",
        default="",
        help=(
            "本次改动声明会影响的指标组，逗号分隔（如 quality,citation）。"
            "未声明的组只要有任何变动即判定为回归。"
        ),
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="作为对照的基线聚合结果；省略时只做切片统计，不做分组闸门。",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="同时输出一份可读的 Markdown 报告。",
    )
    args = parser.parse_args()
    payload = aggregate(args.files)

    if args.slices and args.slices.exists():
        runs = [json.loads(path.read_text(encoding="utf-8-sig")) for path in args.files]
        # Slice reporting is per-run: a question must contribute one verdict per
        # run, otherwise a run-to-run difference is read as a within-run one.
        rows = [row for run in runs for row in run.get("rows", [])]
        contract = json.loads(args.evaluation.read_text(encoding="utf-8"))
        baseline_summary = None
        if args.baseline and args.baseline.exists():
            baseline_summary = _metric_view(
                json.loads(args.baseline.read_text(encoding="utf-8-sig"))
            )
        payload["slice_report"] = build_slice_report(
            rows=rows,
            slices_path=args.slices,
            contract_cases=contract.get("cases", []),
            declared_changes=[g for g in args.declared_changes.split(",") if g.strip()],
            baseline_summary=baseline_summary,
            current_summary=_metric_view(payload),
        )
        payload["run_comparability"] = comparability_report(
            [_metric_view(run) for run in runs]
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.report:
        write_report(payload, args.report)

    summary: dict[str, Any] = {
        "output": str(args.output.resolve()),
        "runs": payload["run_count"],
        "provider_stability_gate_passed": payload["provider_stability_gate_passed"],
    }
    report = payload.get("slice_report") or {}
    if report.get("flaky_detection"):
        summary["flaky_question_ids"] = report["excluded_question_ids"]
    if report.get("group_gate"):
        summary["group_gate_passed"] = report["group_gate"]["all_groups_passed"]
    if payload.get("run_comparability"):
        summary["runs_comparable"] = payload["run_comparability"]["comparable"]
    if args.report:
        summary["report"] = str(args.report.resolve())
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
