"""Upload an offline generation replay as a LangSmith dataset experiment.

This script never executes retrieval or generation.  It only reads the saved
replay JSON and the immutable evaluation file, then creates (or reuses) a
dataset, one example per question, a dataset-bound experiment, and one run per
question with its metrics attached as feedback.

One dataset, many experiments
-----------------------------
An earlier version created a fresh dataset on every upload, naming it after the
run.  That made each run an island: two runs of the same questions landed in
two unrelated datasets, so LangSmith's Datasets & Experiments view -- which
compares experiments *bound to one dataset* -- could not show them side by
side.  The dataset now represents the question set and is reused; each upload
adds one experiment.

Per-question detail
-------------------
Everything needed to explain a score travels with the example and the run: the
question, the hops the contract declares (with the source question and chunk
each hop came from), the answer, the full audit, and the per-hop results.  For
a multi-hop question the useful question is "which hop was missed", and that is
only answerable if the hop list and its verdicts are both present.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=False)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_stamp() -> str:
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON 根对象必须是对象：{path}")
    return payload


def load_inputs(result_path: Path, evaluation_path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Read a run and its contract, and require them to describe the same set.

    The earlier version asserted a literal 12 rows, which is a property of one
    particular question set rather than of correctness.  What actually matters
    is that every row has a contract entry and every contract entry has a row:
    a mismatch means the metrics would be scored against the wrong expectations.
    """
    result = load_json(result_path)
    evaluation = load_json(evaluation_path)
    rows = result.get("rows")
    cases = evaluation.get("cases")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"结果文件缺少 rows：{result_path}")
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"评测文件缺少 cases：{evaluation_path}")
    expected_ids = {str(case.get("id")) for case in cases}
    result_ids = {str(row.get("question_id")) for row in rows}
    if expected_ids != result_ids:
        raise ValueError(
            "结果与评测题目不一致："
            f"missing={sorted(expected_ids - result_ids)}, "
            f"extra={sorted(result_ids - expected_ids)}"
        )
    return result, {str(case["id"]): case for case in cases}


def _metric_values(row: dict[str, Any]) -> dict[str, float | bool | None]:
    audit = row.get("audit") or {}
    return {
        "generation_success": row.get("generation_success"),
        "citation_validity": audit.get("citation_validity"),
        # How many allowed citation ids were referenced.  Not claim-level
        # coverage, hence the explicit name.
        "citation_id_usage_ratio": audit.get("citation_id_usage_ratio"),
        # False for refusal/ambiguity questions, whose citation behaviour is
        # policy rather than factual quoting and must not be averaged in.
        "citation_metric_applicable": audit.get("citation_metric_applicable"),
        "expected_answer_span_recall": audit.get("expected_answer_span_recall"),
        "span_metric_applicable": audit.get("span_metric_applicable"),
        "required_term_recall": audit.get("required_term_recall"),
        "source_coverage": audit.get("source_coverage"),
        # Multi-hop coverage.  None on single-hop questions, which is why the
        # applicable flag travels with it -- otherwise a single-hop run would
        # average "not applicable" into the mean as a zero.
        "hop_recall": audit.get("hop_recall"),
        "hop_metric_applicable": audit.get("hop_metric_applicable"),
        "unsupported_number_count": audit.get("unsupported_number_count"),
        "unsupported_claim_count": audit.get("unsupported_claim_count"),
        "refusal_correctness": audit.get("refusal_correctness"),
        "ambiguity_safety": audit.get("ambiguity_safety"),
        "generation_latency_seconds": row.get("generation_latency_seconds"),
        "answer_length": audit.get("answer_length", len(str(row.get("answer") or ""))),
    }


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _hop_inputs(case: dict[str, Any]) -> list[dict[str, Any]]:
    """The hops the contract declares, as LangSmith-visible input detail."""
    hops = []
    for hop in case.get("required_hops") or []:
        if not isinstance(hop, dict):
            continue
        hops.append({
            "hop_id": hop.get("hop_id"),
            "from_question": hop.get("from_question"),
            "source_chunk_id": hop.get("source_chunk_id"),
            "expected_span": hop.get("expected_span"),
            "position": hop.get("position"),
        })
    return hops


