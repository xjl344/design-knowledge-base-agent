"""Measure whether context size changes latency, with the drift cancelled out.

The problem this solves
-----------------------
Provider latency drifted 2.3x within one and a half hours (23s -> 41s -> 53s
mean for a *fixed* configuration), and the same provider returns different
answers for 11 of 12 questions at temperature 0.  So the obvious experiment --
run all of arm A, then all of arm B -- cannot attribute a latency difference to
the configuration: whatever the provider was doing during the second half lands
entirely on arm B.

This probe interleaves instead.  Within a round it runs both arms on the same
question back to back, so drift affects the pair almost equally, and the
comparison is made *within* each pair rather than between two blocks of time.

Two further guards:

* the order alternates per round (A,B then B,A), so a warm-up or cool-down
  effect cannot favour one arm;
* failures are recorded but excluded from the latency statistics, because a
  timeout's duration is set by the ceiling, not by the provider.

What it can and cannot settle
-----------------------------
It settles whether a larger context costs latency.  It says nothing about
answer quality: use the replay harness for that, where the verdicts are
computed per answer and do not depend on how fast the provider was.

Usage::

    python scripts/ab_latency_probe.py \
        --snapshot data/frozen_multihop_cases.jsonl \
        --evaluation data/generation_eval.multihop.v1.json \
        --arm small:5:none --arm large:99:none --rounds 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.frozen_evidence import build_evidence_pack, load_cases  # noqa: E402
from src.generator_v2 import GENERATOR_MAX_RETRIES, generate_from_pack  # noqa: E402
from config import settings  # noqa: E402

COMPLETED = "completed"


def _parse_arm(spec: str) -> dict[str, Any]:
    """``name:max_items:max_chars`` where max_chars may be ``none``."""
    parts = spec.split(":")
    if len(parts) != 3:
        raise SystemExit(f"臂定义必须是 name:max_items:max_chars，收到 {spec!r}")
    name, max_items, max_chars = parts
    budget = None if max_chars.lower() in ("none", "-", "") else int(max_chars)
    return {"name": name, "max_items": int(max_items), "max_chars_per_item": budget}


async def _measure(
    arm: dict[str, Any],
    case: Any,
    spec: dict[str, Any],
) -> dict[str, Any]:
    pack = build_evidence_pack(
        case,
        max_items=arm["max_items"],
        max_chars_per_item=arm["max_chars_per_item"],
    )
    started = time.perf_counter()
    result = await generate_from_pack(
        pack,
        expected_answer_spans=spec.get("expected_answer_spans", []),
        expected_sources=spec.get("expected_sources", []),
        required_terms=spec.get("required_terms", []),
        refusal_requirements=spec.get("refusal_requirements", []),
        ambiguity_requirements=spec.get("ambiguity_requirements", []),
        required_hops=spec.get("required_hops", []),
        max_retries=GENERATOR_MAX_RETRIES,
    )
    return {
        "status": result.generation_status,
        "latency_seconds": round(time.perf_counter() - started, 3),
        "context_chars": len(pack.context_text()),
        "evidence_count": len(pack.items),
    }


async def probe(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_cases(args.snapshot)
    specs = {
        str(case["id"]): case
        for case in json.loads(args.evaluation.read_text(encoding="utf-8")).get("cases", [])
    }
    arms = [_parse_arm(spec) for spec in args.arm]
    if len(arms) != 2:
        raise SystemExit("需要恰好两个臂（A 与 B）才能做配对比较")
    question_ids = [qid for qid in specs if qid in cases]
    if not question_ids:
        raise SystemExit("快照与契约没有交集，无可测题目")

    observations: list[dict[str, Any]] = []
    started = time.perf_counter()
    # Order is randomised per (round, question) rather than alternated on a
    # fixed schedule.  A strict A,B / B,A alternation is periodic, and a
    # provider whose slow phases have a similar period would be hit by one arm
    # every time -- the probe measured exactly that pattern once (one round
    # uniformly slower for one arm), which is indistinguishable from a real
    # effect.  The seed is recorded so a run stays reproducible.
    rng = random.Random(args.seed)
    for round_index in range(args.rounds):
        for question_id in question_ids:
            ordered = list(arms)
            if rng.random() < 0.5:
                ordered.reverse()
            for arm in ordered:
                observation = await _measure(arm, cases[question_id], specs[question_id])
                observations.append({
                    "round": round_index,
                    "question_id": question_id,
                    "arm": arm["name"],
                    "order": ordered.index(arm),
                    **observation,
                })
        print(
            f"round {round_index + 1}/{args.rounds} done "
            f"({len(observations)} calls, {time.perf_counter() - started:.0f}s)",
            file=sys.stderr,
            flush=True,
        )

    return {
        "snapshot": str(args.snapshot),
        "evaluation": str(args.evaluation),
        "arms": arms,
        "rounds": args.rounds,
        "order_seed": args.seed,
        "llm_model": settings.llm_model,
        "llm_timeout_seconds": float(settings.llm_timeout_seconds),
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "observations": observations,
        **summarise(observations, [arm["name"] for arm in arms]),
    }


def summarise(observations: list[dict[str, Any]], names: list[str]) -> dict[str, Any]:
    """Paired per-question comparison, excluding failed calls from latency."""
    first, second = names
    completed = [item for item in observations if item["status"] == COMPLETED]

    by_arm: dict[str, list[float]] = {name: [] for name in names}
    for item in completed:
        by_arm[item["arm"]].append(item["latency_seconds"])

    # Pair within (question, round): the two calls ran back to back, so they
    # share whatever the provider was doing at that moment.
    pairs: dict[tuple[str, int], dict[str, float]] = {}
    for item in completed:
        key = (item["question_id"], item["round"])
        pairs.setdefault(key, {})[item["arm"]] = item["latency_seconds"]
    diffs = [
        values[second] - values[first]
        for values in pairs.values()
        if first in values and second in values
    ]

    per_question: dict[str, list[float]] = {}
    for (question_id, _), values in pairs.items():
        if first in values and second in values:
            per_question.setdefault(question_id, []).append(values[second] - values[first])

    faster = sum(1 for diff in diffs if diff < 0)
    slower = sum(1 for diff in diffs if diff > 0)
    return {
        "completed_calls": len(completed),
        "failed_calls": len(observations) - len(completed),
        "failure_statuses": sorted(
            {item["status"] for item in observations if item["status"] != COMPLETED}
        ),
        "latency_by_arm": {
            name: {
                "n": len(values),
                "mean": round(statistics.fmean(values), 2) if values else None,
                "median": round(statistics.median(values), 2) if values else None,
                "max": round(max(values), 2) if values else None,
            }
            for name, values in by_arm.items()
        },
        "paired_difference": {
            "definition": f"{second} - {first}，正数表示 {second} 更慢",
            "pairs": len(diffs),
            "mean": round(statistics.fmean(diffs), 2) if diffs else None,
            "median": round(statistics.median(diffs), 2) if diffs else None,
            f"{second}_slower_pairs": slower,
            f"{second}_faster_pairs": faster,
            "per_question_mean": {
                question_id: round(statistics.fmean(values), 2)
                for question_id, values in sorted(per_question.items())
            },
        },
        "context_chars_by_arm": {
            name: sorted({
                item["context_chars"]
                for item in observations
                if item["arm"] == name and item.get("context_chars") is not None
            })
            for name in names
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="交替 A/B 延迟探针（抵消 provider 漂移）")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument(
        "--arm",
        action="append",
        required=True,
        help="name:max_items:max_chars（max_chars 可为 none）；需给两个",
    )
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260921,
                        help="臂顺序随机化的种子；固定以便复现")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    for path in (args.snapshot, args.evaluation):
        if not path.exists():
            raise SystemExit(f"文件不存在：{path}")
    if args.output.exists():
        raise SystemExit(f"拒绝覆盖已有结果：{args.output}。请指定新的 --output。")

    result = asyncio.run(probe(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(
        {
            "output": str(args.output),
            "latency_by_arm": result["latency_by_arm"],
            "paired_difference": result["paired_difference"],
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
