"""How much of `hop_recall` is the span defect, and how much is the model?

The defect (`check_span_normalisation.py`) means some declared spans are
*destroyed by normalisation* before the comparison starts.  A hop scored on such
a span is not scored harshly -- it is **not measurable at all**: no answer,
however correct, can match.  Reporting it as `0` therefore mixes "the model
missed this hop" with "this hop could not be credited".

This script brackets the correction instead of guessing it, because inventing a
"fixed" matcher would itself be a research decision:

* **lower bound** -- today's number: damaged observations count as misses.
* **upper bound** -- damaged observations are dropped from the denominator,
  on the grounds that they carry no information about the model.

The truth is between them, and the width of the bracket is the honest statement
of how much this defect can move the conclusion.  A hop on a *clean* span that
still fails is left alone: those are real misses or real paraphrase losses, and
separating them needs a human reading the answer, not a wider matcher.

Usage::

    python scripts/quantify_span_defect.py --run data/runs/mhreal_r3.json
    python scripts/quantify_span_defect.py --glob "data/runs/mhreal_*.json"
"""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_span_normalisation import inspect  # noqa: E402


def damaged_spans(evaluations: list[Path]) -> set[str]:
    """Every declared span that normalisation destroys, across the contracts.

    Keyed by the span text itself, not by question id: the same formula is
    declared in more than one contract, and a hop's audit records the span.
    """
    from scripts.check_span_normalisation import collect_spans

    return {
        item["span"]
        for item in collect_spans(evaluations)
        if inspect(item["span"])["labels_damage"]
    }


def measure(path: Path, damaged: set[str]) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = payload.get("rows") or []
    completed = [row for row in rows if row.get("status") == "completed"]

    matched = total = damaged_total = damaged_matched = 0
    damaged_hops: dict[str, int] = {}
    for row in completed:
        audit = row.get("audit") or {}
        if not audit.get("hop_metric_applicable"):
            continue
        for hop in audit.get("hop_results") or []:
            total += 1
            hit = bool(hop.get("matched"))
            matched += 1 if hit else 0
            if hop.get("expected_span") in damaged:
                damaged_total += 1
                damaged_matched += 1 if hit else 0
                key = f"{row['question_id']} {hop.get('hop_id')}"
                damaged_hops[key] = damaged_hops.get(key, 0) + 1

    clean_total = total - damaged_total
    clean_matched = matched - damaged_matched
    return {
        "run": path.name,
        "completed_rows": len(completed),
        "hops_total": total,
        "hops_matched": matched,
        "hops_on_damaged_span": damaged_total,
        "hops_matched_on_damaged_span": damaged_matched,
        "hops_on_clean_span": clean_total,
        "hops_matched_on_clean_span": clean_matched,
        "ratio_lower": round(matched / total, 3) if total else None,
        "ratio_upper": (
            round(matched / clean_total, 3) if clean_total else None
        ),
        "ratio_clean_only": (
            round(clean_matched / clean_total, 3) if clean_total else None
        ),
        "damaged_hops": dict(sorted(damaged_hops.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", action="append", help="运行文件，可重复")
    parser.add_argument("--glob", default="data/runs/mhreal_*.json", help="未指定 --run 时的通配")
    parser.add_argument(
        "--evaluation",
        action="append",
        help="契约文件（用于确定哪些跨段受损）；默认扫 data/generation_eval*.json",
    )
    args = parser.parse_args(argv)

    evaluations = (
        [Path(p) for p in args.evaluation]
        if args.evaluation
        else sorted(Path(p) for p in glob.glob("data/generation_eval*.json"))
    )
    damaged = damaged_spans(evaluations)
    print(f"契约里被规范化破坏的期望跨段：{len(damaged)} 个")
    for span in sorted(damaged):
        print(f"  · {span!r}")
    print()

    paths = (
        [Path(p) for p in args.run]
        if args.run
        else sorted(Path(p) for p in glob.glob(args.glob))
    )
    if not paths:
        raise SystemExit("没有找到运行文件")

    print(
        f"{'运行':28s} {'跳观测':>6s} {'命中':>5s} {'受损跳':>6s} "
        f"{'下界':>7s} {'上界':>7s}"
    )
    print("-" * 74)
    results = []
    for path in paths:
        if not path.exists():
            continue
        item = measure(path, damaged)
        results.append(item)
        print(
            f"{item['run']:28s} {item['hops_total']:6d} {item['hops_matched']:5d} "
            f"{item['hops_on_damaged_span']:6d} "
            f"{item['ratio_lower']!s:>7s} {item['ratio_upper']!s:>7s}"
        )

    print()
    for item in results:
        if not item["hops_on_damaged_span"]:
            continue
        print(f"{item['run']}: 受损跳来自")
        for key, count in item["damaged_hops"].items():
            print(f"    {key}  ×{count}")

    # Pooled, because the per-run denominators are small and differ.
    total = sum(item["hops_total"] for item in results)
    matched = sum(item["hops_matched"] for item in results)
    damaged_total = sum(item["hops_on_damaged_span"] for item in results)
    clean_total = total - damaged_total
    if total:
        print()
        print(f"合并 {len(results)} 次运行：跳观测 {total}，命中 {matched}")
        print(f"  下界（受损跳算失败）      : {matched}/{total} = {matched / total:.3f}")
        if clean_total:
            print(
                f"  上界（受损跳移出分母）    : {matched}/{clean_total} = "
                f"{matched / clean_total:.3f}"
            )
        print(
            f"  → 本缺陷最多能移动 {abs(matched / clean_total - matched / total):.3f}"
            f"（受影响的受损跳 {damaged_total} 个）"
            if clean_total
            else ""
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
