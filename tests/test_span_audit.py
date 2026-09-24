"""The span audit has to stay reproducible and stay honest.

Two failure modes are worth guarding, and they pull in opposite directions:

* the audit silently losing coverage, so a later run reports a number computed
  over fewer observations than it claims;
* the audit becoming a way to write off failures -- crediting observations the
  model could not possibly have answered, which would inflate capability.

The second is the more dangerous one, because it makes the system look better
while looking like diligence.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_span_audit import ALLOWED_VERDICTS, parse_run  # noqa: E402
from scripts.finalize_span_audit import (  # noqa: E402
    BATCH2_VERDICTS,
    REAL_VERDICTS,
    SYNTHETIC_VERDICTS,
)

AUDIT = ROOT / "data" / "span_audit.v2.json"

# Observations that must be credited: the answer states the hop's substance and
# the metric missed it.  Pinned because "the metric missed it" is exactly the
# claim a reader cannot check by re-running the code.
CREDITED_VERDICTS = ("correct_paraphrase", "damaged_span")


def _audit() -> dict:
    return json.loads(AUDIT.read_text(encoding="utf-8"))


def _observations() -> list[dict]:
    payload = _audit()
    return payload["real"]["observations"] + payload["synthetic"]["observations"]


def test_every_observation_carries_a_legal_verdict_and_a_reason():
    for item in _observations():
        assert item["verdict"] in ALLOWED_VERDICTS, item["key"]
        assert item["evidence_note"].strip(), item["key"]


def test_both_question_sets_are_audited():
    """Auditing only the set whose number looks wrong is the error under test.

    The corrected real figure is compared against the synthetic one, so leaving
    the synthetic set unaudited would compare a corrected number with a raw one.
    """
    payload = _audit()
    assert payload["real"]["totals"]["audited_failures"] > 0
    assert payload["synthetic"]["totals"]["audited_failures"] > 0


def test_an_unreachable_hop_is_never_credited_as_capability():
    """The mechanical rule, checked against the data rather than the prose.

    If the hop's source chunk never reached the model, the observation says
    nothing about the model.  It is removed from the denominator -- crediting it
    as a hit would inflate the score, and counting it as a miss would deflate it.
    """
    for item in _observations():
        if item["chunk_in_pack"]:
            continue
        assert item["verdict"] == "correct_refusal", item["key"]


def test_a_reachable_failure_is_never_excused_as_unreachable():
    """The reverse direction: a real miss must not be filed as a refusal."""
    for item in _observations():
        if item["verdict"] == "correct_refusal":
            assert item["chunk_in_pack"] is False, item["key"]


def test_the_taper_formula_is_filed_as_a_damaged_span():
    """r19 h1 is the provable defect, and the fix differs from a tolerance call.

    It must not be filed as `correct_paraphrase`: the matcher could not have
    matched any answer, which is a metric bug rather than a strictness choice.
    """
    damaged = [item for item in _observations() if item["verdict"] == "damaged_span"]
    assert damaged, "受损跨段必须被单独标出"
    for item in damaged:
        assert item["hop_id"] == "h1"
        assert "D1" in item["expected_span"]


def test_the_audit_covers_every_failed_hop_in_the_runs_it_names():
    """Coverage: the counts must match the runs, not a remembered number."""
    payload = _audit()
    for name in ("real", "synthetic"):
        block = payload[name]
        named = {run["path"].split("/")[-1].split("\\")[-1] for run in block["runs"]}
        for item in block["observations"]:
            assert item["run_file"] in named, item["run_file"]
            assert item["matched"] is False, "审计只应收录失败的跳"


@pytest.mark.parametrize(
    "table,expected_size",
    [(REAL_VERDICTS, 9), (SYNTHETIC_VERDICTS, 4), (BATCH2_VERDICTS, 11)],
)
def test_the_verdict_tables_are_pinned(table, expected_size):
    """A hop that gains or loses a verdict is a decision, so it shows up here."""
    assert len(table) == expected_size
    for (question_id, hop_id), (verdict, reason) in table.items():
        assert verdict in ALLOWED_VERDICTS, (question_id, hop_id)
        assert reason.strip(), (question_id, hop_id)


def test_the_batch2_audit_is_a_separate_document():
    """batch2 must not have been folded into the pinned v2 document.

    The v2 file is named by history and its `supersedes`/`what_changed` describe
    the v1->v2 method change, which says nothing about a new question batch.
    """
    payload = json.loads((ROOT / "data" / "span_audit.batch2.v1.json").read_text(encoding="utf-8"))
    assert payload["audit_version"] == "span-audit.batch2.v1"
    assert payload["supersedes"] is None
    assert set(payload["overrides"]) == {"batch2"}
    assert "batch2" in payload and "real" not in payload and "synthetic" not in payload
    for item in payload["batch2"]["observations"]:
        assert item["verdict"] in ALLOWED_VERDICTS, item["key"]
        assert item["evidence_note"].strip(), item["key"]
        assert item["chunk_in_pack"] is True, (
            "batch2 的失败观测全部可达（122/122），出现不可达说明证据装配变了"
        )


def test_the_batch2_failures_are_mostly_measurement_not_ability():
    """The number that motivated demoting batch2's spans.

    110 of 122 failed observations were the substance delivered in other words.
    If that share ever drops, the demotion in SPAN_DEMOTIONS is no longer
    justified by this evidence and should be revisited.
    """
    payload = json.loads((ROOT / "data" / "span_audit.batch2.v1.json").read_text(encoding="utf-8"))
    totals = payload["batch2"]["totals"]
    assert totals["audited_failures"] == 122
    assert totals["credited_by_audit"] == 110
    assert totals["unmeasurable_refusals"] == 0
    assert totals["incorrect"] == 12


def test_parse_run_rejects_an_incomplete_spec():
    with pytest.raises(SystemExit):
        parse_run("arm=evidenceall")


# ---------------------------------------------------------------------------
# The mechanical override stays reachability-only.
# ---------------------------------------------------------------------------


def test_only_reachability_overrides_the_human_table():
    """A wording-based override was tried and rejected; this keeps it out.

    A marker list over the whole answer ("无法确认", "未显示", ...) overrode 28 of
    44 verdicts, including hops that had been read and confirmed as delivered.
    The cause is in the data: r18 h1's answer delivers the hop *and* says
    `当前资料无法确认` about a different sub-point.  A refusal is about one
    sub-point; the marker is answer-global -- the same failure as a term matcher
    firing on a topic word inside a refusal.

    Scoping it to the hop does not rescue it either: gating on `terms_matched`
    misses r17 h1 (its terms are present, in the sentence that declines to use
    them), and sentence-scoping misses it too.

    So the audit keeps per-hop granularity, which is the right granularity for
    deciding *which spans to demote*.  The per-observation numbers come from the
    scoring code against the demoted contract.
    """
    payload = _audit()
    for name in ("real", "synthetic"):
        for item in payload["overrides"][name]:
            assert item["mechanical"] == "correct_refusal", (
                f"{name}/{item['question_id']} {item['hop_id']}: "
                "只有「证据不可达」可以覆盖人工表；措辞型覆盖已被证伪"
            )


def test_the_rejected_override_is_recorded_rather_than_removed():
    """The record matters: the next reader should not re-try it blind."""
    payload = _audit()
    assert payload["audit_version"] == "span-audit.v2"
    assert payload["supersedes"] == "span-audit.v1"
    assert "整篇触发" in payload["what_changed"]
