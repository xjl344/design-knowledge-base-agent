"""Replay generation quality from frozen evidence without retrieval."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import hashlib
import json
from pathlib import Path
import secrets
import sys
import time

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.frozen_evidence import (  # noqa: E402
    AUDIT_VERSION,
    build_evidence_pack,
    declared_facts_lost_to_truncation,
    load_cases,
)
from src.generator_v2 import (  # noqa: E402
    GENERATOR_MAX_RETRIES,
    GENERATOR_PROMPT_VERSION,
    generate_from_pack,
)
from src.model_clients import model_descriptor  # noqa: E402
from config import settings  # noqa: E402


CONTRACT_FIELDS = (
    "expected_answer_spans",
    "expected_sources",
    "required_terms",
    "refusal_requirements",
    "ambiguity_requirements",
)


def load_evaluation(path: Path) -> dict[str, dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases", [])
    _warn_on_thin_contract(path, cases)
    return {str(item["id"]): item for item in cases}


def _warn_on_thin_contract(path: Path, cases: list[dict]) -> None:
    """Fail loudly when the evaluation set omits behavioural contract fields.

    A set carrying only spans and sources still runs, but every behavioural
    metric silently becomes ``None``.  That looks like "metric not applicable"
    rather than "contract misconfigured", which is exactly the kind of quiet
    hole this harness exists to prevent.
    """
    if not cases:
        return
    present = {key for case in cases for key in case}
    missing = [field for field in CONTRACT_FIELDS if field not in present]
    if not missing:
        return
    raise SystemExit(
        f"拒绝执行：评测集缺少契约字段 {missing}（{path}）。\n"
        "缺少这些字段时 required_term_recall / refusal_correctness / "
        "ambiguity_safety 会静默变成 None，看起来像'不适用'而不是'配置错误'。\n"
        "请改用携带完整契约的 data/generation_eval.v2.json。"
    )


def load_evaluation_version(path: Path) -> int | str:
    """Read the contract version, tolerating a non-numeric one.

    Every contract so far uses an integer, but assuming that turned a future
    string version into a crash deep inside run-metadata assembly rather than a
    clear error at load time.  The value is only ever recorded and compared for
    equality, so passing the raw value through is safe and honest.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw = payload.get("version", 1)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return str(raw)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def make_run_id() -> str:
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    return f"{timestamp}_{secrets.token_hex(4)}"


def default_output_path(run_id: str) -> Path:
    target_dir = ROOT / "data" / "runs"
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / f"generation_replay_{run_id}.json"


