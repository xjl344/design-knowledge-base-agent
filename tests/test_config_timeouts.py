"""Guards against timeout settings that can never take effect.

A nested budget larger than its parent is dead configuration: the parent
cancels first, so the child's value is unreachable while still appearing set.
This was found in the real project config (retrieval ceiling 180s inside a
120s pipeline ceiling), where it made the retrieval budget meaningless.
"""

from __future__ import annotations

from dataclasses import replace

from config import Settings, check_timeout_consistency, settings


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