def _hop_verdicts(audit: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-hop results from the audit, trimmed to what a reader needs."""
    out = []
    for hop in audit.get("hop_results") or []:
        if not isinstance(hop, dict):
            continue
        out.append({
            "hop_id": hop.get("hop_id"),
            "from_question": hop.get("from_question"),
            "expected_span": hop.get("expected_span"),
            "span_matched": hop.get("span_matched"),
            "matched": hop.get("matched"),
            "term_matches": [
                {"matched": item.get("matched"), "kind": item.get("kind")}
                for item in (hop.get("terms") or [])
                if isinstance(item, dict)
            ],
        })
    return out


def _metadata(
    result: dict[str, Any],
    result_path: Path,
    evaluation_path: Path,
    *,
    arm: str | None,
) -> dict[str, Any]:
    return {
        "source_result_file": str(result_path),
        "source_result_sha256": sha256(result_path),
        "evaluation_file": str(evaluation_path),
        "evaluation_sha256": sha256(evaluation_path),
        "snapshot_path": result.get("snapshot"),
        "snapshot_sha256": result.get("snapshot_sha256"),
        "run_id": result.get("run_id"),
        "model": result.get("model"),
        "prompt_version": result.get("prompt_version"),
        "audit_version": result.get("audit_version"),
        "evaluation_version": result.get("evaluation_version"),
        "repetition_index": result.get("repetition_index"),
        "retrieval_calls": result.get("retrieval_calls"),
        "dry_run": result.get("dry_run"),
        "max_evidence": result.get("max_evidence"),
        "arm": arm,
        "upload_type": "offline-generation-replay",
    }


def _family(evaluation: dict[str, Any]) -> str:
    """A short, name-safe family label for the question set.

    The contract's ``family`` carries a version suffix (``multihop.v1``) that is
    meaningful in the file but noisy in a dataset name, so it is trimmed here
    rather than being renamed at the source.
    """
    raw = str(evaluation.get("family") or "generation")
    trimmed = re.split(r"[.\-_]", raw)[0] or raw
    return re.sub(r"[^A-Za-z0-9]+", "-", trimmed).strip("-") or "generation"


def _dataset_name(evaluation: dict[str, Any], cases: dict[str, Any]) -> str:
    """Stable per question set, so repeated uploads reuse one dataset."""
    return f"design-kb-{_family(evaluation)}-{len(cases)}"


def _experiment_name(result: dict[str, Any], arm: str | None) -> str:
    model = re.sub(r"[^A-Za-z0-9]+", "", str(result.get("model") or "model"))
    suffix = re.sub(r"[^A-Za-z0-9]+", "-", str(arm)).strip("-") if arm else ""
    parts = ["generation-replay", model, suffix or None, safe_stamp()]
    return "-".join(part for part in parts if part)


def _print_plan(
    result: dict[str, Any],
    evaluation: dict[str, Any],
    cases: dict[str, Any],
    result_path: Path,
    evaluation_path: Path,
    arm: str | None,
) -> None:
    print(json.dumps({
        "mode": "dry-run",
        "dataset_name": _dataset_name(evaluation, cases),
        "experiment_name": _experiment_name(result, arm),
        "arm": arm,
        "source_result_file": str(result_path),
        "evaluation_file": str(evaluation_path),
        "result_sha256": sha256(result_path),
        "evaluation_sha256": sha256(evaluation_path),
        "rows": len(result["rows"]),
        "questions": len(cases),
        "multi_hop_questions": sum(1 for case in cases.values() if case.get("required_hops")),
        "retrieval_calls": result.get("retrieval_calls"),
        "dry_run_source": result.get("dry_run"),
        "model": result.get("model"),
        "max_evidence": result.get("max_evidence"),
    }, ensure_ascii=False, indent=2))


def _example_payload(row: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """One dataset example: what was asked, and what the system produced.

    The dataset holds the *result* as well as the question because these are
    offline replays: re-running them requires the frozen snapshot and a live
    provider, so the example has to be self-contained to be readable.
    """
    return {
        "inputs": {
            "question_id": row["question_id"],
            "question": row.get("question", ""),
            "snapshot_id": row.get("snapshot_id"),
            "allowed_citations": row.get("allowed_citations", []),
            "evidence_count": row.get("evidence_count"),
            "hops": _hop_inputs(case),
        },
        "outputs": {
            "answer": row.get("answer"),
            "generation_status": row.get("generation_status", row.get("status")),
            "generation_success": row.get("generation_success"),
            "generation_latency_seconds": row.get("generation_latency_seconds"),
            "attempt_count": row.get("attempt_count"),
            "provider_error_type": row.get("provider_error_type"),
            "error": row.get("error"),
            "audit": row.get("audit") or {},
            "hop_results": _hop_verdicts(row.get("audit") or {}),
            "expected_answer_spans": case.get("expected_answer_spans", []),
            "expected_sources": case.get("expected_sources", []),
            "required_hops": case.get("required_hops", []),
        },
        "metadata": {
            "split": "offline-generation-replay",
            "question_id": row["question_id"],
            "case_type": case.get("case_type"),
        },
    }


def _ensure_dataset(client: Any, dataset_name: str, description: str, meta: dict[str, Any]) -> tuple[Any, int]:
    """Reuse the dataset when it exists; create it (with examples) otherwise."""
    if client.has_dataset(dataset_name=dataset_name):
        dataset = client.read_dataset(dataset_name=dataset_name)
        existing = sum(1 for _ in client.list_examples(dataset_id=dataset.id, limit=1000))
        return dataset, existing
    dataset = client.create_dataset(
        dataset_name=dataset_name,
        description=description,
        metadata=meta,
    )
    return dataset, 0


def upload(
    result: dict[str, Any],
    cases: dict[str, dict[str, Any]],
    evaluation: dict[str, Any],
    result_path: Path,
    evaluation_path: Path,
    *,
    dataset_name: str | None = None,
    experiment_name: str | None = None,
    arm: str | None = None,
) -> dict[str, Any]:
    from langsmith import Client

    if not os.getenv("LANGCHAIN_API_KEY"):
        raise RuntimeError("未设置 LANGCHAIN_API_KEY；请在 .env 中配置，脚本不会打印密钥。")
    if result.get("dry_run") is True:
        raise ValueError("拒绝上传 dry_run=true 的结果；请使用真实结果文件。")
    if result.get("retrieval_calls") != 0:
        raise ValueError(f"结果文件 retrieval_calls 必须为 0，实际为 {result.get('retrieval_calls')}")
    if not result.get("model"):
        raise ValueError("结果文件缺少生成模型名称")

    client = Client()
    dataset_name = dataset_name or _dataset_name(evaluation, cases)
    experiment_name = experiment_name or _experiment_name(result, arm)
    meta = _metadata(result, result_path, evaluation_path, arm=arm)

    dataset, existing_examples = _ensure_dataset(
        client,
        dataset_name,
        f"{_family(evaluation)} 题集（{len(cases)} 题）的离线生成回放；固定 EvidencePack，retrieval_calls=0。",
        {"family": _family(evaluation), "question_count": len(cases)},
    )
    if existing_examples == 0:
        client.create_examples(
            dataset_id=dataset.id,
            examples=[_example_payload(row, cases[str(row["question_id"])]) for row in result["rows"]],
        )
        examples = {
            str((item.inputs or {}).get("question_id")): item
            for item in client.list_examples(dataset_id=dataset.id, limit=1000)
        }
    else:
        # Reuse path: the examples were uploaded by an earlier arm.  Matching on
        # question_id is what keeps the two arms comparable in LangSmith's view.
        examples = {
            str((item.inputs or {}).get("question_id")): item
            for item in client.list_examples(dataset_id=dataset.id, limit=1000)
        }
    missing = {str(row["question_id"]) for row in result["rows"]} - set(examples)
    if missing:
        raise RuntimeError(f"数据集缺少这些题目的 example：{sorted(missing)}")

    metric_keys = [
        "generation_success", "citation_validity", "citation_id_usage_ratio",
        "expected_answer_span_recall", "required_term_recall", "source_coverage",
        "hop_recall", "unsupported_number_count", "unsupported_claim_count",
        "refusal_correctness", "ambiguity_safety",
        "generation_latency_seconds", "answer_length",
    ]
    project = client.create_project(
        experiment_name,
        description=(
            f"{_family(evaluation)} 离线生成回放实验（固定 EvidencePack，retrieval_calls=0）"
            + (f"；arm={arm}" if arm else "")
        ),
        reference_dataset_id=dataset.id,
        num_examples=len(result["rows"]),
        num_repetitions=1,
        evaluator_keys=metric_keys,
        metadata=meta,
    )

    uploaded = 0
    for row in result["rows"]:
        question_id = str(row["question_id"])
        case = cases[question_id]
        example = examples[question_id]
        latency = _numeric(row.get("generation_latency_seconds")) or 0.0
        end_time = datetime.now(timezone.utc)
        run_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{dataset.id}|{experiment_name}|{question_id}|{result.get('run_id')}",
        )
        audit = row.get("audit") or {}
        client.create_run(
            id=run_id,
            name=f"generation-replay-{question_id}",
            run_type="chain",
            project_name=experiment_name,
            inputs={
                "question_id": question_id,
                "question": row.get("question", ""),
                "hops": _hop_inputs(case),
            },
            outputs={
                "answer": row.get("answer"),
                "audit": audit,
                "hop_results": _hop_verdicts(audit),
                "generation_status": row.get("generation_status", row.get("status")),
                "generation_success": row.get("generation_success"),
                "error": row.get("error"),
            },
            start_time=end_time - timedelta(seconds=max(0.0, latency)),
            end_time=end_time,
            reference_example_id=example.id,
            extra={
                "metadata": {
                    **meta,
                    "question_id": question_id,
                    "case_type": case.get("case_type"),
                    "attempt_count": row.get("attempt_count"),
                    "provider_error_type": row.get("provider_error_type"),
                }
            },
            tags=["generation-replay", "offline", "frozen-evidence", _family(evaluation)]
            + ([f"arm:{arm}"] if arm else []),
        )
        for key, value in _metric_values(row).items():
            score = _numeric(value)
            if score is not None:
                client.create_feedback(
                    run_id=run_id,
                    key=key,
                    score=score,
                    session_id=project.id,
                    comment="离线生成回放指标；未重新执行检索。",
                    extra={"source_result_file": str(result_path), "question_id": question_id},
                )
        uploaded += 1

    if hasattr(client, "flush"):
        client.flush()
    # Dataset Experiments are not considered complete until the project is
    # explicitly closed. Without end_time LangSmith may show the run count but
    # return an empty per-example table in Datasets & Experiments.
    client.update_project(project.id, end_time=datetime.now(timezone.utc))
    return {
        "dataset_name": dataset_name,
        "dataset_id": str(dataset.id),
        "dataset_reused": existing_examples > 0,
        "example_count": len(examples),
        "experiment_name": experiment_name,
        "uploaded_count": uploaded,
        "arm": arm,
        "source_result_file": str(result_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把离线生成回放上传为一个 LangSmith dataset 下的新 experiment"
    )
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument(
        "--dataset-name",
        default=None,
        help="省略时由契约的 family 与题数推导，从而多次上传复用同一个 dataset",
    )
    parser.add_argument("--experiment-name", default=None)
    parser.add_argument(
        "--arm",
        default=None,
        help="本次运行的对照臂标签（如 evidence5 / evidence-all），写入名称、元数据与 tag",
    )
    parser.add_argument("--dry-run", action="store_true", help="只校验并打印将要创建的名称，不访问 LangSmith")
    args = parser.parse_args()

    result_path = args.result.resolve()
    evaluation_path = args.evaluation.resolve()
    if not result_path.exists() or not evaluation_path.exists():
        raise SystemExit(f"文件不存在：result={result_path}, evaluation={evaluation_path}")
    result, cases = load_inputs(result_path, evaluation_path)
    evaluation = load_json(evaluation_path)

    if args.dry_run:
        _print_plan(result, evaluation, cases, result_path, evaluation_path, args.arm)
        return 0
    print(json.dumps(
        upload(
            result,
            cases,
            evaluation,
            result_path,
            evaluation_path,
            dataset_name=args.dataset_name,
            experiment_name=args.experiment_name,
            arm=args.arm,
        ),
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
