"""Two prompts, selected by version, comparable inside one time window.

A prompt change is the one intervention this project has been avoiding, and the
reason is measurable: the same configuration moves across rounds by more than
any single prompt tweak is likely to (see the round-stability section of the
experiment report).  So the version plumbing exists to make a prompt A/B
*interleaved* -- both prompts inside the same window -- rather than two blocks,
which would attribute the provider's mood to the prompts.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.interleaved_generation_rounds import parse_arm  # noqa: E402
from src.generator_v2 import (  # noqa: E402
    GENERATOR_PROMPTS,
    GENERATOR_PROMPT_VERSION,
    GENERATOR_PROMPT_VERSION_R3,
    _build_chain,
)


# ---------------------------------------------------------------------------
# The arm spec.
# ---------------------------------------------------------------------------


def test_an_arm_without_a_prompt_version_keeps_its_old_meaning():
    """Every command line written before this existed must still work."""
    arm = parse_arm("evidenceall=99:none")
    assert arm == {
        "name": "evidenceall",
        "max_items": 99,
        "max_chars_per_item": None,
        "prompt_version": None,
    }


def test_an_arm_can_carry_a_prompt_version():
    arm = parse_arm(f"r3=99:none:{GENERATOR_PROMPT_VERSION_R3}")
    assert arm["prompt_version"] == GENERATOR_PROMPT_VERSION_R3
    assert arm["max_items"] == 99
    assert arm["max_chars_per_item"] is None


def test_a_malformed_arm_is_rejected():
    with pytest.raises(SystemExit):
        parse_arm("evidenceall")
    with pytest.raises(SystemExit):
        parse_arm("evidenceall=99")


# ---------------------------------------------------------------------------
# The prompt registry.
# ---------------------------------------------------------------------------


def test_the_new_version_adds_a_rule_rather_than_editing_the_old_one():
    """Editing in place would silently re-label results already on disk."""
    assert GENERATOR_PROMPT_VERSION == "generator-v2-20260919-r2"
    assert GENERATOR_PROMPT_VERSION_R3 == "generator-v2-20260923-r3"
    assert set(GENERATOR_PROMPTS) == {
        GENERATOR_PROMPT_VERSION,
        GENERATOR_PROMPT_VERSION_R3,
    }
    old = GENERATOR_PROMPTS[GENERATOR_PROMPT_VERSION].messages[0].prompt.template
    new = GENERATOR_PROMPTS[GENERATOR_PROMPT_VERSION_R3].messages[0].prompt.template
    # r2's rules must survive verbatim.  Compare up to `{context}`, because r3
    # inserts rule 8 before it and the whole of r2 is therefore not a
    # contiguous substring of r3.
    old_rules, _, old_tail = old.partition("{context}")
    new_rules, _, new_tail = new.partition("{context}")
    assert old_rules.strip() in new_rules
    assert old_tail == new_tail
    assert "8." in new_rules
    assert "8." not in old_rules


def test_rule_eight_names_the_failure_it_targets():
    """The three remaining misses all name the category and stop.

    `坐姿膝高` is never written although the evidence lists it; `680~760` is
    never written although the answer says `桌面高`; the capacity relation is
    named but not stated.  Rule 8 has to say so explicitly, or it is just
    another instruction about being thorough.
    """
    text = GENERATOR_PROMPTS[GENERATOR_PROMPT_VERSION_R3].messages[0].prompt.template
    assert "逐项写出" in text
    assert "不算回答" in text
    assert "关系式" in text


def test_an_unknown_prompt_version_fails_loudly():
    """Silently falling back to the default would make an A/B measure nothing.

    Both arms would run the same prompt and the difference would be pure noise
    reported as a prompt effect.
    """
    with pytest.raises(ValueError, match="未知的提示词版本"):
        _build_chain("generator-v2-does-not-exist")


# ---------------------------------------------------------------------------
# A mixed run must not claim to be one configuration.
# ---------------------------------------------------------------------------


def test_a_mixed_prompt_run_reports_both_and_claims_neither(tmp_path):
    output = tmp_path / "ab.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "interleaved_generation_rounds.py"),
            "--case-set",
            f"name=real_complete,snapshot={ROOT / 'data' / 'frozen_multihop_real_complete.v2.jsonl'},"
            f"evaluation={ROOT / 'data' / 'generation_eval.multihop.real.complete.v2.json'}",
            "--arm", f"r2=99:none:{GENERATOR_PROMPT_VERSION}",
            "--arm", f"r3=99:none:{GENERATOR_PROMPT_VERSION_R3}",
            "--rounds", "1",
            "--dry-run",
            "--output", str(output),
        ],
        capture_output=True,
        text=True,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 and "ModuleNotFoundError" in combined:
        pytest.skip("运行脚本需要模型依赖，离线解释器里跑不了")
    assert result.returncode == 0, combined[-800:]

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["prompt_versions"] == sorted(
        [GENERATOR_PROMPT_VERSION, GENERATOR_PROMPT_VERSION_R3]
    )
    # No single version may be claimed: an aggregator reading `prompt_version`
    # would otherwise average two prompts together as one configuration.
    assert payload["prompt_version"] is None
    for row in payload["rows"]:
        assert row["prompt_version"] in (
            GENERATOR_PROMPT_VERSION,
            GENERATOR_PROMPT_VERSION_R3,
        )


def test_a_single_prompt_run_still_claims_its_version(tmp_path):
    output = tmp_path / "single.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "interleaved_generation_rounds.py"),
            "--case-set",
            f"name=real_complete,snapshot={ROOT / 'data' / 'frozen_multihop_real_complete.v2.jsonl'},"
            f"evaluation={ROOT / 'data' / 'generation_eval.multihop.real.complete.v2.json'}",
            "--arm", "r2=99:none",
            "--rounds", "1",
            "--dry-run",
            "--output", str(output),
        ],
        capture_output=True,
        text=True,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 and "ModuleNotFoundError" in combined:
        pytest.skip("运行脚本需要模型依赖")
    assert result.returncode == 0, combined[-800:]
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["prompt_version"] == GENERATOR_PROMPT_VERSION
    assert payload["prompt_versions"] == [GENERATOR_PROMPT_VERSION]
