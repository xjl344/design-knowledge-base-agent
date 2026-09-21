"""Download a LangSmith Experiment into local JSON and Markdown files."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from config import LOG_DIR


def _feedback_map(client, run) -> dict:
    result = {}
    try:
        for feedback in client.list_feedback(run_ids=[run.id]):
            result[feedback.key] = {"score": feedback.score, "comment": feedback.comment or ""}
    except Exception as exc:
        result["_error"] = str(exc)
    return result


def _pick_experiment(client, requested: str | None, prefix: str) -> str:
    if requested:
        return requested
    projects = list(client.list_projects(limit=200))
    candidates = [p for p in projects if getattr(p, "name", "").startswith(prefix)]
    candidates.sort(key=lambda p: getattr(p, "start_time", None) or datetime.min, reverse=True)
    if not candidates:
        raise RuntimeError(f"未找到以 {prefix} 开头的 Experiment")
    return candidates[0].name


def _load_manifest(path: str | None) -> dict:
    if not path:
        return {}
    manifest_path = Path(path)
    if not manifest_path.is_file():
        raise FileNotFoundError(f"未找到本地 manifest：{manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def download(experiment: str | None, prefix: str, output: str | None, manifest_path: str | None = None) -> int:
    from langsmith import Client

    client = Client()
    project_name = _pick_experiment(client, experiment, prefix)
    runs = [run for run in client.list_runs(project_name=project_name, is_root=True) if run.name == "Target"]
    runs.sort(key=lambda run: run.start_time or datetime.min)
    if not runs:
        raise RuntimeError(f"Experiment {project_name} 没有找到 Target runs")

    rows = []
    for run in runs:
        inputs = run.inputs or {}
        if "example" in inputs and isinstance(inputs["example"], dict):
            inputs = inputs["example"]
        outputs = run.outputs or {}
        row = {"run_id": str(run.id), "question": inputs.get("question", ""), "feedback": _feedback_map(client, run)}
        row.update({key: outputs.get(key) for key in (
            "question_id", "category", "difficulty", "expected_mode", "answer", "sources", "route",
            "web_search_triggered", "web_search_queries", "web_search_errors",
            "status", "delivery_status", "deliverable", "blocking_issues", "allowed_citations",
            "unsupported_claims", "evidence_matrix", "requirement_coverage", "claim_support_rate",
            "direct_evidence_count", "indirect_evidence_count", "confidence", "confidence_status",
            "rewrite_attempts", "tool_calls", "model_calls", "execution_trace", "duration_seconds",
            "retrieval_duration_seconds", "generation_duration_seconds", "token_count", "errors",
        )})
        rows.append(row)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = Path(output) if output else LOG_DIR / f"langsmith_experiment_{stamp}"
    if base.suffix:
        base = base.with_suffix("")
    json_path = base.with_suffix(".json")
    md_path = base.with_suffix(".md")
    manifest = _load_manifest(manifest_path)
    payload = {"experiment": project_name, "downloaded_at": datetime.now().isoformat(timespec="seconds"), "question_count": len(rows), "runtime": manifest.get("runtime", {}), "manifest": manifest, "rows": rows}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    lines = [f"# LangSmith Experiment: {project_name}", "", f"- 题目数：{len(rows)}", f"- JSON：`{json_path}`", "", "| # | ID | 路由 | 交付状态 | 覆盖率 | 引用 | 延迟(s) | 阻断 | judge |", "|---:|---|---|---|---:|---|---:|---|---:|"]
    for index, row in enumerate(rows, 1):
        question = row["question"] if len(row["question"]) <= 40 else row["question"][:37] + "..."
        feedback = row["feedback"]
        issues = ", ".join(str(item.get("status", "")) for item in (row.get("blocking_issues") or [])) or "—"
        judge = feedback.get("answer_correctness", {}).get("score", "—")
        lines.append(f"| {index} | {row.get('question_id', '') or question} | {row.get('route', '')} | {row.get('delivery_status', row.get('status', ''))} | {float(row.get('requirement_coverage', 0) or 0):.3f} | {', '.join(row.get('allowed_citations') or []) or '—'} | {float(row.get('duration_seconds', 0) or 0):.3f} | {issues} | {judge} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已下载 Experiment：{project_name}")
    print(f"JSON：{json_path}")
    print(f"Markdown：{md_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", default=None, help="Experiment 名称；省略时选择最新 design-kb-round2-*")
    parser.add_argument("--prefix", default="design-kb-round2")
    parser.add_argument("--output", default=None, help="输出文件前缀")
    parser.add_argument("--manifest", default=None, help="关联本地 round2 manifest JSON")
    args = parser.parse_args()
    return download(args.experiment, args.prefix, args.output, args.manifest)


if __name__ == "__main__":
    raise SystemExit(main())
