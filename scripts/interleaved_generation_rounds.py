"""Run the multi-hop arms interleaved, across question sets, for several rounds.

Why this exists
---------------
The replay harness runs one arm as a single block of time, so a provider that is
slow -- or outright failing -- for twenty minutes puts that window entirely on
one arm.  That is not hypothetical here: the real-question set's evidence-5 arm
recorded a 60% timeout rate while the all-evidence arm, run immediately after,
recorded 20%.  Read as a result, that says "fewer evidence items are more
reliable", which is not something the configuration can cause.

So the arms are interleaved instead, exactly as ``ab_latency_probe.py`` does for
latency: within a round, both arms run on the same question back to back, and the
order is randomised per (round, question).  A periodic alternation is *not* used
-- a provider whose slow phases share that period would be hit by one arm every
time, which is indistinguishable from a real effect and was measured once.

This script adds two things the latency probe does not have:

* **both question sets in one run.**  Comparing the synthetic set against the
  real set by running them in sequence confounds the set with the time of day,
  which is the same mistake one level up.  Here the two sets share the round, so
  the set comparison is also a within-round comparison.
* **the full audit per call.**  The probe only needs latency; deciding whether
  the model found the facts needs the scored row, so each call is scored by the
  same contract the replay harness uses.

Timeouts are recorded and never scored as answers.  They are counted separately
and reported per arm, because a missing row and a wrong row are different
findings: the first is an availability statement about the provider, the second
is a quality statement about the system.

Usage::

    python scripts/interleaved_generation_rounds.py \
        --case-set name=synthetic,\
snapshot=data/frozen_multihop_cases.jsonl,\
evaluation=data/generation_eval.multihop.v1.json \
        --case-set name=real,\
snapshot=data/frozen_multihop_real_cases.jsonl,\
evaluation=data/generation_eval.multihop.real.v1.json \
        --arm evidence5=5:none --arm evidenceall=99:none \
        --rounds 3 --output data/runs/mh_interleaved_3r.json
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import random
import statistics
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import settings  # noqa: E402
from eval_generation_replay import (  # noqa: E402
    file_sha256,
    load_evaluation,
    load_evaluation_version,
    make_run_id,
    summarize_rows,
)
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

COMPLETED = "completed"


def parse_case_set(spec: str) -> dict[str, str]:
    """``name=<snapshot>,snapshot=...,evaluation=...`` -- commas, not colons.

    Windows drive letters make a colon-separated spec ambiguous
    (``C:\\...``), so the separator is a comma and each field is ``key=value``.
    """
    fields: dict[str, str] = {}
    for piece in spec.split(","):
        key, separator, value = piece.partition("=")
        if not separator:
            raise SystemExit(f"题集字段必须是 key=value，收到 {piece!r}")
        fields[key.strip()] = value.strip()
    missing = {"name", "snapshot", "evaluation"} - set(fields)
    if missing:
        raise SystemExit(f"题集定义缺少字段 {sorted(missing)}：{spec!r}")
    return fields


def parse_arm(spec: str) -> dict[str, Any]:
    """``name=<max_items>:<max_chars>`` where max_chars may be ``none``."""
    name, separator, rest = spec.partition("=")
    if not separator:
        raise SystemExit(f"臂定义必须是 name=max_items:max_chars，收到 {spec!r}")
    max_items, colon, max_chars = rest.partition(":")
    if not colon:
        raise SystemExit(f"臂定义必须是 name=max_items:max_chars，收到 {spec!r}")
    budget = None if max_chars.strip().lower() in ("none", "-", "") else int(max_chars)
    return {"name": name.strip(), "max_items": int(max_items), "max_chars_per_item": budget}


async def score_one(
    *,
    case: Any,
    spec: dict[str, Any],
    arm: dict[str, Any],
    max_retries: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    pack = build_evidence_pack(
        case,
        max_items=arm["max_items"],
        max_chars_per_item=arm["max_chars_per_item"],
    )
    # Computed on every call, including calls that then fail: the damage is to
    # the evidence, which happened regardless of what the provider did next.
    lost_facts = declared_facts_lost_to_truncation(
        case,
        spec,
        max_items=arm["max_items"],
        max_chars_per_item=arm["max_chars_per_item"],
    )
    if dry_run:
        # No provider call.  Exists so the plumbing -- parsing, agenda
        # shuffling, summarising, output -- can be exercised without spending
        # an hour of calls to discover a typo.
        return {
            "question_id": pack.question_id,
            "snapshot_id": pack.snapshot_id,
            "evidence_count": len(pack.items),
            "context_chars": len(pack.context_text()),
            "truncation_lost_facts": lost_facts,
            "generation_latency_seconds": None,
            "error": None,
            "error_type": None,
            "generation_status": "ready",
            "status": "ready",
            "audit": {},
        }
    result = await generate_from_pack(
        pack,
        expected_answer_spans=spec.get("expected_answer_spans", []),
        expected_sources=spec.get("expected_sources", []),
        required_terms=spec.get("required_terms", []),
        refusal_requirements=spec.get("refusal_requirements", []),
        ambiguity_requirements=spec.get("ambiguity_requirements", []),
        required_hops=spec.get("required_hops", []),
        max_retries=max_retries,
    )
    row = {
        # `question_id` must be the *case* id: the contract and the pairing both
        # key on it.  `snapshot_id` is the retrieval snapshot's own id and is
        # kept separately -- using it as the question id silently paired
        # nothing.
        "question_id": pack.question_id,
        "snapshot_id": pack.snapshot_id,
        "evidence_count": len(pack.items),
        "context_chars": len(pack.context_text()),
        "truncation_lost_facts": lost_facts,
    }
    row.update(result.as_dict())
    # `status` mirrors the replay harness so `summarize_rows` can be reused
    # unchanged -- one definition of "the call succeeded", not two.
    row["status"] = result.generation_status
    return row


def hop_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pooled hop hits/total, not a mean of per-question ratios.

    A mean of ratios weights a two-hop question the same as a three-hop one, so
    it cannot be turned into a test statistic.  Counts can.
    """
    matched = total = 0
    per_question: dict[str, dict[str, int]] = {}
    for row in rows:
        audit = row.get("audit") or {}
        if not audit.get("hop_metric_applicable"):
            continue
        for hop in audit.get("hop_results") or []:
            total += 1
            hit = bool(hop.get("matched"))
            matched += 1 if hit else 0
            bucket = per_question.setdefault(
                str(row["question_id"]), {"matched": 0, "total": 0}
            )
            bucket["total"] += 1
            bucket["matched"] += 1 if hit else 0
    return {
        "matched": matched,
        "total": total,
        "ratio": round(matched / total, 3) if total else None,
        "per_question": dict(sorted(per_question.items())),
    }


