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

# Pre-registered targets.
#
# ⚠️ Corrected before the A/B run was read, and the reason matters.
#
# The first version of this list was derived from `data/span_audit.v1.json` --
# written before the r17 h2 span was demoted.  Re-checking against the *current*
# contract (before any A/B output existed; the log was empty and the output file
# absent) showed it was wrong in both directions:
#
#   r17_cylinder_capacity  h2   no longer fails at all (the demotion fixed it)
#   r18_height_adjustment  h1   fails 1 of 3  -- and was not in the list
#   r18_height_adjustment  h2   fails 1 of 3  -- and was not in the list
#
# Leaving r17 in would have diluted the target set with a question that cannot
# move; leaving r18 out would have hidden a question that can.  Correcting a
# pre-registration because it was derived from a stale input is legitimate;
# correcting it *after* seeing the result would not be.
#
# The four hops these questions carry, all of which name the category and stop:
#
#   r03 h2  `坐姿膝高`   never written, although the evidence lists it
#   r04 h2  `680~760`    never written, although the answer says `桌面高`
#   r18 h1  `再用容量公式反推有效高度`  back-derivation named but not performed
#   r18 h2  `检查高度是否导致重心过高`  the check named but not stated
TARGET_QUESTIONS = (
    "r03_office_chair_constraints",
    "r04_child_chair_flow",
    "r18_height_adjustment",
)

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
    other_keys = [key for key in cells if key[1] not in TARGET_QUESTIONS]

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
        "target_questions": pair_stats(target_keys),
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
