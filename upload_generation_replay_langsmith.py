"""Upload an offline generation replay as a new LangSmith dataset experiment.

This script never executes retrieval or generation. It only reads the saved
replay JSON and the immutable evaluation file, then creates a fresh dataset,
examples, dataset-bound experiment project, and one run per question.
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
DEFAULT_RESULT = ROOT / "data" / "runs" / "generation_replay_20260919T211836+0800_814cd403.json"
DEFAULT_EVALUATION = ROOT / "data" / "generation_eval.golden.json"


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
    result = load_json(result_path)
    evaluation = load_json(evaluation_path)
    rows = result.get("rows")
    cases = evaluation.get("cases")
    if not isinstance(rows, list) or len(rows) != 12:
        raise ValueError(f"结果文件必须包含完整 12 题 rows，实际为 {len(rows) if isinstance(rows, list) else '非数组'}")
    if not isinstance(cases, list) or len(cases) != 12:
        raise ValueError(f"评测文件必须包含完整 12 题 cases，实际为 {len(cases) if isinstance(cases, list) else '非数组'}")
    expected_ids = {str(case.get("id")) for case in cases}
    result_ids = {str(row.get("question_id")) for row in rows}
    if expected_ids != result_ids:
        raise ValueError(f"结果与评测题目不一致：missing={sorted(expected_ids-result_ids)}, extra={sorted(result_ids-expected_ids)}")
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


def _metadata(result: dict[str, Any], result_path: Path, evaluation_path: Path) -> dict[str, Any]:
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
        "upload_type": "offline-generation-replay",
    }


def _dataset_name(result: dict[str, Any]) -> str:
    return f"design-kb-generation-replay-12-{safe_stamp()}-{str(result.get('run_id', 'run'))[-8:]}"


def _experiment_name(result: dict[str, Any]) -> str:
    model = re.sub(r"[^A-Za-z0-9]+", "", str(result.get("model") or "model"))
    return f"generation-replay-{model}-{safe_stamp()}"


def _print_plan(result: dict[str, Any], result_path: Path, evaluation_path: Path) -> None:
    print(json.dumps({
        "mode": "dry-run",
        "dataset_name": _dataset_name(result),
        "experiment_name": _experiment_name(result),
        "source_result_file": str(result_path),
        "evaluation_file": str(evaluation_path),
        "result_sha256": sha256(result_path),
        "evaluation_sha256": sha256(evaluation_path),
        "rows": len(result["rows"]),
        "retrieval_calls": result.get("retrieval_calls"),
        "dry_run_source": result.get("dry_run"),
        "model": result.get("model"),
    }, ensure_ascii=False, indent=2))


def upload(result: dict[str, Any], cases: dict[str, dict[str, Any]], result_path: Path, evaluation_path: Path) -> dict[str, Any]:
    from langsmith import Client

    if not os.getenv("LANGCHAIN_API_KEY"):
        raise RuntimeError("未设置 LANGCHAIN_API_KEY；请在 .env 中配置，脚本不会打印密钥。")
    if result.get("dry_run") is True:
        raise ValueError("拒绝上传 dry_run=true 的结果；请使用真实 12 题结果文件。")
    if result.get("retrieval_calls") != 0:
        raise ValueError(f"结果文件 retrieval_calls 必须为 0，实际为 {result.get('retrieval_calls')}")
    if not result.get("model"):
        raise ValueError("结果文件缺少生成模型名称")

    client = Client()
    dataset_name = _dataset_name(result)
    experiment_name = _experiment_name(result)
    meta = _metadata(result, result_path, evaluation_path)
    dataset = client.create_dataset(
        dataset_name=dataset_name,
        description=f"冻结检索证据上的 12 题 {result.get('model')} 生成基线；离线回放，不重新执行检索。",
        metadata=meta,
    )
    examples_payload = []
    for row in result["rows"]:
        case = cases[str(row["question_id"])]
        examples_payload.append({
            # Keep the complete per-question replay visible directly on the
            # Dataset example. The Experiment run stores the same data too,
            # but Dataset examples are what users see first in LangSmith.
            "inputs": {
                "question_id": row["question_id"],
                "question": row.get("question", ""),
                "snapshot_id": row.get("snapshot_id"),
                "allowed_citations": row.get("allowed_citations", []),
                "evidence_count": row.get("evidence_count"),
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
                "expected_answer_spans": case.get("expected_answer_spans", []),
                "expected_sources": case.get("expected_sources", []),
            },
            "metadata": {
                "split": "generation-baseline-12-result",
                "source": str(evaluation_path),
                "question_id": row["question_id"],
                "result_run_id": result.get("run_id"),
                "prompt_version": result.get("prompt_version"),
                "audit_version": result.get("audit_version"),
                "evaluation_version": result.get("evaluation_version"),
                "repetition_index": result.get("repetition_index"),
                "model": result.get("model"),
            },
        })
    client.create_examples(dataset_id=dataset.id, examples=examples_payload)
    examples = {str((item.inputs or {}).get("question_id")): item for item in client.list_examples(dataset_id=dataset.id, limit=100)}
    if len(examples) != 12:
        raise RuntimeError(f"Dataset examples 创建后数量异常：{len(examples)}")

    metric_keys = ["generation_success", "citation_validity", "citation_id_usage_ratio", "expected_answer_span_recall", "required_term_recall", "source_coverage", "unsupported_number_count", "unsupported_claim_count", "refusal_correctness", "ambiguity_safety", "generation_latency_seconds", "answer_length"]
    project = client.create_project(
        experiment_name,
        description="12 题离线生成回放实验（固定 EvidencePack，retrieval_calls=0）。",
        reference_dataset_id=dataset.id,
        num_examples=12,
        num_repetitions=1,
        evaluator_keys=metric_keys,
        metadata=meta,
    )
    uploaded = 0
    for index, row in enumerate(result["rows"]):
        question_id = str(row["question_id"])
        example = examples[question_id]
        latency = _numeric(row.get("generation_latency_seconds")) or 0.0
        end_time = datetime.now(timezone.utc)
        run_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{dataset.id}|{experiment_name}|{question_id}|{result.get('run_id')}")
        outputs = {
            "answer": row.get("answer"),
            "audit": row.get("audit") or {},
            "generation_status": row.get("generation_status", row.get("status")),
            "generation_success": row.get("generation_success"),
            "error": row.get("error"),
        }
        client.create_run(
            id=run_id,
            name=f"generation-replay-{question_id}",
            run_type="chain",
            project_name=experiment_name,
            inputs={"question_id": question_id, "question": row.get("question", "")},
            outputs=outputs,
            start_time=end_time - timedelta(seconds=max(0.0, latency)),
            end_time=end_time,
            reference_example_id=example.id,
            extra={"metadata": {**meta, "question_id": question_id, "attempt_count": row.get("attempt_count"), "provider_error_type": row.get("provider_error_type")}},
            tags=["generation-replay", "offline", "frozen-evidence", "baseline-12"],
        )
        for key, value in _metric_values(row).items():
            score = _numeric(value)
            if score is not None:
                client.create_feedback(run_id=run_id, key=key, score=score, session_id=project.id, comment="离线生成回放指标；未重新执行检索。", extra={"source_result_file": str(result_path), "question_id": question_id})
        uploaded += 1
    if hasattr(client, "flush"):
        client.flush()
    # Dataset Experiments are not considered complete until the project is
    # explicitly closed. Without end_time LangSmith may show "12/12 runs" but
    # return an empty per-example table in Datasets & Experiments.
    client.update_project(project.id, end_time=datetime.now(timezone.utc))
    return {"dataset_name": dataset_name, "dataset_id": str(dataset.id), "experiment_name": experiment_name, "uploaded_count": uploaded, "source_result_file": str(result_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="创建新的 LangSmith Dataset 并上传 12 题离线生成回放")
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--evaluation", type=Path, default=DEFAULT_EVALUATION)
    parser.add_argument("--dry-run", action="store_true", help="只校验并打印将要创建的名称，不访问 LangSmith")
    args = parser.parse_args()
    result_path = args.result.resolve()
    evaluation_path = args.evaluation.resolve()
    if not result_path.exists() or not evaluation_path.exists():
        raise SystemExit(f"文件不存在：result={result_path}, evaluation={evaluation_path}")
    result, cases = load_inputs(result_path, evaluation_path)
    if args.dry_run:
        _print_plan(result, result_path, evaluation_path)
        return 0
    print(json.dumps(upload(result, cases, result_path, evaluation_path), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