def paired_rows(
    rows: list[dict[str, Any]], arms: list[str]
) -> tuple[list[dict[str, Any]], int]:
    """Rows where both arms completed the same question in the same round.

    Returns the pairs and the number of cells dropped because one arm failed.
    That dropped count is the honest denominator: the pairs are not a random
    sample of the questions, they are the questions the provider happened to let
    through twice.
    """
    first, second = arms
    cells: dict[tuple[str, str, int], dict[str, dict[str, Any]]] = {}
    for row in rows:
        key = (str(row["case_set"]), str(row["question_id"]), int(row["round"]))
        cells.setdefault(key, {})[str(row["arm"])] = row

    pairs: list[dict[str, Any]] = []
    dropped = 0
    for (case_set, question_id, round_index), values in sorted(cells.items()):
        left, right = values.get(first), values.get(second)
        if not left or not right:
            dropped += 1
            continue
        if left["status"] != COMPLETED or right["status"] != COMPLETED:
            dropped += 1
            continue
        left_audit = left.get("audit") or {}
        right_audit = right.get("audit") or {}
        pairs.append({
            "case_set": case_set,
            "question_id": question_id,
            "round": round_index,
            f"{first}_hop_recall": left_audit.get("hop_recall"),
            f"{second}_hop_recall": right_audit.get("hop_recall"),
            f"{first}_span_recall": left_audit.get("expected_answer_span_recall"),
            f"{second}_span_recall": right_audit.get("expected_answer_span_recall"),
        })
    return pairs, dropped


