"""Paired comparison statistics, computed on **independent units**.

Why this module exists
----------------------
The paired analysis used to live inside ``scripts/interleaved_generation_rounds.py``,
which imports the model SDK at module level.  That made this arithmetic
untestable in an offline environment -- and untested arithmetic is exactly where
the defect below survived unnoticed.

The defect
----------
``paired_rows`` keys a pair by ``(case_set, question_id, round)``, so one
question contributes one pair **per round**.  Those pairs are not independent:
they share the question, the evidence pack, the model and the prompt.  Taking
``stdev(diffs) / sqrt(n_pairs)`` over them understates the standard error.

Measured on this project's data (``data/runs/mh_gate_probe_v2.json``,
2 arms x 11 questions x 3 rounds): 32 pairs from 11 units, SE understated by
1.41x (0.0714 vs 0.1006), MDE understated from 0.197 to 0.140.

So the statistics here collapse each question to a single mean difference first,
and the pair-level numbers are reported *alongside* rather than *instead* --
the win/loss counts and the dropped-cell denominator are statements about the
run, not about the units.

Dependency boundary
-------------------
Nothing here may import the model SDK, a vector store or the network.
``tests/test_eval_dependency_surface.py`` asserts that, because one convenient
import at the top would stop a recorded run from being re-analysable without a
model installed.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

COMPLETED = "completed"

# 配对统计的口径版本 —— 与 AUDIT_VERSION 是两件事，不能共用一个号。
#
#   AUDIT_VERSION              描述「一条答案怎么被判分」（src/frozen_evidence.py）
#   PAIRED_STATISTICS_VERSION  描述「判分结果怎么被聚合成统计量」（本模块）
#
# 两者都会让新旧运行不可直接比较，但原因不同：前者是判分规则变了，后者是聚合
# 口径变了。混在一个号里，事后就分不出究竟是哪一个变了。
PAIRED_STATISTICS_VERSION = "paired-unit.v1"


def paired_rows(
    rows: list[dict[str, Any]], arms: list[str]
) -> tuple[list[dict[str, Any]], int]:
    """Rows where both arms completed the same question in the same round.

    Returns the pairs and the number of cells dropped because one arm failed.
    That dropped count is the honest denominator: the pairs are not a random
    sample of the questions, they are the questions the provider happened to let
    through twice.

    ⚠️ The key includes ``round``, so one question yields one pair **per round**.
    Those pairs are not independent observations -- see ``summarise_pairs`` for
    how the statistics account for that.
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
    """Paired summary, with the statistics computed on **independent units**.

    ``n`` in every statistic is the number of **units** (questions), not the
    number of pairs (question x round).  When the two differ, both are reported,
    because "32 pairs" and "11 units" answer different questions -- the first
    says how much of the run was comparable, the second how much independent
    evidence there is.
    """
    first, second = arms
    comparable = [
        pair for pair in pairs
        if pair.get(f"{first}_hop_recall") is not None
        and pair.get(f"{second}_hop_recall") is not None
    ]

    # 独立单元是「题」，不是「题×轮」：同一题的多轮先合并成一个观测。
    by_unit: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for pair in comparable:
        by_unit.setdefault(
            (str(pair["case_set"]), str(pair["question_id"])), []
        ).append(pair)

    def unit_mean(arm: str) -> float | None:
        if not by_unit:
            return None
        return statistics.fmean(
            statistics.fmean(pair[f"{arm}_hop_recall"] for pair in group)
            for group in by_unit.values()
        )

    first_mean = unit_mean(first)
    second_mean = unit_mean(second)
    unit_diffs = [
        statistics.fmean(pair[f"{second}_hop_recall"] for pair in group)
        - statistics.fmean(pair[f"{first}_hop_recall"] for pair in group)
        for group in by_unit.values()
    ]
    pair_diffs = [
        pair[f"{second}_hop_recall"] - pair[f"{first}_hop_recall"]
        for pair in comparable
    ]

    n_pairs = len(pair_diffs)
    n_units = len(unit_diffs)
    mean = statistics.fmean(unit_diffs) if unit_diffs else None
    stdev = statistics.stdev(unit_diffs) if n_units > 1 else None
    standard_error = stdev / math.sqrt(n_units) if stdev is not None else None

    return {
        "statistics_version": PAIRED_STATISTICS_VERSION,
        "definition": (
            f"每对 = 同一题、同一轮里两臂都完成；差值 = {second} - {first}。"
            "统计量在单元层计算：同一题的多轮先取均值，一个单元 = (题集, 题号)。"
        ),
        # 观测层（题×轮）：这次运行覆盖了多少个格子。
        "comparable_pairs": n_pairs,
        # 单元层（题）：**统计量用这个 n**，它才是独立观测数。
        "independent_units": n_units,
        f"{first}_mean_hop_recall": (
            round(first_mean, 3) if first_mean is not None else None
        ),
        f"{second}_mean_hop_recall": (
            round(second_mean, 3) if second_mean is not None else None
        ),
        "mean_difference": round(mean, 3) if mean is not None else None,
        # 配对层的点估计也保留：两个口径在平衡设计下仍会略有差异
        # （单元层每题等权，配对层每格等权），并排给出才看得出权重的影响。
        "mean_difference_by_pair": (
            round(statistics.fmean(pair_diffs), 3) if pair_diffs else None
        ),
        "stdev": round(stdev, 4) if stdev is not None else None,
        "standard_error": round(standard_error, 4) if standard_error is not None else None,
        "ci95": (
            (
                round(mean - 1.96 * standard_error, 4),
                round(mean + 1.96 * standard_error, 4),
            )
            if mean is not None and standard_error is not None
            else None
        ),
        "mde": round(1.96 * standard_error, 4) if standard_error is not None else None,
        f"{second}_better_pairs": sum(1 for diff in pair_diffs if diff > 0),
        f"{first}_better_pairs": sum(1 for diff in pair_diffs if diff < 0),
        "tied_pairs": sum(1 for diff in pair_diffs if diff == 0),
        "pairs": pairs,
    }
