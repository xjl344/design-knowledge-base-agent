"""The linter must fire on each defect shape it claims to catch.

Every check exists because that defect shipped and was found by hand, one at a
time.  A check that cannot fail is worse than no check, because it makes the
next reader believe the class is covered.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lint_contracts import lint_case  # noqa: E402


def _case(hops, *, question="问题文本", terms=None):
    return {
        "id": "c1",
        "question": question,
        "required_hops": hops,
        "required_terms": terms or [],
    }


def _hop(**kwargs):
    base = {"hop_id": "h1", "required_terms": [["座高"]]}
    base.update(kwargs)
    return base


def _checks(findings, name):
    return [f for f in findings if f["check"] == name]


def test_empty_needle_is_a_failure():
    """`(mL)` normalises away, so no answer could ever satisfy it."""
    findings = lint_case(_case([_hop(required_terms=[["(mL)"]])]), "t.json")
    assert _checks(findings, "empty-needle")
    assert any(f["level"] == "fail" for f in findings)


def test_vacuous_needle_is_a_warning():
    """A one-character needle matches almost anything."""
    findings = lint_case(
        _case([_hop(required_terms=[["4"]])], question="无关问题"), "t.json"
    )
    assert _checks(findings, "vacuous-needle")


def test_shadowed_alternative_is_a_warning():
    """`P5` is a substring of `P50`, so it can never be the deciding factor.

    This is the group that made `p08 h2` unmatchable before the label strip was
    fixed; even after the fix the group is weaker than it reads.
    """
    findings = lint_case(
        _case([_hop(required_terms=[["P5", "P50", "P95"]])], question="无关问题"),
        "t.json",
    )
    shadowed = _checks(findings, "shadowed")
    assert any(f["raw"] == "P5" for f in shadowed)


def test_echoed_element_is_a_warning():
    """A term that is literally in the question can be satisfied by repeating it."""
    findings = lint_case(
        _case([_hop(required_terms=[["座高"]])], question="椅凳座高是多少"), "t.json"
    )
    assert _checks(findings, "echoed")


def test_a_clean_case_produces_nothing():
    """Neither the span nor the terms appear in the question, so no echo."""
    findings = lint_case(
        _case(
            [_hop(expected_span="400~440", required_terms=[["座高"], ["400~440"]])],
            question="椅凳的座面高度标准是多少",
        ),
        "t.json",
    )
    assert findings == []


def test_the_shipped_contracts_have_no_failures():
    """Warnings are allowed and listed; failures are not.

    Zero failures is what the empty-needle guard bought -- before it,
    `P5`/`P50`/`P95` were three elements no answer could satisfy.
    """
    from scripts.lint_contracts import lint_file

    findings = [
        f
        for path in sorted((ROOT / "data").glob("generation_eval*.json"))
        for f in lint_file(path)
    ]
    failures = [f for f in findings if f["level"] == "fail"]
    assert failures == [], "\n".join(
        f"{f['source']} {f['case']} {f['where']} {f['raw']!r}: {f['detail']}"
        for f in failures
    )
