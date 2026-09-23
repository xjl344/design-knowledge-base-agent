"""Apply the interleaved run's paired analysis to any set of labelled runs.

Why this exists
---------------
`interleaved_generation_rounds.py` runs the arms interleaved and analyses them in
process.  The replay harness produces runs too -- and those runs are *blocks*,
which is why the interleaved script exists.  But the question asked afterwards is
identical in both cases: do the arms differ, on which questions, and how far does
a single arm move on its own?

Rather than write that analysis twice -- and have the two copies drift, which is
how a project ends up with two definitions of "the arms differ" -- this script
imports the analysis from the interleaved module and only does the one thing the
interleaved module cannot: attach `case_set` / `arm` / `round` labels to rows
that were written by a harness that had no idea it was part of a comparison.

Two rules are inherited and must not be relaxed here:

* **A timeout is not an answer.**  Failed rows are counted as availability and
  excluded from every quality mean.  A missing row and a wrong row are different
  findings.
* **The paired set is not a sample of the questions.**  It is the questions the
  provider happened to let through in *both* arms, so the dropped-cell count is
  reported next to every paired statistic rather than being hidden.

Usage::

    python scripts/analyze_arm_runs.py \
        --run case_set=real.noTrunc,arm=evidence5,round=1,path=data/runs/mhreal_g1_notrunc.json \
        --run case_set=real.noTrunc,arm=evidenceall,round=1,path=data/runs/mhreal_g3_notrunc.json \
        --pair evidence5,evidenceall
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval_generation_replay import summarize_rows  # noqa: E402

# The interleaved module lives beside this one, not at the repo root.  Without
# this the import only worked when the file was *run* (Python puts the script's
# directory on the path) and failed the moment anything imported it as a
# module -- which is exactly why this file had no tests for so long: it could
# not be collected.
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from interleaved_generation_rounds import (  # noqa: E402
    COMPLETED,
    hop_counts,
    paired_rows,
    round_stability,
    summarise_pairs,
)

# `summarize_rows` names its keys `<metric>_mean` / `<metric>_rate`; guessing
# `mean_<metric>` silently produced a table of `None`, which is worse than an
# error because it looks like a measurement.
QUALITY_KEYS = (
    "generation_success_rate",
    "provider_timeout_rate",
    "provider_error_rate",
    "answer_span_recall_mean",
    "required_term_recall_mean",
    "numeric_citation_coverage_mean",
    "citation_validity_rate",
    "refusal_correctness_rate",
    "ambiguity_safety_rate",
    "completed",
    "total",
    "truncation_lost_fact_count",
    "malformed_output_count",
    "retried_success_count",
    "first_try_success_count",
)


def parse_run(spec: str) -> dict[str, str]:
    """``case_set=...,arm=...,round=...,path=...`` -- commas, not colons.

    Windows drive letters make a colon-separated spec ambiguous (``C:\\...``),
    so the separator is a comma and each field is ``key=value``.
    """
    fields: dict[str, str] = {}
    for piece in spec.split(","):
        key, separator, value = piece.partition("=")
        if not separator:
            raise SystemExit(f"run 字段必须是 key=value，收到 {piece!r}")
        fields[key.strip()] = value.strip()
    missing = {"case_set", "arm", "round", "path"} - set(fields)
    if missing:
        raise SystemExit(f"run 缺少字段：{sorted(missing)}")
    return fields


def parse_pair(spec: str) -> tuple[str, str]:
    first, separator, second = spec.partition(",")
    if not separator or not first.strip() or not second.strip():
        raise SystemExit(f"--pair 必须是 第一臂,第二臂，收到 {spec!r}")
    return first.strip(), second.strip()


def load_labelled(spec: dict[str, str]) -> list[dict[str, Any]]:
    path = Path(spec["path"])
    if not path.exists():
        raise SystemExit(f"运行文件不存在：{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") or []
    if not rows:
        raise SystemExit(f"运行文件没有 rows：{path}")
    labelled = []
    for row in rows:
        copy = dict(row)
        copy["case_set"] = spec["case_set"]
        copy["arm"] = spec["arm"]
        copy["round"] = int(spec["round"])
        # `summarize_rows` and the pairing both key on `status`; a row written by
        # the replay harness carries it, but an aborted one may not.
        copy.setdefault("status", "completed" if copy.get("generation_success") else "failed")
        labelled.append(copy)
    return labelled


def _latency(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Mean/p50/max over *completed* rows only.

    A timeout's latency is the timeout, not the model's speed; folding it in
    would make an arm that timed out look like an arm that answered slowly.
    """
    values = sorted(
        float(row["generation_latency_seconds"])
        for row in rows
        if row.get("status") == COMPLETED
        and row.get("generation_latency_seconds") is not None
    )
    if not values:
        return {"n": 0, "mean": None, "p50": None, "max": None}
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 1),
        "p50": round(statistics.median(values), 1),
        "max": round(values[-1], 1),
    }