def summarise_pairs(pairs: list[dict[str, Any]], arms: list[str]) -> dict[str, Any]:
    first, second = arms
    comparable = [
        pair for pair in pairs
        if pair.get(f"{first}_hop_recall") is not None
        and pair.get(f"{second}_hop_recall") is not None
    ]
    diffs = [
        pair[f"{second}_hop_recall"] - pair[f"{first}_hop_recall"]
        for pair in comparable
    ]
    return {
        "definition": f"每对 = 同一题、同一轮里两臂都完成；差值 = {second} - {first}",
        "comparable_pairs": len(comparable),
        f"{first}_mean_hop_recall": (
            round(statistics.fmean(pair[f"{first}_hop_recall"] for pair in comparable), 3)
            if comparable else None
        ),
        f"{second}_mean_hop_recall": (
            round(statistics.fmean(pair[f"{second}_hop_recall"] for pair in comparable), 3)
            if comparable else None
        ),
        "mean_difference": round(statistics.fmean(diffs), 3) if diffs else None,
        f"{second}_better_pairs": sum(1 for diff in diffs if diff > 0),
        f"{first}_better_pairs": sum(1 for diff in diffs if diff < 0),
        "tied_pairs": sum(1 for diff in diffs if diff == 0),
        "pairs": pairs,
    }


