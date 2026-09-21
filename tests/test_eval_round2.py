from types import SimpleNamespace

from eval_round2 import (
    RESULT_FIELDS,
    EVALUATION_PROFILES,
    effective_config,
    validate_experiment,
    aggregate_metrics,
    answer_structure_evaluator,
    delivery_contract_evaluator,
    deterministic_scores,
    expected_rule,
)
from src.eval_contracts import CallCounter, DegradationReason, classify_degradation, reserve_budget
from eval_round2 import _execution_summary


def test_profiles_make_local_mode_explicitly_offline_and_planner_free():
    profile = EVALUATION_PROFILES["A_local_single"]
    assert profile.allow_web is False
    assert profile.allow_planning is False
    assert profile.offline_mode is True
    assert profile.allow_web_cache is False
    assert profile.max_retrieval_tasks == 1
    assert profile.max_web_queries == 0
    assert effective_config(profile)["query_decompose_enabled"] is False


def test_validate_experiment_rejects_planner_or_multiple_retrievals_in_a():
    rows = [{
        "question_id": "r2-01",
        "execution_summary": {
            "planner_calls": {"attempted": 1},
            "web_search_calls": {"attempted": 0},
            "retrieval_calls": {"attempted": 2},
            "web_source_count": 0,
        },
    }]
    result = validate_experiment(EVALUATION_PROFILES["A_local_single"], rows, [{"id": "r2-01"}])
    assert result["experiment_status"] == "invalid_baseline"


def test_validate_experiment_requires_completed_web_source_for_c():
    rows = [{
        "question_id": "r2-01",
        "execution_summary": {
            "planner_calls": {"attempted": 1},
            "web_search_calls": {"attempted": 1, "completed": 1},
            "retrieval_calls": {"attempted": 1},
            "web_source_count": 0,
        },
    }]
    result = validate_experiment(EVALUATION_PROFILES["C_controlled_web"], rows, [{"id": "r2-01"}])
    assert result["experiment_status"] == "invalid_baseline"


def test_call_counter_invariants_and_skipped_tasks():
    counter = CallCounter(scheduled=3, attempted=2, completed=0, timeout=1, failed=1, skipped=1)
    counter.validate()
    assert counter.as_dict()["skipped"] == 1


def test_timeout_is_infra_even_without_completed_retrieval():
    result = classify_degradation(
        {
            "planner": CallCounter(scheduled=1, attempted=1, timeout=1),
            "retrieval": CallCounter(scheduled=3, attempted=0, skipped=3),
        },
        "insufficient_evidence",
        None,
    )
    assert result is DegradationReason.INFRA_TIMEOUT


def test_reserve_budget_keeps_web_fallback_window():
    local, fallback = reserve_budget(120.0, 15.0, 0.0)
    assert local == 105.0
    assert fallback == 120.0


def test_execution_summary_accepts_call_counter_objects():
    summary = _execution_summary(
        {"deliverable": False, "tool_calls": [], "model_calls": [], "blocking_issues": []},
        EVALUATION_PROFILES["A_local_single"],
        [],
        [],
    )
    assert summary["web_search_calls"]["attempted"] == 0
    assert summary["degradation_reason"] == "infra_timeout"


def test_metrics_split_evidence_degradation_from_infrastructure_timeout():
    questions = [
        {"id": "evidence", "expected_outcome": "deliver"},
        {"id": "timeout", "expected_outcome": "deliver"},
    ]
    rows = [
        {"question_id": "evidence", "deliverable": False, "delivery_status": "degraded_insufficient_evidence", "blocking_issues": [{"status": "indirect_evidence"}], "allowed_citations": [], "duration_seconds": 1},
        {"question_id": "timeout", "deliverable": False, "delivery_status": "degraded_infra_timeout", "blocking_issues": [], "errors": ["pipeline timeout"], "timeout_stage": "total_pipeline", "allowed_citations": [], "duration_seconds": 20},
    ]
    metrics = aggregate_metrics(rows, questions)
    assert metrics["evidence_insufficiency_degraded_rate"] == 0.5
    assert metrics["infra_timeout_degraded_rate"] == 0.5
    assert metrics["sample_size"] == 2


def example(expected_mode, focus=None):
    return SimpleNamespace(
        inputs={"question": "测试问题"},
        outputs={"expected_mode": expected_mode, "focus": focus or [], "category": "测试", "difficulty": "L2"},
    )


def run(deliverable=True, status="completed", answer="有条件地建议，并列出风险和验证计划。"):
    return {
        "answer": answer,
        "deliverable": deliverable,
        "delivery_status": status,
        "blocking_issues": [] if deliverable else [{"status": "indirect_evidence"}],
        "allowed_citations": ["L1"],
        "requirement_coverage": 1.0,
        "errors": [],
    }


def test_expected_rule_does_not_treat_conditional_recommendation_as_refusal():
    assert expected_rule("若缺官方牌号资料则拒答/降级；资料完整时条件化推荐") == "conditional_or_refuse"


def test_expected_rule_requires_refusal_for_invalid_citation_case():
    assert expected_rule("必须拒绝无效引用，不展示伪造的正式结论") == "must_refuse"


def test_delivery_contract_accepts_conditional_success():
    result = delivery_contract_evaluator(
        run(),
        example("若缺官方牌号资料则拒答/降级；资料完整时条件化推荐"),
    )
    assert result["score"] == 1.0


def test_delivery_contract_requires_degraded_refusal():
    result = delivery_contract_evaluator(
        run(deliverable=True),
        example("必须拒绝无效引用，不展示伪造的正式结论"),
    )
    assert result["score"] == 0.0


def test_answer_structure_checks_formula_and_assumptions():
    result = answer_structure_evaluator(
        run(answer="公式：V=πd²h/4。单位换算后，假设杯体为直筒。"),
        example("可交付，需公开公式和假设", ["公式", "单位换算"]),
    )
    assert result["score"] == 1.0


def test_deterministic_scores_and_result_schema_are_exportable():
    item = {
        "id": "r2-01",
        "question": "测试问题",
        "type": "事实问答",
        "difficulty": "L2",
        "expected_mode": "可交付或条件化交付",
        "focus": ["直接引用"],
    }
    result = run()
    result.update({"question_id": "r2-01", "question": item["question"], "category": item["type"], "difficulty": item["difficulty"], "expected_mode": item["expected_mode"]})
    scores = deterministic_scores(result, item)
    assert {"delivery_contract", "citation_validity", "evidence_coverage", "answer_structure", "failure_diagnosis"} <= set(scores)
    assert {"answer", "blocking_issues", "allowed_citations", "evidence_matrix", "execution_trace", "duration_seconds"} <= set(RESULT_FIELDS)


def test_aggregate_metrics_separates_delivery_and_refusal():
    questions = [
        {"id": "deliver", "expected_mode": "可交付"},
        {"id": "refuse", "expected_mode": "必须拒绝无效引用"},
    ]
    rows = [
        {"question_id": "deliver", "deliverable": True, "delivery_status": "completed", "requirement_coverage": 1.0, "blocking_issues": [], "allowed_citations": [], "duration_seconds": 2},
        {"question_id": "refuse", "deliverable": False, "delivery_status": "degraded_insufficient_evidence", "blocking_issues": [{"status": "citation_invalid"}], "allowed_citations": [], "duration_seconds": 3},
    ]
    metrics = aggregate_metrics(rows, questions)
    assert metrics["normal_delivery_rate"] == 1.0
    assert metrics["correct_refusal_rate"] == 1.0
    assert metrics["contract_compliance_rate"] == 1.0
