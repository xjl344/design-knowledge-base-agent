"""The A/B analysis must not invent a difference, and must show the targets.

Two properties are worth pinning before the real run is read:

* a **null control** -- the same answers labelled as two prompts must produce a
  difference of exactly zero.  If it does not, the analysis is measuring
  something other than the prompts;
* the **pre-registered** questions are reported on their own, so a null result
  cannot be salvaged by hunting through the other questions afterwards.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analyze_prompt_ab import (  # noqa: E402
    CONTROL_QUESTIONS,
    TARGET_QUESTIONS,
    analyse,
)

PROBE = ROOT / "data" / "runs" / "mh_gate_probe_v2.json"


def _null_control() -> dict:
    """The probe's answers, relabelled as if two prompts had produced them.

    Every row is duplicated under both version strings, so any non-zero
    difference is the analysis's own doing.
    """
    payload = json.loads(PROBE.read_text(encoding="utf-8"))
    arms = [
        {"name": "r2", "max_items": 99, "max_chars_per_item": None},
        {"name": "r3", "max_items": 99, "max_chars_per_item": None},
    ]
    rows = []
    for row in payload["rows"]:
        if row.get("status") != "completed":
            continue
        for name, version in (("r2", "v-old"), ("r3", "v-new")):
            copy = dict(row)
            copy["arm"] = name
            copy["prompt_version"] = version
            rows.append(copy)
    return {
        "arms": arms,
        "prompt_versions": ["v-new", "v-old"],
        "rows": rows,
    }


def test_the_null_control_reports_no_difference():
    result = analyse(_null_control())
    assert result["every_question"]["cells"] > 0, "空对照没有产生任何配对单元"
    assert result["every_question"]["mean_difference"] == 0.0
    assert result["every_question"]["new_better"] == 0
    assert result["every_question"]["old_better"] == 0


def test_the_targets_are_reported_separately_from_everything_else():
    """A null on the targets must not be rescued by the other questions."""
    result = analyse(_null_control())
    assert result["pre_registered_targets"] == list(TARGET_QUESTIONS)
    assert result["negative_controls"] == list(CONTROL_QUESTIONS)
    for key in (
        "target_questions",
        "control_questions",
        "all_other_questions",
        "every_question",
    ):
        assert "mean_difference" in result[key] or result[key]["cells"] == 0
    # The three groups must be disjoint and together cover everything.
    total = (
        result["target_questions"]["cells"]
        + result["control_questions"]["cells"]
        + result["all_other_questions"]["cells"]
    )
    assert total == result["every_question"]["cells"]


def test_the_control_question_is_not_a_target():
    """r18's failures are synonyms, not categories, so rule 8 cannot fix them.

    Keeping it out of the targets and in as a control is what stops a null
    result on the targets being confused with "the prompt did nothing anywhere".
    """
    assert not set(TARGET_QUESTIONS) & set(CONTROL_QUESTIONS)
    result = analyse(_null_control())
    assert result["control_questions"]["cells"] > 0, (
        "阴性对照题在运行里没出现——对照就成了空话"
    )


def test_the_targets_are_actually_present_in_the_probe():
    """Pre-registering questions that the run does not contain would be vacuous."""
    result = analyse(_null_control())
    assert result["target_questions"]["cells"] > 0, (
        "预登记的目标题在运行里一个都没出现——预登记就成了空话"
    )


def test_a_single_version_is_refused():
    payload = _null_control()
    payload["prompt_versions"] = ["v-old"]
    for row in payload["rows"]:
        row["prompt_version"] = "v-old"
    with pytest.raises(SystemExit, match="两个提示词版本"):
        analyse(payload)
