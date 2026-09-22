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
    broken = replace(settings, local_retrieval_timeout_seconds=180.0)
    problems = check_timeout_consistency(broken)
    assert len(problems) == 1
    assert "LOCAL_RETRIEVAL_TIMEOUT_SECONDS" in problems[0]
    assert "180" in problems[0]


def test_retrieval_total_budget_exceeding_pipeline_budget_is_reported():
    broken = replace(settings, local_retrieval_total_timeout_seconds=999.0)
    problems = check_timeout_consistency(broken)
    assert any("LOCAL_RETRIEVAL_TOTAL_TIMEOUT_SECONDS" in item for item in problems)


def test_tool_budget_exceeding_pipeline_budget_is_reported():
    broken = replace(settings, tool_timeout_seconds=999.0)
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
    broken = replace(
        settings,
        local_retrieval_timeout_seconds=200.0,
        local_retrieval_total_timeout_seconds=300.0,
        tool_timeout_seconds=400.0,
    )
    assert len(check_timeout_consistency(broken)) == 3


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
