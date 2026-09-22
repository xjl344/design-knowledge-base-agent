"""Check that every declared `expected_span` can still be matched after normalising.

Why this exists
---------------
`_span_matches` normalises both sides before comparing, and normalisation is
lossy in two ways that a *formula* is uniquely vulnerable to:

* ``_SPAN_BARE_LABEL_RE`` removes ``letter + 1-2 digits`` with boundaries on both
  sides.  That is right for table labels (``T1``, ``B3``, ``H2``) and wrong for
  formula variables (``D1``, ``D2``).  A span like
  ``V = π × h × (D1² + D1 × D2 + D2²) / 12 / 1000`` loses both ``D`` terms and
  becomes ``V = π × h × (² + × + ²) / 12 / 1000``.
* ``_normalise_text`` then deletes the operators, so the surviving numbers fuse:
  ``/ 12 / 1000`` becomes ``121000``, a token that appears in no answer.

The result is a declared span that **cannot be matched by any answer**, however
correct.  It is not a scoring judgement that went the wrong way; the target has
been destroyed before the comparison starts.

The guard reports both kinds of damage separately, because the fixes differ:

* ``labels_removed`` -- content removed by label-stripping.  Fix the regex or the
  span; either way it must be a deliberate decision.
* ``parens_removed`` -- content removed by parenthetical-stripping.  For ``(mL)``
  and ``(mm)`` this is correct and harmless; for a formula's bracketed term it is
  not, and the two are indistinguishable to the regex.

Exit status is 1 if any span loses *alphanumeric* content to label-stripping
(the provable defect), and 0 otherwise.  Parenthetical removal alone does not
fail the check -- it is frequently correct -- but it is always reported.

Usage::

    python scripts/check_span_normalisation.py
    python scripts/check_span_normalisation.py --evaluation data/generation_eval.multihop.real.v1.json
"""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.frozen_evidence import (  # noqa: E402
    _normalise_text,
    _SPAN_BARE_LABEL_RE,
    _SPAN_PARENTHETICAL_RE,
    _strip_span_noise,
)

DEFAULT_GLOB = "data/generation_eval*.json"


def iter_cases(payload: Any) -> list[tuple[str, dict[str, Any]]]:
    """Yield ``(question_id, case)`` for both list- and dict-shaped contracts."""
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    if isinstance(cases, dict):
        return [(str(key), value) for key, value in cases.items()]
    if isinstance(cases, list):
        return [
            (str(case.get("question_id") or index), case)
            for index, case in enumerate(cases)
        ]
    return []


def collect_spans(paths: list[Path]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for question_id, case in iter_cases(payload):
            for hop in case.get("required_hops") or []:
                span = hop.get("expected_span")
                if not span:
                    continue
                found.append({
                    "source": path.name,
                    "question_id": question_id,
                    "hop_id": hop.get("hop_id"),
                    "span": span,
                })
    return found


def inspect(span: str) -> dict[str, Any]:
    after_labels = _SPAN_BARE_LABEL_RE.sub("", span)
    after_parens = _SPAN_PARENTHETICAL_RE.sub("", span)
    labels_removed = re.findall(r"(?<![A-Za-z0-9])[A-Za-z]\d{1,2}(?![A-Za-z0-9])", span)
    parens_removed = _SPAN_PARENTHETICAL_RE.findall(span)
    # A removed label is only *damage* if it was alphanumeric content.  A span
    # that legitimately mentions a table label still loses it here, which is why
    # this is reported for a human to accept rather than silently fixed.
    return {
        "normalised": _normalise_text(_strip_span_noise(span)),
        "after_labels": after_labels,
        "after_parens": after_parens,
        "labels_removed": sorted(set(labels_removed)),
        "parens_removed": parens_removed,
        "labels_damage": bool(labels_removed),
        "parens_damage": bool(parens_removed),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--evaluation", action="append", help="契约文件，可重复")
    parser.add_argument("--glob", default=DEFAULT_GLOB, help="未指定 --evaluation 时使用的通配")
    args = parser.parse_args(argv)

    if args.evaluation:
        paths = [Path(p) for p in args.evaluation]
    else:
        paths = sorted(Path(p) for p in glob.glob(args.glob))
    paths = [path for path in paths if path.exists()]
    if not paths:
        raise SystemExit("没有找到契约文件")

    spans = collect_spans(paths)
    print(f"契约文件 {len(paths)} 个，声明的期望跨段 {len(spans)} 个\n")

    damaged = []
    for item in spans:
        report = inspect(item["span"])
        item.update(report)
        if report["labels_damage"] or report["parens_damage"]:
            damaged.append(item)

    if not damaged:
        print("所有期望跨段在规范化后内容完好。")
        return 0

    for item in damaged:
        print("-" * 76)
        print(f"  [{item['source']}] {item['question_id']} {item['hop_id']}")
        print(f"    原文        : {item['span']!r}")
        if item["labels_damage"]:
            print(f"    剥「标签」后: {item['after_labels']!r}")
            print(f"      被当作标签删掉: {item['labels_removed']}")
        if item["parens_damage"]:
            print(f"    剥「括号」后: {item['after_parens']!r}")
            print(f"      被当作括号删掉: {item['parens_removed']}")
        print(f"    最终规范化  : {item['normalised']!r}")

    label_damage = [item for item in damaged if item["labels_damage"]]
    paren_only = [item for item in damaged if not item["labels_damage"]]
    print()
    print(f"标签剥离造成的损坏（必须处理）: {len(label_damage)}")
    print(f"仅括号剥离（通常无害，需人确认）: {len(paren_only)}")
    if label_damage:
        print()
        print("这些跨段的变量被当成表标签删掉了，任何答案都无法匹配到它们。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
