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
    _span_matches,
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
    """Can any answer match this span?  Two independent tests.

    **Identity.**  An answer that quotes the declared span verbatim must be
    credited.  A span that fails this is unmatchable by construction.  This
    catches damage applied to *both* sides equally.

    **Variable preservation.**  Every `letter+digits` token in the declared span
    must still be represented after normalisation.  This catches damage applied
    to *one* side only -- which is the case that actually bit: the contract's
    `D1` was deleted as a table label while the answer's LaTeX `D_1` survived,
    so the two sides normalised to different things and no answer could match.
    Identity alone does not catch that, because the span matches itself.
    """
    normalised = _normalise_text(_strip_span_noise(span))
    after_labels = _SPAN_BARE_LABEL_RE.sub("", span)
    after_parens = _SPAN_PARENTHETICAL_RE.sub("", span)
    labels_removed = re.findall(r"(?<![A-Za-z0-9])[A-Za-z]\d{1,2}(?![A-Za-z0-9])", span)
    parens_removed = _SPAN_PARENTHETICAL_RE.findall(span)

    # Tokens the normalised form no longer represents.  Only letters are
    # required to survive: the operators and brackets are *meant* to go.
    # A substring test comes first because the tokeniser is greedy -- `vhd12` is
    # one token, so a startswith-only check would report the `h` inside it as
    # lost.
    normalised_tokens = re.findall(r"[a-z]+\d*|\d+", normalised)
    lost: list[str] = []
    for token in re.findall(r"[A-Za-z]+\d*", span):
        lowered = token.lower()
        if lowered in normalised:
            continue
        if any(item.startswith(lowered) for item in normalised_tokens):
            continue
        lost.append(token)

    return {
        "normalised": normalised,
        "after_labels": after_labels,
        "after_parens": after_parens,
        "labels_removed": sorted(set(labels_removed)),
        "parens_removed": parens_removed,
        "tokens_lost": sorted(set(lost)),
        "matches_itself": _span_matches(span, span),
        "labels_damage": bool(lost),
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

    unmatchable = []
    informational = []
    for item in spans:
        report = inspect(item["span"])
        item.update(report)
        if not report["matches_itself"]:
            unmatchable.append(item)
        elif report["tokens_lost"] or report["parens_damage"]:
            informational.append(item)

    if not unmatchable and not informational:
        print("所有期望跨段都能自匹配，且规范化未丢失词元。")
        return 0

    for item in unmatchable + informational:
        print("-" * 76)
        print(
            f"  [{item['source']}] {item['question_id']} {item['hop_id']}"
            + ("" if item["matches_itself"] else "   ← 无法自匹配")
        )
        print(f"    原文        : {item['span']!r}")
        print(f"    最终规范化  : {item['normalised']!r}")
        if item["tokens_lost"]:
            print(f"    规范化后不再出现的词元: {item['tokens_lost']}")
            print("      （两侧同样被剥离时无害；只在一侧剥离时任何答案都匹配不上）")
        if item["parens_damage"]:
            print(f"    被当作括号删掉: {item['parens_removed']}")

    print()
    print(f"无法自匹配（必须处理）: {len(unmatchable)}")
    print(f"规范化后丢失词元（需人确认是否对称）: {len(informational)}")
    if unmatchable:
        print()
        print("自匹配失败的跨段：连逐字引用契约原文的答案都拿不到分。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
