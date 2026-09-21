"""Upload previously saved retrieval evaluations to LangSmith without rerunning retrieval.

Each local result row becomes one retriever run linked to the matching dataset
example. This is intended for historical F/R/P logs that were generated before
LangSmith uploading was enabled.
"""

from __future__ import annotations

import argparse
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
DEFAULT_DIR = ROOT / "logs"
DATASET_NAME = "design-knowledge-chunking-10"

# LangSmith Client reads credentials from the process environment. Load the
# project-local .env before constructing the client so a fresh PowerShell
# session can upload without printing or manually copying the API key.
load_dotenv(ROOT / ".env", override=False)


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _source_id(value: str) -> str:
    return "".join(str(value or "").replace("\\", "/").casefold().split()).rsplit("/", 1)[-1]


def _source_matches(expected: str, actual: str) -> bool:
    expected = "".join(str(expected or "").replace("\\", "/").casefold().split())
    actual = "".join(str(actual or "").replace("\\", "/").casefold().split())
    return _source_id(expected) == _source_id(actual) or expected in actual


def _row_metrics(row: dict[str, Any]) -> dict[str, float]:
    metrics = row.get("source_level_metrics") or row.get("retrieval_metrics") or {}

    def numeric(value: Any) -> float:
        # Older logs may omit a metric or explicitly store null when an
        # evaluator failed. LangSmith feedback requires a numeric score, so
        # use zero only for the legacy upload representation.
        try:
            return float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    return {
        "source_hit": numeric(row.get("source_hit", 0.0)),
        "recall_at_5": numeric(metrics.get("recall_at_5", 0.0)),
        "recall_at_10": numeric(metrics.get("recall_at_10", 0.0)),
        "mrr": numeric(metrics.get("mrr", 0.0)),
        "ndcg_at_10": numeric(metrics.get("ndcg_at_10", 0.0)),
    }


def _infer_strategy(path: Path, payload: dict[str, Any]) -> str:
    value = str(payload.get("strategy", "")).upper()
    if value in {"F", "R", "P"}:
        return value
    match = re.search(r"(?:^|[_-])(F|R|P)(?:[_-]|$)", path.stem, re.I)
    if not match:
        raise ValueError(f"无法从结果文件识别策略 F/R/P：{path}")
    return match.group(1).upper()


def load_result_file(path: Path) -> tuple[str, list[dict[str, Any]]]:
    # Windows PowerShell 5.1 may emit a UTF-8 BOM; utf-8-sig accepts both BOM
    # and BOM-less JSON files.
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError(f"结果文件缺少 results 数组：{path}")
    return _infer_strategy(path, payload), payload["results"]


def _run_outputs(row: dict[str, Any], strategy: str, source_file: Path) -> dict[str, Any]:
    # Keep the complete diagnostic row so the historical run is inspectable in
    # LangSmith; remove no candidate-stage details.
    outputs = dict(row)
    outputs["historical_upload"] = True
    outputs["historical_strategy"] = strategy
    outputs["historical_source_file"] = str(source_file)
    return outputs


def _project_suffix(path: Path) -> str:
    """Create a short stable label so each historical file is a separate project."""
    stem = path.stem
    # Ablation folders contain identically named files such as
    # ``R_retrieval.json``. Include the parent folder in that case so baseline,
    # source-role, and full-independent runs remain separate in LangSmith.
    if stem.endswith("_retrieval") and path.parent.name:
        stem = f"{path.parent.name}-{stem}"
    # Keep experiment names readable in LangSmith while avoiding path
    # separators and punctuation that make filtering awkward.
    suffix = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-_")
    return suffix[:80] or "result"


def _ensure_experiment_project(client: Any, dataset: Any, project_name: str, strategy: str, path: Path, rows: list[dict[str, Any]]) -> str:
    """Create/bind a dataset experiment, avoiding tracing-project name clashes."""
    metric_keys = list(_row_metrics(rows[0]).keys()) if rows else []
    kwargs = {
        "upsert": True,
        "reference_dataset_id": dataset.id,
        "num_examples": len(rows),
        "num_repetitions": 1,
        "evaluator_keys": metric_keys,
        "metadata": {
            "historical_upload": True,
            "strategy": strategy,
            "source_result_file": str(path),
            "source_result_mtime": path.stat().st_mtime,
        },
        "description": f"历史检索结果回放：{path.name}",
    }
    try:
        client.create_project(project_name, **kwargs)
        return project_name
    except Exception as exc:
        # A project with this name may already exist as a tracing project.
        # LangSmith disallows changing that project's type, so create a
        # deterministic experiment-specific name instead.
        if "different type" not in str(exc).lower() and "conflict" not in str(exc).lower():
            raise
        experiment_name = f"{project_name}-dataset-experiment"
        client.create_project(experiment_name, **kwargs)
        print(f"项目名称冲突，已改用 dataset experiment：{experiment_name}")
        return experiment_name


