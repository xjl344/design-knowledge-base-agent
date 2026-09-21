"""Group gates, flaky detection and small-sample verdicts for generation replay.

Three problems this solves
--------------------------
1. **Group gates.**  A single pass/fail number cannot say whether a change did
   what it claimed.  Metrics are declared in groups (retrieval / usability /
   quality / safety / citation); an experiment states which groups it intends
   to move, and drift in any other group is a regression even if the headline
   number improved.

2. **Flaky detection.**  When a question changes verdict between runs with no
   change to the system, the *metric* is unstable.  Averaging it produces a
   number that looks precise and is not.  Such questions are moved out of the
   regression denominator and reported separately.

3. **Small-sample verdicts.**  With 12-20 questions, an ordinary mean will
   happily report noise as improvement.  The convention used here follows
   ``spark-llm-eval``: below 20 paired samples use a non-parametric test, and
   binary metrics use an exact test when discordant pairs are few.
"""

from __future__ import annotations

from itertools import combinations
from math import comb
from typing import Any

# Below this many paired samples, assume nothing about the distribution.
SMALL_SAMPLE_THRESHOLD = 20
# Two runs of the same system differing by more than this on one metric are
# treated as incomparable rather than as a trend worth reporting.
RUN_TO_RUN_TOLERANCE = 0.10
# A question whose verdict flips in this share of run pairs is flagged flaky.
FLAKY_FLIP_SHARE = 0.0  # any flip at all is a signal with this few runs


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


# ---------------------------------------------------------------------------
# Small-sample tests
# ---------------------------------------------------------------------------
def wilcoxon_signed_rank(a: list[float], b: list[float]) -> dict[str, Any]:
    """Non-parametric paired test, appropriate when n < 20.

    Implemented without SciPy so the evaluator keeps its dependency surface
    small and stays runnable offline.  Ties are dropped, which is the standard
    conservative handling and avoids inflating significance.
    """
    if len(a) != len(b) or not a:
        raise ValueError("wilcoxon 需要等长且非空的配对样本")
    diffs = [x - y for x, y in zip(a, b) if x != y]
    n = len(diffs)
    if n == 0:
        return {"test": "wilcoxon", "n_pairs": 0, "statistic": 0.0,
                "p_value": 1.0, "significant": False,
                "note": "所有配对完全一致"}
    ranks = _average_ranks([abs(d) for d in diffs])
    w_plus = sum(rank for rank, d in zip(ranks, diffs) if d > 0)
    w_minus = sum(rank for rank, d in zip(ranks, diffs) if d < 0)
    statistic = min(w_plus, w_minus)
    p_value = _wilcoxon_exact_p(n, statistic)
    return {
        "test": "wilcoxon",
        "n_pairs": n,
        "statistic": round(statistic, 3),
        "p_value": round(p_value, 4),
        "significant": p_value < 0.05,
        "small_sample": n < SMALL_SAMPLE_THRESHOLD,
    }


def _average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index
        while end + 1 < len(order) and values[order[end + 1]] == values[order[index]]:
            end += 1
        average = (index + end) / 2 + 1
        for position in range(index, end + 1):
            ranks[order[position]] = average
        index = end + 1
    return ranks


def _wilcoxon_exact_p(n: int, statistic: float) -> float:
    """Exact two-sided p-value by enumerating sign assignments.

    Feasible up to about n=20 (2^20 assignments); beyond that a normal
    approximation is used, which is where the approximation is already good.
    """
    if n > 20:
        total = n * (n + 1) / 2
        mean_w = total / 2
        sd = (n * (n + 1) * (2 * n + 1) / 24) ** 0.5
        if sd == 0:
            return 1.0
        z = (statistic - mean_w) / sd
        return min(1.0, 2 * _normal_cdf(z))
    total = n * (n + 1) // 2
    # Count sign assignments whose W+ is at most the observed statistic.
    counts = [0] * (total + 1)
    counts[0] = 1
    for rank in range(1, n + 1):
        for value in range(total, rank - 1, -1):
            counts[value] += counts[value - rank]
    extreme = sum(counts[: int(statistic) + 1])
    return min(1.0, 2 * extreme / (2 ** n))


