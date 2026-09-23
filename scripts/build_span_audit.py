"""Build the span-audit table: every failed hop, with the evidence a human needs.

Why this exists
---------------
`hop_recall` requires the answer to contain `expected_span` (near-)verbatim.  When
a hop fails, three very different things produce the same `False`:

* the model never brought the hop's fact out (**real miss**);
* the model brought it out in different words (**paraphrase loss**);
* the hop's source chunk was not in the pack at all, so the model **could not**
  have known it (**unreachable** -- a correct refusal, not a failure).

The third is invisible in the audit today: `hop_results` records
`source_chunk_id` but never whether that chunk reached the model.  A hop whose
evidence was absent is scored as a miss exactly like a hop that was answered
badly.  The `chunk_in_pack` column below fixes that, and it is the column that
decides whether an evidence-5 refusal is *correct* or merely *unlucky*.

This script only fills the mechanical columns.  `verdict` and `evidence_note`
are left empty on purpose: separating a paraphrase loss from a real miss needs a
person reading the answer, and a regex that guesses would be the very defect this
audit exists to measure.  Fill them in, then validate with ``--check``.

The scope is hops that **failed**.  A hop that matched needs no verdict.

Usage::

    python scripts/build_span_audit.py \
        --run arm=evidenceall,path=data/runs/mhreal_r3.json \
        --snapshot data/frozen_multihop_real_cases.jsonl \
        --output data/span_audit.v1.json

    python scripts/build_span_audit.py --audit data/span_audit.v1.json --check
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.frozen_evidence import build_evidence_pack, load_cases  # noqa: E402

AUDIT_VERSION = "span-audit.v1"

# The vocabulary the verdict has to come from.  `damaged_span` is separate from
# `ungradable` because the two have different fixes: the first is a metric bug
# with a known cause, the second means the audit itself could not decide.
ALLOWED_VERDICTS = (
    "correct_literal",
    "correct_paraphrase",
    "correct_refusal",
    "incorrect",
    "ungradable",
    "damaged_span",
)

EXCERPT_RADIUS = 140


def parse_run(spec: str) -> dict[str, str]:
    """``arm=...,path=...`` -- commas, not colons (Windows drive letters)."""
    fields: dict[str, str] = {}
    for piece in spec.split(","):
        key, separator, value = piece.partition("=")
        if not separator:
            raise SystemExit(f"run 字段必须是 key=value，收到 {piece!r}")
        fields[key.strip()] = value.strip()
    missing = {"arm", "path"} - set(fields)
    if missing:
        raise SystemExit(f"run 缺少字段：{sorted(missing)}")
    return fields


def excerpt(answer: str, terms: list[dict[str, Any]]) -> str:
    """A window around the first declared term hit, else the head of the answer.

    Centred on the term because the question being asked is "did the answer say
    this here", and the surrounding sentences are what a reader needs to judge
    it.  Falls back to the head when no term appears at all -- that absence is
    itself the finding.
    """
    text = str(answer or "")
    for group in terms:
        for hit in group.get("hits") or []:
            index = text.find(str(hit))
            if index >= 0:
                start = max(0, index - EXCERPT_RADIUS)
                return text[start:index + EXCERPT_RADIUS]
    return text[: EXCERPT_RADIUS * 2]


def resolve_pack_config(payload: dict[str, Any], arm: str) -> tuple[Any, Any]:
    """The pack configuration a row was scored under.

    Two producers write runs: the replay harness records `max_evidence` /
    `max_chars_per_item` at the top level, and the interleaved harness records a
    list of arms because each arm carries its own cap.  Reading only the first
    shape silently produced `max_items=None` and crashed inside
    `build_evidence_pack` -- so both are handled here.
    """
    if "max_evidence" in payload:
        return payload.get("max_evidence"), payload.get("max_chars_per_item")
    for entry in payload.get("arms") or []:
        if str(entry.get("name")) == arm:
            return entry.get("max_items"), entry.get("max_chars_per_item")
    raise SystemExit(f"运行文件里找不到臂 {arm!r} 的 pack 配置")


def collect(
    runs: list[dict[str, str]], snapshot: Path, *, include_matched: bool
) -> list[dict[str, Any]]:
    cases = load_cases(snapshot)
    observations: list[dict[str, Any]] = []
    for spec in runs:
        path = Path(spec["path"])
        if not path.exists():
            raise SystemExit(f"运行文件不存在：{path}")
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        max_items, max_chars = resolve_pack_config(payload, spec["arm"])
        run_id = payload.get("run_id")
        for row in payload.get("rows") or []:
            audit = row.get("audit") or {}
            if not audit.get("hop_metric_applicable"):
                continue
            case = cases.get(str(row.get("question_id")))
            if case is None:
                raise SystemExit(f"快照里没有 {row.get('question_id')!r}")
            # Rebuilt rather than read: the freeze makes this deterministic, and
            # the run never recorded which chunks reached the model.
            pack = build_evidence_pack(case, max_items, max_chars)
            pack_chunks = {item.chunk_id for item in pack.items}
            for hop in audit.get("hop_results") or []:
                if hop.get("matched") and not include_matched:
                    continue
                terms = hop.get("terms") or []
                observations.append({
                    "key": "|".join([
                        str(run_id), spec["arm"], str(row.get("question_id")),
                        str(hop.get("hop_id")),
                    ]),
                    "run_id": run_id,
                    "run_file": path.name,
                    "arm": spec["arm"],
                    "max_evidence": max_items,
                    "max_chars_per_item": max_chars,
                    "question_id": row.get("question_id"),
                    "hop_id": hop.get("hop_id"),
                    "from_question": hop.get("from_question"),
                    "expected_span": hop.get("expected_span"),
                    # `matched` is the hop's own verdict and has to travel with
                    # it: `--include-matched` lists passing hops too, and without
                    # this column a passing hop is indistinguishable from a
                    # failing one that merely has the same shape.
                    "matched": bool(hop.get("matched")),
                    "span_matched": hop.get("span_matched"),
                    "terms_matched": bool(terms) and all(t.get("matched") for t in terms),
                    "terms": terms,
                    "source_chunk_id": hop.get("source_chunk_id"),
                    "chunk_in_pack": hop.get("source_chunk_id") in pack_chunks,
                    "evidence_count": len(pack.items),
                    "answer_excerpt": excerpt(row.get("answer") or "", terms),
                    "answer": row.get("answer") or "",
                    "verdict": "",
                    "evidence_note": "",
                })
    return observations


def check(path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    observations = payload.get("observations") or []
    problems: list[str] = []
    counts: dict[str, int] = {}
    for item in observations:
        verdict = item.get("verdict") or ""
        if verdict not in ALLOWED_VERDICTS:
            problems.append(f"{item.get('key')}: verdict 未填或非法：{verdict!r}")
            continue
        counts[verdict] = counts.get(verdict, 0) + 1
        if not (item.get("evidence_note") or "").strip():
            problems.append(f"{item.get('key')}: 缺少 evidence_note")
    print(f"审计条目 {len(observations)}")
    for verdict in ALLOWED_VERDICTS:
        if counts.get(verdict):
            print(f"  {verdict:20s} {counts[verdict]}")
    if problems:
        print()
        for problem in problems:
            print(f"  ⚠️  {problem}")
        return 1
    print("\n全部条目已填且合法。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", action="append", help="arm=...,path=...，可重复")
    parser.add_argument(
        "--snapshot", default="data/frozen_multihop_real_cases.jsonl",
        help="用于重建证据包的冻结快照",
    )
    parser.add_argument("--output", help="写出审计表")
    parser.add_argument(
        "--audit", help="已有审计表；配合 --check 校验",
    )
    parser.add_argument("--check", action="store_true", help="校验已填的审计表")
    parser.add_argument(
        "--include-matched", action="store_true",
        help="连命中的跳一起列出（默认只列失败的跳）",
    )
    args = parser.parse_args(argv)

    if args.check:
        if not args.audit:
            raise SystemExit("--check 需要 --audit")
        return check(Path(args.audit))

    if not args.run or not args.output:
        raise SystemExit("生成审计表需要 --run 与 --output")

    runs = [parse_run(spec) for spec in args.run]
    snapshot = Path(args.snapshot)
    observations = collect(runs, snapshot, include_matched=args.include_matched)

    payload = {
        "audit_version": AUDIT_VERSION,
        "snapshot": str(snapshot),
        "allowed_verdicts": list(ALLOWED_VERDICTS),
        "scope": "命中的跳已排除" if not args.include_matched else "包含命中的跳",
        "runs": [
            {
                "arm": spec["arm"],
                "path": spec["path"],
            }
            for spec in runs
        ],
        "observations": observations,
    }
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    unreachable = sum(1 for item in observations if not item["chunk_in_pack"])
    print(f"写出 {len(observations)} 条待审计观测 -> {args.output}")
    print(f"  其中证据未进 pack（模型不可能知道）: {unreachable}")
    print(f"  span 判否但 terms 全中（改写候选）  : "
          f"{sum(1 for item in observations if item['span_matched'] is False and item['terms_matched'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