def upload_file(client: Any, dataset: Any, path: Path, experiment_prefix: str, dry_run: bool = False) -> int:
    strategy, rows = load_result_file(path)
    examples = {
        (item.inputs or {}).get("question", ""): item
        for item in client.list_examples(dataset_id=dataset.id, limit=1000)
    }
    if not examples:
        raise ValueError(f"LangSmith 数据集没有 examples，无法绑定结果：{dataset.name}")

    project_name = f"{experiment_prefix}-{strategy}-{_project_suffix(path)}"
    # Explicitly register the project as an experiment for this dataset.
    # Merely setting reference_example_id on individual runs stores the runs,
    # but does not reliably make the project appear in the dataset's
    # Experiments tab across LangSmith versions.
    if not dry_run and hasattr(client, "create_project"):
        project_name = _ensure_experiment_project(
            client, dataset, project_name, strategy, path, rows
        )
    uploaded = 0
    for index, row in enumerate(rows):
        question = str(row.get("question", ""))
        example = examples.get(question)
        if example is None:
            raise ValueError(f"结果中的题目不在数据集 {dataset.name}：{question[:80]}")
        metrics = _row_metrics(row)
        if dry_run:
            print(f"DRY-RUN {project_name} #{index + 1}: {question[:60]} metrics={metrics}")
            uploaded += 1
            continue

        run_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"design-kb-history|{project_name}|{path.resolve()}|{index}",
        )
        # Re-running a historical upload should be idempotent. The SDK has
        # changed its not-found exception over time, so treat any successful
        # read as "already uploaded" and only ignore read failures.
        if hasattr(client, "read_run"):
            try:
                client.read_run(run_id)
                uploaded += 1
                continue
            except Exception:
                pass
        end_time = datetime.now(timezone.utc)
        latency = float(row.get("latency_seconds") or 0.0)
        start_time = end_time - timedelta(seconds=max(0.0, latency))
        client.create_run(
            id=run_id,
            name=f"historical-retrieval-{strategy}-{index + 1:02d}",
            run_type="retriever",
            project_name=project_name,
            inputs={"question": question},
            outputs=_run_outputs(row, strategy, path),
            start_time=start_time,
            end_time=end_time,
            reference_example_id=example.id,
            extra={
                "metadata": {
                    "historical_upload": True,
                    "strategy": strategy,
                    "source_result_file": str(path),
                    "source_result_mtime": path.stat().st_mtime,
                }
            },
            tags=["historical", "retrieval", f"strategy:{strategy}", f"iteration:{_project_suffix(path)}"],
        )
        for key, score in metrics.items():
            client.create_feedback(
                run_id=run_id,
                key=key,
                score=score,
                comment="从本地历史检索结果回放上传；未重新执行检索。",
                extra={"historical_upload": True, "source_result_file": str(path)},
            )
        uploaded += 1
    if not dry_run and hasattr(client, "flush"):
        client.flush()
    return uploaded


def main() -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(description="将本地历史 F/R/P 检索结果补传到 LangSmith")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--files", nargs="*", type=Path, help="指定 JSON 文件；省略时读取目录内全部 *_retrieval_*.json")
    parser.add_argument("--dataset", default=DATASET_NAME)
    parser.add_argument("--experiment-prefix", default="design-kb-history")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Scan nested cap/source-cap experiment folders as well when no explicit
    # files are supplied. Every matching file maps to its own LangSmith
    # project, so historical comparisons remain separated.
    files = args.files or sorted(args.input_dir.rglob("*_retrieval_*.json"))
    if not files:
        raise SystemExit(f"未找到历史结果文件：{args.input_dir}")
    if args.dry_run:
        client = None
        dataset = None
        for path in files:
            strategy, rows = load_result_file(path)
            print(f"{path.name}: strategy={strategy}, rows={len(rows)}")
        return 0

    from langsmith import Client
    from eval_chunking_langsmith import load_questions, sync_dataset

    client = Client()
    # Always synchronize the canonical questions. This handles a missing,
    # partially-created, or stale dataset uniformly before binding runs.
    dataset = sync_dataset(client, load_questions(), dataset_name=args.dataset)

    total = 0
    for path in files:
        count = upload_file(client, dataset, path, args.experiment_prefix)
        strategy, _ = load_result_file(path)
        project_name = f"{args.experiment_prefix}-{strategy}-{_project_suffix(path)}"
        print(f"已补传 {path.name}: {count} 条 -> {project_name}")
        total += count
    print(f"历史结果补传完成：{total} 条 run。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