def round_stability(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """How often a question's hop score changes between rounds.

    With one round per cell, every difference between arms is also a difference
    between two moments in time.  Knowing how much a single configuration moves
    on its own is what says whether an arm difference is even resolvable.
    """
    series: dict[tuple[str, str, str], list[float]] = {}
    for row in rows:
        audit = row.get("audit") or {}
        value = audit.get("hop_recall")
        if value is None:
            continue
        key = (str(row["case_set"]), str(row["question_id"]), str(row["arm"]))
        series.setdefault(key, []).append(float(value))
    unstable = {
        "|".join(key): values
        for key, values in series.items()
        if len(values) > 1 and len(set(values)) > 1
    }
    return {
        "cells": len(series),
        "cells_with_multiple_rounds": sum(1 for values in series.values() if len(values) > 1),
        "cells_that_changed": len(unstable),
        "changed": dict(sorted(unstable.items())),
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    sets = [parse_case_set(spec) for spec in args.case_set]
    arms = [parse_arm(spec) for spec in args.arm]
    if len(arms) != 2:
        raise SystemExit("需要恰好两个臂才能做配对比较")
    names = [arm["name"] for arm in arms]
    if len(set(names)) != 2:
        raise SystemExit(f"臂名必须互不相同：{names}")

    loaded: dict[str, dict[str, Any]] = {}
    for case_set in sets:
        snapshot = Path(case_set["snapshot"])
        evaluation = Path(case_set["evaluation"])
        for path in (snapshot, evaluation):
            if not path.exists():
                raise SystemExit(f"文件不存在：{path}")
        cases = load_cases(snapshot)
        specs = load_evaluation(evaluation)
        question_ids = [qid for qid in specs if qid in cases]
        if not question_ids:
            raise SystemExit(f"题集 {case_set['name']} 的快照与契约没有交集")
        loaded[case_set["name"]] = {
            "cases": cases,
            "specs": specs,
            "question_ids": question_ids,
            "snapshot": snapshot,
            "evaluation": evaluation,
            "unmatched": sorted(qid for qid in specs if qid not in cases),
        }

    rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    rng = random.Random(args.seed)
    for round_index in range(args.rounds):
        # The agenda is shuffled, so neither question set sits in a fixed slot
        # of the round: a provider slow phase would otherwise always land on the
        # same set, which is the confound this whole script exists to remove.
        agenda = [
            (name, question_id)
            for name in loaded
            for question_id in loaded[name]["question_ids"]
        ]
        rng.shuffle(agenda)
        for case_set_name, question_id in agenda:
            entry = loaded[case_set_name]
            ordered = list(arms)
            if rng.random() < 0.5:
                ordered.reverse()
            for arm in ordered:
                row = await score_one(
                    case=entry["cases"][question_id],
                    spec=entry["specs"].get(question_id, {}),
                    arm=arm,
                    max_retries=int(args.max_retries),
                    dry_run=bool(args.dry_run),
                )
                row.update({
                    "case_set": case_set_name,
                    "round": round_index,
                    "arm": arm["name"],
                    "order": ordered.index(arm),
                })
                rows.append(row)
        print(
            f"round {round_index + 1}/{args.rounds} done "
            f"({len(rows)} calls, {time.perf_counter() - started:.0f}s)",
            file=sys.stderr,
            flush=True,
        )

    by_arm = {
        name: summarize_rows([row for row in rows if row["arm"] == name])
        for name in names
    }
    by_arm_and_set = {
        f"{name}@{case_set}": summarize_rows([
            row for row in rows
            if row["arm"] == name and row["case_set"] == case_set
        ])
        for name in names
        for case_set in loaded
    }
    hop_by_arm_and_set = {
        f"{name}@{case_set}": hop_counts([
            row for row in rows
            if row["arm"] == name and row["case_set"] == case_set
        ])
        for name in names
        for case_set in loaded
    }
    pairs, dropped = paired_rows(rows, names)

    return {
        "experiment": "generation-interleaved-rounds",
        "run_id": args.run_id,
        "started_at": args.started_at,
        "finished_at": datetime.now().astimezone().isoformat(),
        "model": model_descriptor("generator")["model"],
        "prompt_version": GENERATOR_PROMPT_VERSION,
        "audit_version": AUDIT_VERSION,
        "case_sets": [
            {
                "name": case_set["name"],
                "snapshot": str(loaded[case_set["name"]]["snapshot"]),
                "snapshot_sha256": file_sha256(loaded[case_set["name"]]["snapshot"]),
                "evaluation": str(loaded[case_set["name"]]["evaluation"]),
                "evaluation_sha256": file_sha256(loaded[case_set["name"]]["evaluation"]),
                "evaluation_version": load_evaluation_version(
                    loaded[case_set["name"]]["evaluation"]
                ),
                "question_ids": loaded[case_set["name"]]["question_ids"],
                "questions_missing_from_snapshot": loaded[case_set["name"]]["unmatched"],
            }
            for case_set in sets
        ],
        "arms": arms,
        "rounds": args.rounds,
        "order_seed": args.seed,
        "llm_timeout_seconds": int(settings.llm_timeout_seconds),
        "generation_max_retries": int(args.max_retries),
        "dry_run": bool(args.dry_run),
        "retrieval_calls": 0,
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "calls": len(rows),
        "summary": {
            "by_arm": by_arm,
            "by_arm_and_case_set": by_arm_and_set,
            "hop_counts_by_arm_and_case_set": hop_by_arm_and_set,
            "paired": summarise_pairs(pairs, names),
            "paired_cells_dropped": dropped,
            "round_stability": round_stability(rows),
        },
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="交替执行多跳实验的两臂（可跨题集），抵消 provider 漂移"
    )
    parser.add_argument(
        "--case-set",
        action="append",
        required=True,
        help="name=<snapshot>,snapshot=<路径>,evaluation=<路径>；可给多个",
    )
    parser.add_argument(
        "--arm",
        action="append",
        required=True,
        help="name=<max_items>:<max_chars>（max_chars 可为 none）；需给两个",
    )
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument(
        "--seed",
        type=int,
        default=20260922,
        help="臂顺序与题序随机化的种子；固定以便复现",
    )
    parser.add_argument("--max-retries", type=int, default=GENERATOR_MAX_RETRIES)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只装配证据并走完调度与汇总，不调用模型（用于验证管线）",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.output.exists():
        raise SystemExit(f"拒绝覆盖已有结果：{args.output}。请指定新的 --output。")
    if args.rounds < 1:
        raise SystemExit("--rounds 至少为 1")

    args.run_id = make_run_id()
    args.started_at = datetime.now().astimezone().isoformat()
    result = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(result, ensure_ascii=False, indent=2))
    except FileExistsError:
        raise SystemExit(f"拒绝覆盖已有结果：{args.output}。请重新运行以生成新的 run id。")

    print(json.dumps({
        "output": str(args.output),
        "calls": result["calls"],
        "retrieval_calls": 0,
        "hop_counts": result["summary"]["hop_counts_by_arm_and_case_set"],
        "paired": {
            key: value
            for key, value in result["summary"]["paired"].items()
            if key != "pairs"
        },
        "paired_cells_dropped": result["summary"]["paired_cells_dropped"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
