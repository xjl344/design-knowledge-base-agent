"""Weekly feedback triage, cost summary, and bad-case regression exporter.

Usage:
    python scripts/triage_feedback.py --summary
    python scripts/triage_feedback.py --list-pending
    python scripts/triage_feedback.py --triage fb-xxxx --category R1 --attribution system_failure --disposition "补切分"
    python scripts/triage_feedback.py --export-regression fb-xxxx --out data/regression_from_feedback.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import settings
from src.cost_ledger import QueryCostLedger, summarize_query_costs
from src.feedback import (
    ATTRIBUTION_KINDS,
    FAILURE_CATEGORIES,
    FeedbackLedger,
    summarize_feedback,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="反馈台账周报、坏案例归类与回归集导出")
    parser.add_argument(
        "--feedback-ledger",
        type=Path,
        default=settings.feedback_ledger_path,
        help="反馈台账 JSONL 路径",
    )
    parser.add_argument(
        "--cost-ledger",
        type=Path,
        default=settings.query_cost_ledger_path,
        help="每问成本账本 JSONL 路径",
    )
    parser.add_argument("--summary", action="store_true", help="输出反馈与成本汇总")
    parser.add_argument("--list-pending", action="store_true", help="列出待归类的点踩坏案例")
    parser.add_argument("--triage", default=None, help="要归类的 feedback_id")
    parser.add_argument("--category", choices=sorted(FAILURE_CATEGORIES), default=None)
    parser.add_argument("--attribution", choices=sorted(ATTRIBUTION_KINDS), default=None)
    parser.add_argument("--disposition", default="", help="处置结论")
    parser.add_argument("--export-regression", default=None, help="导出为回归集的 feedback_id")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "regression_from_feedback.jsonl",
        help="回归集输出 JSONL 路径",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出汇总")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    fb_ledger = FeedbackLedger(args.feedback_ledger)
    cost_ledger = QueryCostLedger(args.cost_ledger)

    if args.triage:
        if not args.category or not args.attribution:
            print("归类必须同时提供 --category 和 --attribution", file=sys.stderr)
            return 2
        updated = fb_ledger.triage(
            args.triage,
            failure_category=args.category,
            attribution_kind=args.attribution,
            disposition=args.disposition,
        )
        print(
            f"已归类 {updated.feedback_id}: {updated.failure_category} "
            f"({updated.attribution_kind}) -> {updated.disposition or '无备注'}"
        )
        return 0

    if args.export_regression:
        case = fb_ledger.export_regression_case(args.export_regression, args.out)
        print(f"已导出回归样本 {case['case_id']} -> {args.out}")
        return 0

    if args.list_pending:
        pending = [
            r for r in fb_ledger.load_all()
            if r.verdict == "down" and r.triage_status != "triaged"
        ]
        if not pending:
            print("当前没有待归类的坏案例。")
            return 0
        for item in pending:
            print(
                f"{item.feedback_id} | {item.created_at} | reason={item.reason_code} | "
                f"blamed={item.blamed_evidence_ids} | refusal={item.was_refusal} | "
                f"note={item.free_text[:60]}"
            )
        return 0

    fb_records = fb_ledger.load_all()
    cost_records = cost_ledger.load_all()
    cost_summary = summarize_query_costs(cost_records)
    fb_summary = summarize_feedback(
        fb_records,
        total_sessions=cost_summary["total_queries"] or None,
    )
    payload = {"feedback": fb_summary, "cost": cost_summary}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    cold_rate = cost_summary["cold_start_rate"]
    cold_rate_text = f"{cold_rate:.1%}" if cold_rate is not None else "N/A"
    print("=== 反馈闭环与每问成本周报 ===")
    print(
        f"总问答数: {cost_summary['total_queries']} | "
        f"冷启动: {cost_summary['cold_start_count']} ({cold_rate_text}) | "
        f"延迟 p50/p95: {cost_summary['latency_p50_seconds']}s / {cost_summary['latency_p95_seconds']}s"
    )
    print(
        f"单问 Token p50: {cost_summary['tokens_p50']} | "
        f"单问估算费用 p50: ¥{cost_summary['cost_p50_cny']} | "
        f"累计估算费用: ¥{cost_summary['total_estimated_cost_cny']}"
    )
    print(
        f"反馈总数: {fb_summary['total_feedback']}（👍 {fb_summary['up_count']} / 👎 {fb_summary['down_count']}）| "
        f"待归类坏案例: {fb_summary['pending_bad_cases']} | 已归类: {fb_summary['triaged_bad_cases']} | "
        f"拒答反馈: {fb_summary['refusal_feedback_count']}"
    )
    if fb_summary["submission_rate"] is not None:
        print(f"反馈提交率: {fb_summary['submission_rate']:.1%}（{fb_summary['unique_feedback_sessions']}/{fb_summary['total_sessions']}）")
    if fb_summary["by_reason_code"]:
        print(f"点踩原因分布: {fb_summary['by_reason_code']}")
    if fb_summary["by_failure_category"]:
        print(f"坏案例分类分布: {fb_summary['by_failure_category']}")
    for warning in fb_summary["warnings"]:
        print(f"⚠️  {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