def summarize_rows(rows: list[dict]) -> dict:
    """Create batch-level metrics without treating provider failures as answers."""
    completed = [row for row in rows if row.get("status") == "completed"]
    latencies = [
        float(row["generation_latency_seconds"])
        for row in completed
        if row.get("generation_latency_seconds") is not None
    ]
    audits = [row.get("audit") or {} for row in completed]
    refusal_values = [audit["refusal_correctness"] for audit in audits if audit.get("refusal_correctness") is not None]
    ambiguity_values = [audit["ambiguity_safety"] for audit in audits if audit.get("ambiguity_safety") is not None]

    def _values(key: str, *, applicable_key: str | None = None) -> list:
        out = []
        for audit in audits:
            if applicable_key and not audit.get(applicable_key):
                continue
            value = audit.get(key)
            if value is not None:
                out.append(value)
        return out

    # Boundary questions (refusal / ambiguity) are excluded from span and
    # citation metrics so a correct refusal cannot dilute fact-question rates.
    citation_values = _values("citation_status", applicable_key="citation_metric_applicable")
    span_values = _values("expected_answer_span_recall", applicable_key="span_metric_applicable")
    usage_values = _values("citation_id_usage_ratio", applicable_key="citation_metric_applicable")

    # Malformed-output accounting.  ``sanitization_applied`` marks answers that
    # carried trailing control tokens (e.g. ".calc"); the acceptance line is
    # malformed_output_count == 0, so pollution stays visible as a count rather
    # than being silently cleaned and forgotten.
    sanitized_rows = [row for row in completed if row.get("sanitization_applied")]
    sanitization_rule_counts: dict[str, int] = {}
    for row in sanitized_rows:
        for rule in row.get("sanitization_rules") or []:
            sanitization_rule_counts[rule] = sanitization_rule_counts.get(rule, 0) + 1

    # Retry dependence. A row that only succeeded after a failure is NOT the
    # same evidence as a first-try success, so it is counted separately rather
    # than being folded into generation_success_rate. A high retried_success
    # count means the provider is unstable even though the pass rate looks fine.
    retried_success_rows = [
        row for row in completed
        if (row.get("audit") or {}).get("failed_attempt_count")
    ]
    first_try_rows = [row for row in completed if row not in retried_success_rows]
    total_failed_attempts = sum(
        int((row.get("audit") or {}).get("failed_attempt_count") or 0)
        for row in completed
    )

    def _mean(values: list) -> float | None:
        return round(sum(values) / len(values), 3) if values else None

    def _percentile(values: list[float], percentile: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, int(round((percentile / 100) * (len(ordered) - 1)))))
        return round(ordered[index], 3)

    required_values = [audit["required_term_recall"] for audit in audits if audit.get("required_term_recall") is not None]
    # Facts the character budget cut away.  Counted across every row, including
    # failed ones: the damage is to the evidence, which happened regardless of
    # whether the call then succeeded.
    truncation_lost = [
        (str(row.get("question_id")), fact)
        for row in rows
        for fact in (row.get("truncation_lost_facts") or [])
    ]
    # Multi-hop coverage is only defined for questions that declare hops, so the
    # applicable flag decides the denominator -- same rule as the citation and
    # span metrics above.  Without it, single-hop rows would report "not
    # applicable" as a zero and drag every multi-hop mean down.
    hop_values = _values("hop_recall", applicable_key="hop_metric_applicable")
    # Attribution is scored against the answer's own numeric claims, so this
    # denominator is answer properties rather than evidence count -- which is
    # what makes it comparable across configurations with different evidence
    # volumes, unlike the utilisation ratio.
    numeric_citation_values = _values(
        "numeric_claim_citation_coverage",
        applicable_key="numeric_citation_metric_applicable",
    )

    return {
        "total": len(rows),
        "completed": len(completed),
        "generation_success_rate": round(len(completed) / len(rows), 3) if rows else None,
        "status_counts": {
            status: sum(row.get("status") == status for row in rows)
            for status in sorted({row.get("status") for row in rows})
        },
        "provider_timeout_rate": round(sum(row.get("status") == "provider_timeout" for row in rows) / len(rows), 3) if rows else None,
        "provider_error_rate": round(sum(row.get("status") == "provider_error" for row in rows) / len(rows), 3) if rows else None,
        "generation_latency_seconds": {
            "p50": round(sorted(latencies)[(len(latencies) - 1) // 2], 3) if latencies else None,
            "p95": _percentile(latencies, 95),
            "max": round(max(latencies), 3) if latencies else None,
        },
        "citation_status_counts": {status: citation_values.count(status) for status in sorted(set(citation_values))},
        "citation_metric_sample_size": len(citation_values),
        "citation_validity_rate": (
            round(citation_values.count("pass") / len(citation_values), 3) if citation_values else None
        ),
        "citation_id_usage_ratio_mean": _mean(usage_values),
        "span_metric_sample_size": len(span_values),
        "answer_span_recall_mean": _mean(span_values),
        "required_term_recall_mean": _mean(required_values),
        "required_term_metric_sample_size": len(required_values),
        "hop_recall_mean": _mean(hop_values),
        "hop_metric_sample_size": len(hop_values),
        "numeric_citation_coverage_mean": _mean(numeric_citation_values),
        "numeric_citation_metric_sample_size": len(numeric_citation_values),
        # Non-zero means the configured budget removed evidence the contract
        # scores against, so the metrics below are measuring a damaged context.
        # It is reported rather than raised because a small budget may be a
        # deliberate latency trade; it must simply never be silent.
        "truncation_lost_fact_count": len(truncation_lost),
        "truncation_lost_fact_question_ids": sorted({qid for qid, _ in truncation_lost}),
        "truncation_lost_fact_details": [
            {"question_id": qid, **fact} for qid, fact in truncation_lost[:10]
        ],
        "malformed_output_count": len(sanitized_rows),
        "malformed_output_question_ids": [row.get("question_id") for row in sanitized_rows],
        "sanitization_rule_counts": sanitization_rule_counts,
        "first_try_success_count": len(first_try_rows),
        "retried_success_count": len(retried_success_rows),
        "retried_success_question_ids": [
            row.get("question_id") for row in retried_success_rows
        ],
        "failed_attempt_count_total": total_failed_attempts,
        "refusal_correctness_rate": _mean(refusal_values),
        "ambiguity_safety_rate": _mean(ambiguity_values),
    }


async def run(args: argparse.Namespace) -> dict:
    cases = load_cases(args.snapshot)
    evaluation = load_evaluation(Path(args.evaluation))
    selected_ids = args.question_id or list(evaluation)
    rows = []
    started = time.perf_counter()
    for question_id in selected_ids:
        if question_id not in cases:
            rows.append({"question_id": question_id, "status": "missing_snapshot"})
            continue
        spec = evaluation.get(question_id, {})
        pack = build_evidence_pack(
            cases[question_id],
            max_items=args.max_evidence,
            max_chars_per_item=args.max_chars_per_item,
        )
        # A truncating budget can cut away the very fact this question is scored
        # on, and the row would still report success -- the drop would read as a
        # model regression instead of a configuration error.  Checked per row so
        # the run says which questions it damaged, not just that it did.
        lost_facts = declared_facts_lost_to_truncation(
            cases[question_id],
            spec,
            max_items=args.max_evidence,
            max_chars_per_item=args.max_chars_per_item,
        )
        row = {
            "question_id": question_id,
            "question": pack.question,
            "snapshot_id": pack.snapshot_id,
            "allowed_citations": list(pack.allowed_citations),
            "evidence_count": len(pack.items),
            "truncation_lost_facts": lost_facts,
            "retrieval_timing": cases[question_id].retrieval_timing,
        }
        if args.dry_run:
            row.update({"status": "ready", "context_chars": len(pack.context_text()), "audit": {}})
        else:
            result = await generate_from_pack(
                pack,
                expected_answer_spans=spec.get("expected_answer_spans", []),
                expected_sources=spec.get("expected_sources", []),
                required_terms=spec.get("required_terms", []),
                refusal_requirements=spec.get("refusal_requirements", []),
                ambiguity_requirements=spec.get("ambiguity_requirements", []),
                # Absent for single-hop contracts, which is why the default is
                # an empty tuple rather than a required argument: the same
                # harness has to serve both sets.
                required_hops=spec.get("required_hops", []),
                max_retries=int(args.max_retries),
            )
            row.update(result.as_dict())
            row["status"] = result.generation_status
        rows.append(row)
    return {
        "experiment": "generation-replay",
        "run_id": args.run_id,
        "started_at": args.started_at,
        "finished_at": datetime.now().astimezone().isoformat(),
        "model": model_descriptor("generator")["model"],
        "snapshot": str(args.snapshot),
        "snapshot_sha256": file_sha256(Path(args.snapshot)),
        "evaluation": str(args.evaluation),
        "evaluation_sha256": file_sha256(Path(args.evaluation)),
        "prompt_version": GENERATOR_PROMPT_VERSION,
        "audit_version": AUDIT_VERSION,
        "evaluation_version": load_evaluation_version(Path(args.evaluation)),
        "repetition_index": int(args.repetition_index),
        # Execution parameters are recorded so a stability-gate failure can be
        # diagnosed without guessing: the ceiling explains timeouts, and the
        # retry count states whether a failed call was ever retried.
        "llm_timeout_seconds": int(settings.llm_timeout_seconds),
        "generation_max_retries": int(args.max_retries),
        # The evidence cap is the one parameter that distinguishes the two arms
        # of the multi-hop experiment, so it has to travel with the run.  It was
        # absent, which made a 5-item run and an all-items run indistinguishable
        # in their metadata.
        "max_evidence": int(args.max_evidence),
        # Character budget per chunk.  Distinct from max_evidence: that one
        # trades latency for *more* chunks, this one shrinks each chunk.
        "max_chars_per_item": (
            None if args.max_chars_per_item is None else int(args.max_chars_per_item)
        ),
        "retrieval_calls": 0,
        "dry_run": bool(args.dry_run),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "summary": summarize_rows(rows),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="离线回放冻结证据上的生成质量")
    parser.add_argument("--snapshot", default=str(ROOT / "data" / "frozen_retrieval_cases.jsonl"))
    parser.add_argument("--evaluation", default=str(ROOT / "data" / "generation_eval.v2.json"))
    parser.add_argument("--output", default="", help="结果路径；省略时自动写入 data/runs 唯一文件")
    parser.add_argument("--max-evidence", type=int, default=5)
    parser.add_argument(
        "--max-chars-per-item",
        type=int,
        default=600,
        help=(
            "每条证据的字符上限。默认 600 由实测事实深度得出：单跳 12 题的评分事实最深在 "
            "536 字符处，600 下预检零丢失、逐题指标零差异；多跳题可传 300（其实测最深 154）。"
            "无论取何值，运行都会报出被切掉的契约事实，不会静默降级。"
            "延迟收益未坐实（provider 漂移达 2.3 倍），故不以此为由调小预算。"
            "用 --no-truncate 关闭预算。"
        ),
    )
    parser.add_argument(
        "--no-truncate",
        action="store_true",
        help="关闭字符预算（保持与历史运行完全一致的行为）",
    )
    parser.add_argument("--question-id", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--repetition-index", type=int, default=1)
    parser.add_argument(
        "--max-retries",
        type=int,
        default=GENERATOR_MAX_RETRIES,
        help=(
            "每次模型调用的重试次数。默认 0，使一行等于一次调用，"
            "provider 失败率是测量值而非重试掩盖后的结果。"
        ),
    )
    args = parser.parse_args()
    if args.no_truncate:
        args.max_chars_per_item = None
    args.run_id = make_run_id()
    args.started_at = datetime.now().astimezone().isoformat()
    snapshot_path = Path(args.snapshot).resolve()
    evaluation_path = Path(args.evaluation).resolve()
    output_path = (Path(args.output).resolve() if args.output else default_output_path(args.run_id).resolve())
    # Ground truth 保护闸门：输出绝不能覆盖评测输入。该文件是人工标注的
    # 语义基准，被结果覆盖后整轮评测将失去对照，且项目无版本控制可回滚。
    if output_path == evaluation_path:
        raise SystemExit(
            "拒绝执行：--output 与 --evaluation 指向同一文件。"
            "这会把人工标注的评测基准覆盖为结果文件。请改用不同的输出路径。"
        )
    if output_path.exists():
        raise SystemExit(f"拒绝覆盖已有结果文件：{output_path}。请指定新的 --output 路径。")
    if not evaluation_path.exists():
        raise SystemExit(f"拒绝执行：评测输入不存在：{evaluation_path}")
    if not snapshot_path.exists():
        raise SystemExit(f"拒绝执行：冻结快照不存在：{snapshot_path}")
    args.snapshot = snapshot_path
    args.evaluation = evaluation_path
    result = asyncio.run(run(args))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(result, ensure_ascii=False, indent=2))
    except FileExistsError:
        raise SystemExit(f"拒绝覆盖已有结果文件：{output_path}。请重新运行以生成新的 run id。")
    print(json.dumps({"output": str(output_path), "retrieval_calls": 0, "rows": len(result["rows"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