def _quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The quality keys, the availability breakdown, and latency.

    The summary carries the whole row list under `rows`; printing it here would
    bury the numbers this script exists to produce.
    """
    summary = summarize_rows(rows)
    out = {key: summary.get(key) for key in QUALITY_KEYS if key in summary}
    out["latency"] = _latency(rows)
    # The exact availability statement, not a rate derived from it: which way a
    # question failed (timeout vs provider error) is a different finding.
    out["status_counts"] = dict(sorted((summary.get("status_counts") or {}).items()))
    return out


def analyse(
    runs: list[dict[str, str]], pair: tuple[str, str]
) -> dict[str, Any]:
    first, second = pair
    by_run: dict[str, Any] = {}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    all_rows: list[dict[str, Any]] = []

    for spec in runs:
        rows = load_labelled(spec)
        all_rows.extend(rows)
        label = f"{spec['case_set']}|{spec['arm']}|r{spec['round']}"
        by_run[label] = {
            "path": spec["path"],
            "calls": len(rows),
            "quality": _quality(rows),
            "hops": hop_counts(rows),
        }
        grouped.setdefault((spec["case_set"], spec["arm"]), []).extend(rows)

    by_cell: dict[str, Any] = {}
    for (case_set, arm), rows in sorted(grouped.items()):
        by_cell[f"{case_set}|{arm}"] = {
            "rounds": sorted({int(row["round"]) for row in rows}),
            "calls": len(rows),
            "quality": _quality(rows),
            "hops": hop_counts(rows),
            # Pooling hop counts across runs that scored *different* questions
            # moves the denominator with availability -- the same defect as a
            # metric whose denominator is the evidence count.  The pooled cell
            # is reported, but the per-run numbers are the ones to read.
            "pooling_warning": (
                "合并轮的 hops 分母随完成题集变化，跨轮不可比；请读每次运行"
            ),
        }

    paired_by_case_set: dict[str, Any] = {}
    for case_set in sorted({spec["case_set"] for spec in runs}):
        rows = [row for row in all_rows if row["case_set"] == case_set]
        pairs, dropped = paired_rows(rows, [first, second])
        entry = summarise_pairs(pairs, [first, second])
        entry["dropped_cells"] = dropped
        paired_by_case_set[case_set] = entry

    stability: dict[str, Any] = {}
    for case_set in sorted({spec["case_set"] for spec in runs}):
        rows = [row for row in all_rows if row["case_set"] == case_set]
        stability[case_set] = round_stability(rows)

    return {
        "pair": list(pair),
        "by_run": dict(sorted(by_run.items())),
        "by_case_set_and_arm": dict(sorted(by_cell.items())),
        "paired": paired_by_case_set,
        "round_stability": stability,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        metavar="case_set=...,arm=...,round=...,path=...",
        help="一次运行的标签与路径，可重复",
    )
    parser.add_argument("--pair", required=True, help="第一臂,第二臂（差值 = 第二臂 - 第一臂）")
    parser.add_argument("--json", help="把完整结果写到该路径")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="只打印分组与配对结果，不打印每次运行",
    )
    args = parser.parse_args(argv)

    runs = [parse_run(spec) for spec in args.run]
    pair = parse_pair(args.pair)
    result = analyse(runs, pair)

    if args.json:
        Path(args.json).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _line(label: str, entry: dict[str, Any]) -> str:
        quality = entry["quality"]
        hops = entry["hops"]
        ratio = hops["ratio"]
        hop_text = f"{hops['matched']}/{hops['total']}" + (
            f"={ratio}" if ratio is not None else ""
        )
        latency = quality.get("latency") or {}
        counts = quality.get("status_counts") or {}
        counts_text = " ".join(f"{name}:{count}" for name, count in counts.items())
        return (
            f"  {label:32s} n={entry['calls']:2d} "
            f"ok={quality.get('completed')}/{quality.get('total')} "
            f"to={quality.get('provider_timeout_rate')} "
            f"hops={hop_text:9s} "
            f"span={quality.get('answer_span_recall_mean')} "
            f"lat={latency.get('mean')}(max {latency.get('max')}) "
            f"[{counts_text}]"
        )

    if not args.quiet:
        print("每次运行：")
        for label, entry in result["by_run"].items():
            print(_line(label, entry))

    print("\n按题集 × 臂（合并轮次）：")
    for label, entry in result["by_case_set_and_arm"].items():
        print(_line(label, entry))

    print("\n配对（两臂都完成的题）：")
    for case_set, entry in result["paired"].items():
        print(
            f"  {case_set}: n={entry['comparable_pairs']} "
            f"dropped={entry['dropped_cells']} "
            f"{pair[0]}={entry.get(f'{pair[0]}_mean_hop_recall')} "
            f"{pair[1]}={entry.get(f'{pair[1]}_mean_hop_recall')} "
            f"diff={entry.get('mean_difference')} "
            f"({pair[1]}更好 {entry.get(f'{pair[1]}_better_pairs')} / "
            f"{pair[0]}更好 {entry.get(f'{pair[0]}_better_pairs')} / "
            f"持平 {entry['tied_pairs']})"
        )

    print("\n同一配置跨轮的抖动（说明一个臂自身能移动多少）：")
    for case_set, entry in result["round_stability"].items():
        print(
            f"  {case_set}: 单元 {entry['cells']}，多轮 {entry['cells_with_multiple_rounds']}，"
            f"发生翻转 {entry['cells_that_changed']}"
        )
        for key, values in entry["changed"].items():
            print(f"      {key}: {values}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
