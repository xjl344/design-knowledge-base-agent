"""Recompute a paired effect from a recorded run, under both statistics conventions.

Why this exists
---------------
A report that says "evidence quantity: +0.250" is citing a number.  Anyone
reading it should be able to re-derive that number from files in the repository
-- otherwise the claim is only as good as the transcript it came from.

Two traps make that harder than it looks, and this script exists to close both:

1. **Two audit layers.**  A row carries ``row["audit"]``, produced at run time
   under whatever contract was current then.  Re-reading that field gives the
   *old* scoring, not the report's.  The report re-scores offline with the
   current contract (``soft_audit``).  Reading the wrong one produces numbers
   that look like a reproducibility failure but are really a layer mismatch.

2. **Two statistics conventions.**  Pairing by ``(case_set, question, round)``
   treats one question's rounds as independent observations, which understates
   the standard error.  ``src/paired_statistics.py`` aggregates each question to
   a single observation first.  Both are printed, because the size of the
   understatement is itself a result -- but only the unit-level block is a
   number to quote.

Nothing here re-implements the scoring or the arithmetic.  Re-scoring comes from
``analyze_prompt_ab.rescore_row`` and the statistics from
``src.paired_statistics``; two copies of a convention is how the layers drifted
apart to begin with.

Usage::

    python scripts/recompute_paired_effect.py \
        --run data/runs/mh_gate_probe_v2.json --pair evidence5,evidenceall
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analyze_prompt_ab import rescore_row  # noqa: E402
from src.paired_statistics import (  # noqa: E402
    COMPLETED,
    PAIRED_STATISTICS_VERSION,
    paired_rows,
    summarise_pairs,
)


def build_cache(payload: dict[str, Any]) -> dict[str, Any]:
    """The arm -> pack-configuration map the re-scoring needs."""
    return {
        "arms": {
            str(arm["name"]): (arm.get("max_items"), arm.get("max_chars_per_item"))
            for arm in payload.get("arms") or []
        }
    }


def rescore(rows: list[dict[str, Any]], cache: dict[str, Any]) -> tuple[list[dict], int]:
    """Replace each row's audit with one recomputed under the current contract.

    The whole audit dict is swapped, not just ``hop_recall``.  Patching one field
    would leave a row half-scored by the old contract and half by the new one,
    which is harder to reason about than either layer on its own.
    """
    rescored: list[dict[str, Any]] = []
    skipped = 0
    for row in rows:
        if row.get("status") != COMPLETED:
            skipped += 1
            continue
        audit = rescore_row(row, cache)
        if audit is None:
            skipped += 1
            continue
        patched = dict(row)
        patched["audit"] = audit
        rescored.append(patched)
    return rescored, skipped


def hop_levels(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, int]]:
    """Pooled hop hits/total per (case set, arm), on the recomputed audit.

    Counts rather than a mean of per-question ratios: a two-hop question and a
    three-hop question must not carry the same weight when the denominator is
    hops.  Rows whose contract declares the hop metric inapplicable are skipped
    -- they have no hops to count, which is not the same as scoring zero.
    """
    levels: dict[tuple[str, str], dict[str, int]] = {}
    for row in rows:
        audit = row.get("audit") or {}
        if not audit.get("hop_metric_applicable"):
            continue
        hops = audit.get("hop_results") or []
        bucket = levels.setdefault(
            (str(row["case_set"]), str(row["arm"])), {"matched": 0, "total": 0}
        )
        bucket["total"] += len(hops)
        bucket["matched"] += sum(1 for hop in hops if hop.get("matched"))
    return levels


def superseded_pair_statistics(
    pairs: list[dict[str, Any]], arms: list[str]
) -> dict[str, Any]:
    """The old convention, kept only to measure how much it understated.

    ⚠️ Do not quote these.  ``stdev`` is taken across *pairs*, so one question's
    rounds count as separate evidence.  The block exists so the size of the
    understatement is a number rather than an assertion.

    The arm levels are repeated here because they are pair-weighted, while the
    current block's are unit-weighted.  On this project's data the two agree to
    three decimals for one arm and differ by 0.008 for the other -- enough to
    look like a disagreement if only one layer is printed.
    """
    first, second = arms
    comparable = [
        pair
        for pair in pairs
        if pair.get(f"{first}_hop_recall") is not None
        and pair.get(f"{second}_hop_recall") is not None
    ]
    if not comparable:
        return {"comparable_pairs": 0}
    diffs = [
        pair[f"{second}_hop_recall"] - pair[f"{first}_hop_recall"]
        for pair in comparable
    ]
    mean = statistics.fmean(diffs)
    stdev = statistics.stdev(diffs) if len(diffs) > 1 else 0.0
    standard_error = stdev / math.sqrt(len(diffs))
    return {
        "comparable_pairs": len(diffs),
        f"{first}_mean_hop_recall": round(
            statistics.fmean(pair[f"{first}_hop_recall"] for pair in comparable), 3
        ),
        f"{second}_mean_hop_recall": round(
            statistics.fmean(pair[f"{second}_hop_recall"] for pair in comparable), 3
        ),
        "mean_difference": round(mean, 4),
        "stdev": round(stdev, 4),
        "standard_error": round(standard_error, 4),
        "ci95": (
            round(mean - 1.96 * standard_error, 4),
            round(mean + 1.96 * standard_error, 4),
        ),
        "mde": round(1.96 * standard_error, 4),
    }


def paired_entry(
    rows: list[dict[str, Any]], arms: list[str]
) -> dict[str, Any]:
    """One paired result, current convention plus the superseded one beside it."""
    pairs, dropped = paired_rows(rows, arms)
    entry = summarise_pairs(pairs, arms)
    entry["dropped_cells"] = dropped
    entry["superseded_pair_level"] = superseded_pair_statistics(pairs, arms)
    return entry


def analyse(payload: dict[str, Any], arms: list[str]) -> dict[str, Any]:
    first, second = arms
    rows = payload.get("rows") or []
    cache = build_cache(payload)
    rescored, skipped = rescore(rows, cache)

    levels: dict[str, Any] = {}
    for (case_set, arm), bucket in sorted(hop_levels(rescored).items()):
        levels[f"{case_set}|{arm}"] = {
            **bucket,
            "ratio": (
                round(bucket["matched"] / bucket["total"], 3)
                if bucket["total"]
                else None
            ),
        }

    by_case_set = {
        case_set: paired_entry(
            [row for row in rescored if str(row["case_set"]) == case_set], arms
        )
        for case_set in sorted({str(row["case_set"]) for row in rescored})
    }

    return {
        "run_id": payload.get("run_id"),
        "audit_layer": "soft_audit（用当前契约离线重算），不是 row['audit']（运行时旧契约）",
        "statistics_version": PAIRED_STATISTICS_VERSION,
        "recorded_audit_version": payload.get("audit_version"),
        "pair": [first, second],
        "rows_total": len(rows),
        "rows_rescored": len(rescored),
        "rows_skipped": skipped,
        "hop_levels": levels,
        # Pooling across case sets is legitimate **for the paired difference**:
        # each cell contributes only its own within-cell difference, so the
        # difficulty level of a case set cancels.  It is not legitimate for the
        # levels, which is why the per-set block is always printed too.
        "pooled": paired_entry(rescored, arms),
        "by_case_set": by_case_set,
    }


def describe(label: str, entry: dict[str, Any], arms: list[str]) -> list[str]:
    """Render one paired result: current convention first, superseded second.

    Both conventions' arm levels are printed even though only the current one is
    quotable.  They are weighted differently -- per question vs per cell -- so a
    reader who has the report's pair-weighted level in hand would otherwise see
    two different "evidenceall" numbers and read it as a contradiction.
    """
    first, second = arms
    ci = entry.get("ci95")
    old = entry.get("superseded_pair_level") or {}
    lines = [
        f"\n  [{label}] 配对={entry['comparable_pairs']} "
        f"独立单元={entry['independent_units']} dropped={entry['dropped_cells']}"
    ]
    if ci is None:
        lines.append("    ✅ 现行口径（单元层）：独立单元不足，算不出区间")
        return lines

    lines.append(
        f"    ✅ 现行口径（单元层）：差={entry['mean_difference']:+.4f} "
        f"SD={entry['stdev']} SE={entry['standard_error']} "
        f"CI95=[{ci[0]:+.4f}, {ci[1]:+.4f}] MDE={entry['mde']}"
    )
    lines.append(
        f"       水平（每题等权）：{first}={entry.get(f'{first}_mean_hop_recall')} "
        f"{second}={entry.get(f'{second}_mean_hop_recall')}"
    )
    if old.get("standard_error"):
        inflation = entry["standard_error"] / old["standard_error"]
        lines.append(
            f"    ⚠️ 旧口径（配对层，勿引用）：差={old['mean_difference']:+.4f} "
            f"SE={old['standard_error']} "
            f"CI95=[{old['ci95'][0]:+.4f}, {old['ci95'][1]:+.4f}] MDE={old['mde']}"
        )
        lines.append(
            f"       水平（每格等权）：{first}={old.get(f'{first}_mean_hop_recall')} "
            f"{second}={old.get(f'{second}_mean_hop_recall')}"
        )
        lines.append(
            f"       → 旧口径把 SE 低估了 {inflation:.2f} 倍"
            f"（{old['standard_error']} → {entry['standard_error']}）"
        )
    lines.append(
        f"    → 现行口径 CI {'跨过' if ci[0] < 0 < ci[1] else '不跨过'} 0"
        f"：{second} 更好 {entry.get(f'{second}_better_pairs')} 题 / "
        f"{first} 更好 {entry.get(f'{first}_better_pairs')} 题 / "
        f"持平 {entry['tied_pairs']} 题（配对层计数）"
    )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, required=True, help="记录下来的运行文件")
    parser.add_argument(
        "--pair",
        required=True,
        help="两臂，逗号分隔：第一臂,第二臂（差值 = 第二臂 - 第一臂）",
    )
    parser.add_argument("--json", type=Path, help="把完整结果写到该路径")
    args = parser.parse_args(argv)

    arms = [part.strip() for part in args.pair.split(",")]
    if len(arms) != 2 or not all(arms):
        raise SystemExit(f"--pair 需要恰好两个臂名，收到 {args.pair!r}")

    payload = json.loads(args.run.read_text(encoding="utf-8"))
    result = analyse(payload, arms)

    print(f"运行：{args.run}（run_id={result['run_id']}）")
    print(f"审计层：{result['audit_layer']}")
    print(
        f"文件记录的 audit_version={result['recorded_audit_version']}，"
        f"配对统计口径={result['statistics_version']}"
    )
    print(
        f"重算覆盖 {result['rows_rescored']}/{result['rows_total']} 行"
        f"（跳过 {result['rows_skipped']}：未完成，或题不在快照/评测集里）"
    )

    print("\n各臂跳覆盖率（重算口径，pooled 命中/总跳数）：")
    for key, bucket in result["hop_levels"].items():
        print(
            f"  {key:<28} {bucket['matched']}/{bucket['total']} = {bucket['ratio']}"
        )

    print("\n配对效应（差值 = 第二臂 - 第一臂）：")
    for line in describe("两题集合并", result["pooled"], arms):
        print(line)
    print("\n  分题集（合并口径合法的是「差值」，不是「水平」——水平受题集难度影响）：")
    for case_set, entry in result["by_case_set"].items():
        for line in describe(case_set, entry, arms):
            print(line)

    if args.json:
        args.json.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n完整结果已写入 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
