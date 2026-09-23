"""Paired analysis of a prompt A/B run produced by the interleaved harness.

Why paired, and why the pre-registered targets matter
-----------------------------------------------------
The same configuration moves across rounds by more than a prompt tweak is likely
to produce, so a before/after comparison of aggregate numbers would report the
provider's mood as the prompt's effect.  Two things make this interpretable:

* both prompts run **inside one time window**, so a slow period hits both;
* the hypothesis is **pre-registered** -- `TARGET_QUESTIONS` is fixed here, before
  the run, and the script reports those cells separately from everything else.

Reporting the targets separately is what stops a null result from being salvaged
by hunting through the other questions for something that moved.

Usage::

    python scripts/analyze_prompt_ab.py data/runs/prompt_ab_3r.json
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

from src.frozen_evidence import build_evidence_pack, load_cases, soft_audit  # noqa: E402

# Pre-registered targets, and a negative control.
#
# ⚠️ Revised twice before the A/B was read, and both reasons matter.
#
# Revision 1 -- the list was derived from `data/span_audit.v1.json`, written
# before the r17 h2 span was demoted.  Re-checking against the current contract
# showed r17 h2 no longer fails at all, while r18 h1/h2 do.
#
# Revision 2 -- then I checked *why* each hop fails instead of trusting the
# audit's label, and found r18 does not match the mechanism rule 8 targets:
#
#   r03 h2  wants `坐姿膝高`, answer says `坐姿`            -> category, not item
#   r04 h2  wants `680~760`,   answer says `桌面高`         -> category, not value
#   r18 h1  wants `反推`,      answer says `反算`           -> SYNONYM
#   r18 h2  wants `过高`,      answer says `过大`           -> SYNONYM
#
# Rule 8 says "write the specific item or value, not its category".  It says
# nothing about word choice, so it cannot fix r18 -- and r18's substance *was*
# delivered both times.  Those two are term-level false negatives: demoting a
# span to terms moved the wording problem from the span onto the terms, where it
# still sits.
#
# So r18 stays in the analysis as a **negative control**: rule 8 must not move
# it.  If it does move, that is either spillover or a broken analysis, and either
# way it needs explaining before any target result is believed.
#
# Both revisions happened before any A/B output existed (the output file was
# absent and the log empty), and both *narrowed* the target rather than widening
# it -- which is the direction that makes the test harder, not easier.
TARGET_QUESTIONS = (
    "r03_office_chair_constraints",
    "r04_child_chair_flow",
)

# Expected NOT to move.  A target set with no negative control cannot tell
# "the prompt did nothing" from "the analysis finds differences everywhere".
CONTROL_QUESTIONS = ("r18_height_adjustment",)

CASE_SETS = {
    "real_complete": (
        ROOT / "data" / "frozen_multihop_real_complete.v2.jsonl",
        ROOT / "data" / "generation_eval.multihop.real.complete.v2.json",
    ),
    "synthetic": (
        ROOT / "data" / "frozen_multihop.v2.jsonl",
        ROOT / "data" / "generation_eval.multihop.v2.json",
    ),
}


def hop_recall(row: dict[str, Any], cache: dict[str, Any]) -> float | None:
    """Re-score one row against the contract, and return its hop recall."""
    case_set = row["case_set"]
    question_id = row["question_id"]
    if case_set not in CASE_SETS:
        return None
    if case_set not in cache:
        snapshot, evaluation = CASE_SETS[case_set]
        cache[case_set] = (
            load_cases(snapshot),
            json.loads(evaluation.read_text(encoding="utf-8"))["cases"],
        )
    cases, specs = cache[case_set]
    case = cases.get(question_id)
    spec = next((item for item in specs if str(item["id"]) == question_id), None)
    if case is None or spec is None:
        return None
    # The pack must be rebuilt from the *arm's* configuration.  Using the row's
    # `evidence_count` instead would rebuild a pack the row was not scored
    # against, and the audit would then be reading different evidence.
    max_items, max_chars = cache["arms"].get(str(row.get("arm")), (None, None))
    pack = build_evidence_pack(
        case, max_items=max_items, max_chars_per_item=max_chars
    )
    audit = soft_audit(
        row.get("answer") or "",
        pack,
        expected_answer_spans=spec.get("expected_answer_spans", []),
        expected_sources=spec.get("expected_sources", []),
        required_terms=spec.get("required_terms", []),
        refusal_requirements=spec.get("refusal_requirements", []),
        ambiguity_requirements=spec.get("ambiguity_requirements", []),
        required_hops=spec.get("required_hops", []),
    )
    hops = audit.get("hop_results") or []
    if not hops:
        return None
    return sum(1 for hop in hops if hop.get("matched")) / len(hops)


def analyse(payload: dict[str, Any]) -> dict[str, Any]:
    cache: dict[str, Any] = {
        "arms": {
            str(arm["name"]): (arm.get("max_items"), arm.get("max_chars_per_item"))
            for arm in payload.get("arms") or []
        }
    }
    # cell -> prompt -> [scores across rounds]
    cells: dict[tuple[str, str], dict[str, list[float]]] = {}
    for row in payload.get("rows", []):
        if row.get("status") != "completed":
            continue
        score = hop_recall(row, cache)
        if score is None:
            continue
        key = (str(row["case_set"]), str(row["question_id"]))
        cells.setdefault(key, {}).setdefault(str(row["prompt_version"]), []).append(score)

    versions = sorted(payload.get("prompt_versions") or [])
    if len(versions) != 2:
        raise SystemExit(f"需要两个提示词版本才能配对，收到 {versions}")

    def pair_stats(keys: list[tuple[str, str]]) -> dict[str, Any]:
        diffs: list[float] = []
        for key in keys:
            per_prompt = cells.get(key, {})
            if not all(version in per_prompt for version in versions):
                continue
            means = [statistics.mean(per_prompt[version]) for version in versions]
            diffs.append(means[1] - means[0])
        if not diffs:
            return {"cells": 0}
        mean = statistics.mean(diffs)
        stdev = statistics.stdev(diffs) if len(diffs) > 1 else 0.0
        se = stdev / math.sqrt(len(diffs)) if diffs else 0.0
        return {
            "cells": len(diffs),
            "mean_difference": round(mean, 4),
            "median_difference": round(statistics.median(diffs), 4),
            "stdev": round(stdev, 4),
            "standard_error": round(se, 4),
            "ci95": (
                round(mean - 1.96 * se, 4),
                round(mean + 1.96 * se, 4),
            ),
            "new_better": sum(1 for value in diffs if value > 0),
            "old_better": sum(1 for value in diffs if value < 0),
            "tied": sum(1 for value in diffs if value == 0),
        }

    target_keys = [key for key in cells if key[1] in TARGET_QUESTIONS]
    control_keys = [key for key in cells if key[1] in CONTROL_QUESTIONS]
    other_keys = [
        key
        for key in cells
        if key[1] not in TARGET_QUESTIONS and key[1] not in CONTROL_QUESTIONS
    ]

    by_question = {}
    for key in sorted(cells):
        per_prompt = cells[key]
        by_question[f"{key[0]}|{key[1]}"] = {
            version: round(statistics.mean(values), 3)
            for version, values in sorted(per_prompt.items())
        }

    return {
        "prompt_versions": versions,
        "old": versions[0],
        "new": versions[1],
        "pre_registered_targets": list(TARGET_QUESTIONS),
        "negative_controls": list(CONTROL_QUESTIONS),
        "target_questions": pair_stats(target_keys),
        # Expected to be unchanged.  A non-zero result here means the effect is
        # not the one rule 8 describes, and must be explained before the target
        # result is believed.
        "control_questions": pair_stats(control_keys),
        "all_other_questions": pair_stats(other_keys),
        "every_question": pair_stats(list(cells)),
        "by_question": by_question,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path)
    args = parser.parse_args(argv)

    payload = json.loads(args.run.read_text(encoding="utf-8"))
    result = analyse(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
