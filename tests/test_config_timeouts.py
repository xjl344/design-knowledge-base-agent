"""Guards against timeout settings that can never take effect.

A nested budget larger than its parent is dead configuration: the parent
cancels first, so the child's value is unreachable while still appearing set.
This was found in the real project config (retrieval ceiling 180s inside a
120s pipeline ceiling), where it made the retrieval budget meaningless.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from config import Settings, check_timeout_consistency, settings

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_shipped_configuration_has_no_unreachable_timeouts():
    assert check_timeout_consistency() == []


def test_retrieval_budget_exceeding_pipeline_budget_is_reported():
    # Relative to the configured pipeline ceiling, not a hard-coded number: the
    # point is "a child above its parent is reported", and pinning 180 only said
    # that 180 exceeds a 120 s parent -- which stopped being true the moment the
    # pipeline ceiling was raised to fit real retrieval times.
    over = settings.total_pipeline_timeout_seconds + 60.0
    broken = replace(settings, local_retrieval_timeout_seconds=over)
    problems = check_timeout_consistency(broken)
    assert len(problems) == 1
    assert "LOCAL_RETRIEVAL_TIMEOUT_SECONDS" in problems[0]
    assert f"{over:g}" in problems[0]


def test_retrieval_total_budget_exceeding_pipeline_budget_is_reported():
    broken = replace(
        settings,
        local_retrieval_total_timeout_seconds=settings.total_pipeline_timeout_seconds + 60.0,
    )
    problems = check_timeout_consistency(broken)
    assert any("LOCAL_RETRIEVAL_TOTAL_TIMEOUT_SECONDS" in item for item in problems)


def test_tool_budget_exceeding_pipeline_budget_is_reported():
    broken = replace(
        settings,
        tool_timeout_seconds=settings.total_pipeline_timeout_seconds + 60.0,
    )
    problems = check_timeout_consistency(broken)
    assert any("TOOL_TIMEOUT_SECONDS" in item for item in problems)


def test_equal_budgets_are_allowed():
    # A child equal to the parent is reachable, so it is not an error.
    boundary = replace(
        settings,
        local_retrieval_timeout_seconds=settings.total_pipeline_timeout_seconds,
    )
    assert check_timeout_consistency(boundary) == []


def test_multiple_problems_are_all_reported_not_just_the_first():
    base = settings.total_pipeline_timeout_seconds
    broken = replace(
        settings,
        local_retrieval_timeout_seconds=base + 1,
        local_retrieval_total_timeout_seconds=base + 2,
        tool_timeout_seconds=base + 3,
    )
    assert len(check_timeout_consistency(broken)) == 3


# ---------------------------------------------------------------------------
# The other half: a budget can also be too small to ever finish.
#
# `check_timeout_consistency` only catches a child budget that is *unreachable*
# because its parent cancels first.  Nothing caught the opposite failure, and it
# happened: the retrieval budget was lowered from 180 to 20 to satisfy the upper
# bound, and 20 s is far below what retrieval actually costs, so the app could
# not retrieve at all (`工具 local_retrieval 执行超过 20 秒`).
#
# The floors are the measured worst cases, not round numbers.  They are floors
# rather than equalities because a larger timeout is always safe; the failure
# being guarded is a budget nobody checked could finish.
# ---------------------------------------------------------------------------

# Single-query retrieval: 129 s mean / 195 s max over 35 questions (2026-09-24).
MIN_LOCAL_RETRIEVAL_SECONDS = 200.0
# The model-call ceiling: 60 s produced deterministic 60.0-62.5 s timeouts on the
# largest recorded contexts, in 2-3 of 3 identical runs.
MIN_LLM_TIMEOUT_SECONDS = 180.0


def test_the_retrieval_budget_can_actually_finish():
    assert settings.local_retrieval_timeout_seconds >= MIN_LOCAL_RETRIEVAL_SECONDS, (
        f"LOCAL_RETRIEVAL_TIMEOUT_SECONDS="
        f"{settings.local_retrieval_timeout_seconds:g}s 低于实测最坏值 "
        f"{MIN_LOCAL_RETRIEVAL_SECONDS:g}s，每次检索都会超时。"
        "上限检查通过不代表这个预算够用。"
    )


def test_the_model_call_ceiling_is_above_the_measured_wall():
    assert settings.llm_timeout_seconds >= MIN_LLM_TIMEOUT_SECONDS


def test_the_shipped_env_example_is_usable_not_just_consistent():
    """`.env.example` is the reproducibility contract for a fresh clone.

    A fresh clone following it has to get budgets that can finish, not merely
    budgets that nest correctly -- the two failures are independent, and the
    shipped example had both at once (20 s retrieval inside a 120 s pipeline,
    with a 60 s model ceiling that was already known to be a wall).
    """
    values: dict[str, str] = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, raw = stripped.partition("=")
        values[key.strip()] = raw.split("#")[0].strip()

    assert float(values["LOCAL_RETRIEVAL_TIMEOUT_SECONDS"]) >= MIN_LOCAL_RETRIEVAL_SECONDS
    assert (
        float(values["LOCAL_RETRIEVAL_TOTAL_TIMEOUT_SECONDS"])
        >= MIN_LOCAL_RETRIEVAL_SECONDS
    )
    assert float(values["LLM_TIMEOUT_SECONDS"]) >= MIN_LLM_TIMEOUT_SECONDS
    assert float(values["LOCAL_RETRIEVAL_TIMEOUT_SECONDS"]) <= float(
        values["TOTAL_PIPELINE_TIMEOUT_SECONDS"]
    )


# ---------------------------------------------------------------------------
# The ceiling has to travel with the run.
#
# A timeout rate is meaningless without the ceiling it was measured against:
# "4 of 36 calls timed out" means "raise the ceiling" at 180 s and "the service
# is broken" at 3600 s, and those have opposite remedies.  Re-aggregating runs
# that predate these fields yields `latency_ceiling_seconds: None`, which is
# exactly the unanswerable state this guards against.
# ---------------------------------------------------------------------------


def _args(**overrides):
    base = {"max_retries": 0, "max_evidence": 5, "max_chars_per_item": 600}
    base.update(overrides)
    return SimpleNamespace(**base)


def test_execution_parameters_record_the_ceiling_and_the_retry_policy():
    from eval_generation_replay import execution_parameters

    params = execution_parameters(_args())
    assert params["llm_timeout_seconds"] == int(settings.llm_timeout_seconds)
    assert params["generation_max_retries"] == 0


def test_the_recorded_parameter_set_is_exactly_these_four():
    """Pinned so a field cannot be dropped one at a time.

    A payload that silently loses the ceiling looks exactly like a healthy run,
    which is how the P0 runs ended up undiagnosable.
    """
    from eval_generation_replay import execution_parameters

    assert set(execution_parameters(_args())) == {
        "llm_timeout_seconds",
        "generation_max_retries",
        "max_evidence",
        "max_chars_per_item",
    }


def test_no_truncation_stays_none_and_is_not_coerced_to_a_number():
    """`None` is the meaning of the all-evidence arm, not a missing value.

    Coercing it to a number would silently describe a different experiment
    while still looking like a recorded parameter.
    """
    from eval_generation_replay import execution_parameters

    params = execution_parameters(_args(max_chars_per_item=None))
    assert params["max_chars_per_item"] is None
    assert params["max_evidence"] == 5


def test_a_committed_run_carries_the_parameters_it_was_scored_under():
    """Ties the function to reality: a real artifact must have the fields."""
    from eval_generation_replay import execution_parameters

    path = ROOT / "data" / "runs" / "mhreal_r1.json"
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    for key in execution_parameters(_args()):
        assert key in payload, f"运行产物缺少 {key}"
