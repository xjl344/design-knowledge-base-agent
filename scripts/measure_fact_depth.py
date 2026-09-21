"""Measure how deep inside each chunk the expected facts sit.

Why this exists
---------------
``max_chars_per_item`` caps how much of each chunk reaches the model.  It is a
real speed win -- 43% off mean latency on the 12-question set -- but only if the
budget is set above the deepest fact the questions actually need.

Set it too low and the evidence is cut away: at 300 characters the single-hop
set lost q10 (span recall 1.0 -> 0.5), because its facts sit up to 536
characters into their chunk.  Set it from measurement and the same compression
costs nothing.

The two question sets differ sharply, which is why no single number is right:

    multi-hop (6)    facts within ~150 chars   -> 300 is generous
    single-hop (12)  facts up to ~536 chars    -> 600 is safe

So this prints the depth distribution and a suggested budget, and it should be
re-run whenever the question set or the snapshot changes.

Usage::

    python scripts/measure_fact_depth.py \
        --snapshot data/frozen_retrieval_cases.jsonl \
        --evaluation data/generation_eval.v2.json --max-items 5

    python scripts/measure_fact_depth.py \
        --snapshot data/frozen_multihop_cases.jsonl \
        --evaluation data/generation_eval.multihop.v1.json --max-items 99
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.frozen_evidence import build_evidence_pack, load_cases  # noqa: E402

# Round a suggested budget up to something readable, and add a margin so the
# fact is not sitting exactly on the cut.
BUDGET_ROUNDING = 100
BUDGET_MARGIN = 1.2


def _normalise(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _span_texts(spec: dict[str, Any]) -> list[str]:
    """Spans appear as ``{"text": ...}`` objects; tolerate bare strings too."""
    out: list[str] = []
    for span in spec.get("expected_answer_spans") or []:
        if isinstance(span, dict):
            text = span.get("text")
        else:
            text = span
        if text:
            out.append(str(text))
    return out


def _depth_in(content: str, texts: list[str]) -> int:
    """Deepest character offset at which any of ``texts`` can be located.

    Matching is on the numbers inside a span, because spans carry units
    ("680mm~760mm") while the extracted text does not ("680~760") and a verbatim
    compare would silently measure nothing.
    """
    normalised = _normalise(content)
    deepest = -1
    for text in texts:
        for number in re.findall(r"\d+(?:\.\d+)?", text):
            index = normalised.find(number)
            if index > deepest:
                deepest = index
    return deepest


def _measure_hop_case(spec: dict[str, Any], pack: Any) -> tuple[int, int, int]:
    """Measure each hop against *its own* chunk.

    This is the whole point of a hop declaration, and measuring any other way
    gives wrong answers: scanning every span against every chunk let an
    unrelated table's percentile values register as "deep" matches, and the
    tool reported 573 characters for a set that empirically loses nothing at
    300.
    """
    by_chunk = {item.chunk_id: item.page_content for item in pack.items}
    deepest = -1
    deepest_chars = 0
    measured = 0
    for hop in spec.get("required_hops") or []:
        if not isinstance(hop, dict):
            continue
        content = by_chunk.get(str(hop.get("source_chunk_id") or ""))
        if content is None:
            continue
        texts = [str(hop.get("expected_span") or "")]
        texts += [str(item) for item in (hop.get("required_terms") or [])]
        index = _depth_in(content, texts)
        if index < 0:
            continue
        measured += 1
        if index > deepest:
            deepest = index
            deepest_chars = len(_normalise(content))
    return deepest, deepest_chars, measured


def measure(snapshot: Path, evaluation: Path, max_items: int) -> dict[str, Any]:
    cases = load_cases(snapshot)
    specs = {
        str(case["id"]): case
        for case in json.loads(evaluation.read_text(encoding="utf-8")).get("cases", [])
    }
    rows: list[dict[str, Any]] = []
    for question_id, spec in specs.items():
        case = cases.get(question_id)
        if case is None:
            rows.append({"question_id": question_id, "status": "missing_from_snapshot"})
            continue
        pack = build_evidence_pack(case, max_items=max_items)
        if spec.get("required_hops"):
            deepest, deepest_chars, measured = _measure_hop_case(spec, pack)
            basis = "per_hop_chunk"
        else:
            deepest = -1
            deepest_chars = 0
            measured = 0
            texts = _span_texts(spec)
            for item in pack.items:
                index = _depth_in(item.page_content, texts)
                if index < 0:
                    continue
                measured += 1
                if index > deepest:
                    deepest = index
                    deepest_chars = len(_normalise(item.page_content))
            basis = "any_item"
        rows.append({
            "question_id": question_id,
            "status": "ok" if measured else "no_numeric_span",
            "items": len(pack.items),
            "chunk_chars": deepest_chars,
            "deepest_fact_at": deepest,
            "basis": basis,
            "ratio": round(deepest / deepest_chars, 3) if deepest >= 0 and deepest_chars else None,
        })

    depths = [row["deepest_fact_at"] for row in rows if row.get("deepest_fact_at", -1) >= 0]
    deepest_overall = max(depths) if depths else None
    suggested = None
    if deepest_overall is not None:
        raw = deepest_overall * BUDGET_MARGIN
        suggested = int(-(-raw // BUDGET_ROUNDING) * BUDGET_ROUNDING)
    return {
        "snapshot": str(snapshot),
        "evaluation": str(evaluation),
        "max_items": max_items,
        "questions": len(specs),
        "measured_questions": len(depths),
        "deepest_fact_at": deepest_overall,
        "suggested_budget": suggested,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="测量必需事实在每个 chunk 中的深度，用于设定字符预算")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument(
        "--max-items",
        type=int,
        default=5,
        help="与将要使用的 max_evidence 保持一致，否则测的不是同一份上下文",
    )
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    args = parser.parse_args()

    if not args.snapshot.exists() or not args.evaluation.exists():
        raise SystemExit(f"文件不存在：{args.snapshot} / {args.evaluation}")

    result = measure(args.snapshot, args.evaluation, args.max_items)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"快照：{Path(result['snapshot']).name}  max_items={result['max_items']}")
    print(f"题目：{result['questions']}（可测 {result['measured_questions']}）\n")
    print(f"{'question':<34} {'items':>5} {'chunk':>7} {'deepest':>8} {'ratio':>7}")
    for row in result["rows"]:
        if row["status"] != "ok":
            print(f"{row['question_id']:<34} {row['status']}")
            continue
        ratio = f"{row['ratio'] * 100:.0f}%" if row["ratio"] is not None else "—"
        print(f"{row['question_id']:<34} {row['items']:>5} {row['chunk_chars']:>7} "
              f"{row['deepest_fact_at']:>8} {ratio:>7}")

    if result["deepest_fact_at"] is None:
        print("\n没有任何可测的事实位置（期望片段里没有数字？），无法给出预算建议。")
        return 0

    unmeasured = [
        row["question_id"] for row in result["rows"] if row["status"] == "no_numeric_span"
    ]
    print(
        f"\n最深的事实出现在第 {result['deepest_fact_at']} 字符处"
        f"\n建议 max_chars_per_item = {result['suggested_budget']}"
        f"（= 最深位置 × {BUDGET_MARGIN} 向上取整到 {BUDGET_ROUNDING}）"
        "\n低于这个值会切掉事实；远高于它则拿不到压缩收益。"
    )
    if unmeasured:
        # This is not a footnote.  The one question that actually broke under a
        # 300-char budget (q10, span recall 1.0 -> 0.5) is an enumeration whose
        # expected fact is a concept rather than a number -- exactly the kind
        # this measurement cannot see.  Reporting a budget while hiding that
        # would be the confident-but-wrong answer.
        print(
            f"\n⚠️ 以下 {len(unmeasured)} 题无法测量（期望片段不是字面数字，"
            f"而是概念或枚举）：{', '.join(unmeasured)}"
            "\n   它们的必需事实可能比上面测到的更深，预算对这些题没有保障。"
            "\n   降低预算后请用真实回放确认这些题的指标没有下滑。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
