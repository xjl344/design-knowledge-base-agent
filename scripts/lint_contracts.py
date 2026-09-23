"""Lint every contract for elements that cannot do the job they were written for.

Each check below exists because a real defect of that shape shipped and was
found the hard way, one at a time.  This script exists so the next one is found
by a command instead of by reading answers.

Checks
------
``empty-needle``   The normalised form is empty, so the matcher rejects it and
                   the element can never be satisfied.  ``P5``/``P50``/``P95``
                   all matched the bare-label strip and vanished, which made
                   ``p08 h2`` a permanent false negative.
``vacuous-needle`` The normalised form is one character, so it matches almost
                   any answer.  A check that everything passes is not a check.
``shadowed``       One alternative's normalisation is a substring of another's
                   in the same group, so the shorter one can never be the
                   deciding factor -- the group is weaker than it reads.
``echoed``         The element appears verbatim in the question, so an answer
                   that repeats the question scores without answering it.

Exit code is 1 when anything at ``fail`` level is found, so it can gate a build.

Usage::

    python scripts/lint_contracts.py
    python scripts/lint_contracts.py --json
"""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.frozen_evidence import _normalise_text, _strip_span_noise  # noqa: E402

# Normalised length below this is treated as vacuous.  Two is the smallest
# value that can be a real word rather than a digit or a stray character.
MIN_MEANINGFUL_LENGTH = 2


def _term_needle(term: str) -> str:
    """The same normalisation the term matcher uses."""
    return _normalise_text(_strip_span_noise(term, labels=False))


def _span_needle(span: str) -> str:
    """The same normalisation the span matcher uses on the contract side."""
    return _normalise_text(_strip_span_noise(span))


def lint_case(case: dict[str, Any], source: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    case_id = str(case.get("id"))
    question = _normalise_text(str(case.get("question") or ""))

    def check(
        needle: str,
        raw: str,
        kind: str,
        where: str,
        siblings: list[str] = (),
    ) -> None:
        if not needle:
            findings.append({
                "level": "fail",
                "check": "empty-needle",
                "source": source,
                "case": case_id,
                "where": where,
                "raw": raw,
                "detail": "规范化后为空，任何答案都匹配不上",
            })
            return
        if len(needle) < MIN_MEANINGFUL_LENGTH:
            findings.append({
                "level": "warn",
                "check": "vacuous-needle",
                "source": source,
                "case": case_id,
                "where": where,
                "raw": raw,
                "detail": f"规范化后只有 {len(needle)} 个字符，几乎任何答案都能匹配",
            })
        for other in siblings:
            if other == raw:
                continue
            other_needle = (
                _span_needle(other) if kind == "span" else _term_needle(other)
            )
            if other_needle and other_needle != needle and needle in other_needle:
                findings.append({
                    "level": "warn",
                    "check": "shadowed",
                    "source": source,
                    "case": case_id,
                    "where": where,
                    "raw": raw,
                    "detail": f"{raw!r} 是同一组里 {other!r} 的子串，"
                    "满足后者必然满足前者，该备选不起作用",
                })
        if question and len(needle) >= MIN_MEANINGFUL_LENGTH and needle in question:
            findings.append({
                "level": "warn",
                "check": "echoed",
                "source": source,
                "case": case_id,
                "where": where,
                "raw": raw,
                "detail": "该元素在问题里逐字出现，复述问题即可得分",
            })

    for hop in case.get("required_hops") or []:
        hop_id = str(hop.get("hop_id"))
        span = hop.get("expected_span")
        if span:
            check(
                _span_needle(str(span)),
                str(span),
                "span",
                f"{hop_id}.expected_span",
                [str(item) for item in hop.get("expected_span_alternatives") or []],
            )
        for index, group in enumerate(hop.get("required_terms") or []):
            if not isinstance(group, list):
                continue
            for term in group:
                check(
                    _term_needle(str(term)),
                    str(term),
                    "term",
                    f"{hop_id}.required_terms[{index}]",
                    [str(item) for item in group],
                )

    for index, group in enumerate(case.get("required_terms") or []):
        if not isinstance(group, list):
            continue
        for term in group:
            check(
                _term_needle(str(term)),
                str(term),
                "term",
                f"required_terms[{index}]",
                [str(item) for item in group],
            )
    return findings


def lint_file(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        finding
        for case in payload.get("cases") or []
        for finding in lint_case(case, path.name)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument(
        "--paths",
        nargs="*",
        default=sorted(glob.glob(str(ROOT / "data" / "generation_eval*.json"))),
    )
    args = parser.parse_args(argv)

    findings = [f for path in args.paths for f in lint_file(Path(path))]
    failures = [f for f in findings if f["level"] == "fail"]
    warnings = [f for f in findings if f["level"] == "warn"]

    if args.json:
        print(json.dumps(
            {"failures": failures, "warnings": warnings},
            ensure_ascii=False,
            indent=2,
        ))
        return 1 if failures else 0

    print(f"检查 {len(args.paths)} 份契约")
    for level, group in (("FAIL", failures), ("WARN", warnings)):
        if not group:
            continue
        print(f"\n{level} ({len(group)})")
        for item in group:
            print(f"  [{item['check']}] {item['source']} {item['case']} "
                  f"{item['where']} {item['raw']!r}")
            print(f"      {item['detail']}")
    print(f"\n合计 fail={len(failures)} warn={len(warnings)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