def _normal_cdf(z: float) -> float:
    # Abramowitz & Stegun 7.1.26; accurate to ~1e-7, enough for a gate.
    sign = -1.0 if z < 0 else 1.0
    x = abs(z) / (2 ** 0.5)
    t = 1.0 / (1.0 + 0.3275911 * x)
    y = 1.0 - ((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
               - 0.284496736) * t * t
    erf = sign * (1 - y * _exp(-x * x))
    return 0.5 * (1 + erf)


def _exp(value: float) -> float:
    # math.exp is available; wrapped so the helper reads as a single formula.
    import math

    return math.exp(value)


def mcnemar_exact(correct_a: list[bool], correct_b: list[bool]) -> dict[str, Any]:
    """Exact McNemar test for paired binary outcomes.

    With few discordant pairs the chi-square approximation is invalid, so the
    exact binomial form is used throughout -- this is the case that matters at
    12 questions, where discordant pairs are usually in single digits.
    """
    if len(correct_a) != len(correct_b):
        raise ValueError("mcnemar 需要等长的配对样本")
    b = sum(1 for x, y in zip(correct_a, correct_b) if x and not y)
    c = sum(1 for x, y in zip(correct_a, correct_b) if y and not x)
    discordant = b + c
    if discordant == 0:
        return {"test": "mcnemar_exact", "discordant": 0, "p_value": 1.0,
                "significant": False, "note": "无不一致配对"}
    if discordant > 1000:
        chi = (abs(b - c) - 1) ** 2 / discordant
        p_value = _chi2_sf_1df(chi)
        method = "chi2_approx"
    else:
        # Exact two-sided binomial with p=0.5.
        tail = sum(comb(discordant, k) for k in range(0, min(b, c) + 1)) / (2 ** discordant)
        p_value = min(1.0, 2 * tail)
        method = "exact"
    return {
        "test": "mcnemar_exact",
        "method": method,
        "b_only_first": b,
        "c_only_second": c,
        "discordant": discordant,
        "p_value": round(p_value, 4),
        "significant": p_value < 0.05,
        "small_sample": discordant < 10,
    }


def _chi2_sf_1df(chi: float) -> float:
    # Survival function of chi-square with 1 df == erfc(sqrt(chi/2)).
    import math

    return math.erfc((chi / 2) ** 0.5)


def choose_test(metric_kind: str, n_pairs: int) -> str:
    """Pick the paired test the sample size can actually support."""
    if metric_kind == "binary":
        return "mcnemar_exact"
    if n_pairs < SMALL_SAMPLE_THRESHOLD:
        return "wilcoxon"  # non-parametric: no normality assumption to make
    return "wilcoxon"  # still preferred; switch to t-test only with evidence


# ---------------------------------------------------------------------------
# Flaky detection
# ---------------------------------------------------------------------------
def detect_flaky_questions(
    per_question_verdicts: dict[str, list[bool | None]],
    *,
    metric: str,
) -> dict[str, Any]:
    """Find questions whose verdict changes across runs of the same system.

    A verdict that flips without the system changing is evidence about the
    metric, not about the model.  Such questions are removed from the
    regression denominator and reported on their own.
    """
    flagged: list[dict[str, Any]] = []
    for question_id, verdicts in sorted(per_question_verdicts.items()):
        observed = [v for v in verdicts if v is not None]
        if len(observed) < 2:
            continue
        flips = sum(
            1 for a, b in combinations(observed, 2) if a != b
        )
        pairs = comb(len(observed), 2)
        share = flips / pairs if pairs else 0.0
        if share > FLAKY_FLIP_SHARE:
            flagged.append({
                "question_id": question_id,
                "verdicts": observed,
                "flip_pairs": flips,
                "total_pairs": pairs,
                "flip_share": round(share, 3),
            })
    return {
        "metric": metric,
        "flaky_question_ids": [item["question_id"] for item in flagged],
        "details": flagged,
        "note": (
            "同一系统多次运行中判定翻转的题。这些题不进入回归判定的分母，"
            "否则一个不稳定的指标会被当成改进或退步。"
        ),
    }


# ---------------------------------------------------------------------------
# Run-to-run comparability
# ---------------------------------------------------------------------------
def check_run_comparability(
    run_metrics: list[dict[str, float | None]],
    *,
    keys: list[str],
    tolerance: float = RUN_TO_RUN_TOLERANCE,
) -> dict[str, Any]:
    """Refuse to report a trend when the runs disagree beyond tolerance.

    A difference between two runs of the same configuration is measurement
    noise, not a result.  Comparing them anyway is how a service hiccup gets
    reported as a prompt improvement.
    """
    unstable: list[dict[str, Any]] = []
    for key in keys:
        values = [run.get(key) for run in run_metrics if run.get(key) is not None]
        if len(values) < 2:
            continue
        spread = max(values) - min(values)
        if spread > tolerance:
            unstable.append({
                "metric": key,
                "values": [round(float(v), 3) for v in values],
                "spread": round(spread, 3),
                "tolerance": tolerance,
            })
    return {
        "comparable": not unstable,
        "tolerance": tolerance,
        "unstable_metrics": unstable,
        "verdict": (
            "同配置多次运行的差异在容差内，可作为对照"
            if not unstable
            else "同配置多次运行差异超出容差，不应对这些指标出趋势结论"
        ),
    }


# ---------------------------------------------------------------------------
# Group gates
# ---------------------------------------------------------------------------
def evaluate_group_gates(
    group_definitions: dict[str, Any],
    metric_values: dict[str, Any],
    *,
    declared_changes: list[str],
) -> dict[str, Any]:
    """Decide pass/fail per metric group given what the change claimed to move.

    A group not named in ``declared_changes`` is expected to hold still, so any
    movement there fails regardless of direction.  A declared group is allowed
    to move, but must stay within its tolerance.
    """
    results: dict[str, Any] = {}
    overall = True
    for group_name, definition in group_definitions.items():
        metrics = definition.get("metrics") or []
        allowed = float(definition.get("allowed_change", 0.0))
        declared = group_name in declared_changes
        details = []
        group_ok = True

        for metric in metrics:
            entry = metric_values.get(metric)
            if entry is None:
                continue
            delta = entry.get("delta")
            direction = definition.get("direction")
            if delta is None:
                details.append({"metric": metric, "delta": None,
                                "verdict": "no_baseline"})
                continue
            # Two independent questions, and both must hold:
            #   1. scope  -- was this group allowed to move at all?
            #   2. size   -- if it was, did it stay within tolerance?
            # They used to be collapsed into one branch, which let a
            # ``lower_is_better`` group improve freely even when the experiment
            # never declared it -- an undeclared improvement is still evidence
            # the change was not scoped, so it must fail too.
            #
            # ``allowed_change`` is a band, not a ceiling.  Treating it as a
            # ceiling let a declared group fall arbitrarily far and still pass
            # ("quality may fluctuate by 0.05" was read as "quality may drop by
            # 0.17"), which is the failure the gate exists to catch.
            if direction == "lower_is_better":
                # Only an increase is a regression; improving past the band is
                # fine when the group was declared, and any increase is out of
                # scope when it was not.
                within_tolerance = delta <= allowed
            else:
                within_tolerance = abs(delta) <= allowed if declared else delta == 0
            in_scope = declared or delta == 0
            breach = not (within_tolerance and in_scope)
            if breach:
                group_ok = False
            details.append({
                "metric": metric,
                "baseline": entry.get("baseline"),
                "current": entry.get("current"),
                "delta": round(float(delta), 4),
                "allowed_change": allowed,
                "declared": declared,
                "direction": direction,
                "verdict": "breach" if breach else "ok",
                "breach_reason": (
                    "out_of_scope" if not in_scope
                    else "beyond_tolerance" if not within_tolerance
                    else None
                ),
            })

        results[group_name] = {
            "declared": declared,
            "metrics": details,
            "passed": group_ok,
        }
        overall = overall and group_ok

    return {
        "declared_changes": list(declared_changes),
        "groups": results,
        "all_groups_passed": overall,
        "note": (
            "未声明的指标组必须完全不动；已声明的组允许在容差内变动。"
            "这样一次提示词改动若动了检索或可用性指标，会直接失败而不是被平均掉。"
        ),
    }
