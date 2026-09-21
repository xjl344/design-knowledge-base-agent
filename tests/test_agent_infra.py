import asyncio

from langchain_core.documents import Document

from src.checkpoint import CheckpointManager
from src.planning import build_problem_spec, build_task_specs, classify_question, heuristic_plan, parse_structured_analysis
from src.resilience import CircuitBreaker, CircuitOpenError, CostLimit, CostTracker, is_permanent_model_error
from src.tools import Tool, ToolRegistry
from src.evidence import audit_claims, classify_claim, detect_source_conflicts, extract_claims
from src.evaluation import decision_quality_metrics
from src.model_clients import model_descriptor


def test_planning_classifies_comparison_and_creates_bounded_tasks():
    assert classify_question("对比方案 A、方案 B、方案 C 的优劣") == "comparison"
    plan = heuristic_plan("对比方案 A、方案 B、方案 C 哪个更适合当前目标")
    assert plan["parallel"] is True
    assert plan["problem_spec"]["candidates"]
    assert all("operation" in task for task in plan["task_specs"])


def test_numeric_capacity_question_stays_on_simple_path():
    assert classify_question("尺寸计算问题：直筒杯内径 65 mm、有效液高 120 mm，估算理想容量") == "simple"


def test_local_mode_routes_generator_to_ollama(monkeypatch):
    monkeypatch.setenv("RAG_LOCAL_MODEL", "true")
    monkeypatch.setenv("LOCAL_GENERATOR_MODEL", "qwen2.5:7b-instruct")
    descriptor = model_descriptor("generator")
    assert descriptor == {"role": "generator", "backend": "local", "model": "qwen2.5:7b-instruct"}


def test_planning_contract_is_domain_agnostic():
    question = "比较两种方案在成本、可靠性方面的差异，并给出推荐"
    spec = build_problem_spec(question)
    tasks = build_task_specs(question, spec)
    assert spec["intent"] == "comparison"
    assert "成本" in spec["criteria"]
    assert any(task["operation"] == "synthesize" for task in tasks)


def test_structured_planner_response_is_normalized_without_domain_rules():
    raw = '{"intent":"recommendation","object":"方案","task_mode":"recommendation","constraints":["约束"],"criteria":["可靠性"],"requested_outputs":["推荐"],"explicit_references":[],"candidates":["甲","乙"],"assumptions":[],"confidence":0.8}'
    plan = parse_structured_analysis(raw, "请在方案甲和方案乙中推荐可靠性更高的方案")
    assert plan["question_type"] == "recommendation"
    assert plan["problem_spec"]["criteria"] == ["可靠性"]
    assert all(task["operation"] in {"retrieve", "analyze", "recommend"} for task in plan["task_specs"])


def test_tool_registry_retries_and_returns_call_metadata():
    attempts = 0

    async def flaky(value: str):
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise RuntimeError("temporary")
        return value.upper()

    async def run():
        registry = ToolRegistry()
        registry.register(Tool("flaky", "test", flaky, {"value": str}, max_retries=2))
        return await registry.call("flaky", value="ok")

    result = asyncio.run(run())
    assert result.success is True
    assert result.data == "OK"
    assert result.attempts == 2
    assert result.call_id


def test_tool_registry_times_out_and_reports_failure():
    async def slow():
        await asyncio.sleep(0.05)

    async def run():
        registry = ToolRegistry()
        registry.register(Tool("slow", "test", slow, timeout_seconds=0.001, max_retries=0))
        return await registry.call("slow")

    result = asyncio.run(run())
    assert result.success is False
    assert result.attempts == 1
    assert "超过" in (result.error or "")


def test_tool_registry_blocks_disabled_web_search():
    async def run():
        registry = ToolRegistry(disabled_tools={"web_search"})
        registry.register(Tool("web_search", "test", lambda question: question, {"question": str}))
        return await registry.call("web_search", question="test")

    result = asyncio.run(run())
    assert result.success is False
    assert "web_disabled" in (result.error or "")


def test_circuit_breaker_opens_after_failures():
    async def fail():
        raise RuntimeError("down")

    async def run():
        breaker = CircuitBreaker(failure_threshold=2, recovery_seconds=60)
        for _ in range(2):
            try:
                await breaker.call(fail)
            except RuntimeError:
                pass
        try:
            await breaker.call(fail)
        except CircuitOpenError:
            return True
        return False

    assert asyncio.run(run()) is True


def test_permanent_account_errors_do_not_open_circuit_breaker():
    assert is_permanent_model_error(RuntimeError("Error code: 402 Insufficient Balance"))

    async def fail_permanently():
        raise RuntimeError("Error code: 402 Insufficient Balance")

    async def run():
        breaker = CircuitBreaker(failure_threshold=1)
        for _ in range(3):
            try:
                await breaker.call(fail_permanently)
            except RuntimeError:
                pass
        return breaker.state

    assert asyncio.run(run()).value == "closed"


def test_cost_tracker_rejects_per_query_limit():
    tracker = CostTracker(CostLimit(per_query_tokens=10, daily_tokens=100))
    try:
        tracker.check_and_record(11)
    except RuntimeError as exc:
        assert "单次查询" in str(exc)
    else:
        raise AssertionError("expected per-query limit")


def test_checkpoint_round_trip(tmp_path):
    manager = CheckpointManager(tmp_path)
    state = {"question": "test", "documents": [Document(page_content="x")]}
    manager.save("session-1", state)
    loaded = manager.load("session-1")
    assert loaded["question"] == "test"
    assert "Document" in loaded["documents"][0]


def test_claim_audit_distinguishes_facts_inferences_and_unsupported_claims():
    document = Document(page_content="资料明确说明方案具有较高强度。", metadata={"source_type": "local", "source": "guide.md"})
    answer = "资料明确说明方案具有较高强度 [L1]。因此建议优先选择该方案。"
    result = audit_claims(answer, [document], {})
    assert classify_claim("因此建议优先选择该方案") == "design_inference"
    assert len(result["claims"]) == 2
    assert result["claims"][0]["evidence_status"] == "supported"
    assert result["unsupported_claims"]


def test_claim_audit_detects_scope_mismatch():
    document = Document(
        page_content="资料内容",
        metadata={"source_type": "local", "source": "adult.md", "scope": "成年人尺寸"},
    )
    result = audit_claims("该结论适用于未成年人 [L1]。", [document], {})
    assert result["claims"][0]["evidence_status"] == "scope_mismatch"


def test_source_conflict_is_flagged_without_selecting_a_winner():
    documents = [
        Document(page_content="方案的适用温度为 80℃。", metadata={"source": "a.md", "source_type": "local"}),
        Document(page_content="方案的适用温度为 100℃。", metadata={"source": "b.md", "source_type": "local"}),
    ]
    conflicts = detect_source_conflicts(documents)
    assert conflicts
    assert conflicts[0]["status"] == "potential_conflict"


def test_recommendation_is_conditional_and_traceable():
    document = Document(page_content="资料说明方案具有较高可靠性。", metadata={"source_type": "local", "source": "guide.md"})
    result = audit_claims(
        "因此建议优先选择方案甲 [L1]。",
        [document],
        {"criteria": ["可靠性"], "constraints": ["成本受限"], "assumptions": ["使用环境稳定"]},
    )
    condition = result["recommendation_conditions"][0]
    assert "可靠性" in condition["conditions"]
    assert "成本受限" in condition["conditions"]
    assert condition["reversible"] is True


def test_decision_quality_metrics_are_computed():
    audit = audit_claims("资料事实：方案可靠性较高 [L1]。建议优先选择方案。", [
        Document(page_content="资料", metadata={"source_type": "local", "source": "guide.md"})
    ], {"criteria": ["可靠性"]})
    metrics = decision_quality_metrics(audit)
    assert 0 <= metrics["claim_support_rate"] <= 1
    assert "recommendation_traceability" in metrics


def test_markdown_headings_and_reference_only_lines_are_not_claims():
    result = extract_claims("## 四、设计建议\n\n资料事实：参数为 10 mm [L1]。\n[W1][W2]")
    assert len(result) == 1
    assert result[0]["claim_role"] == "substantive"


def test_refusal_claim_is_not_marked_as_unconditional_recommendation():
    result = audit_claims(
        "因此，无法科学地给出儿童水杯的最佳握持直径精确值。",
        [],
        {},
    )
    assert result["claims"][0]["claim_role"] == "refusal"
    assert not any(item["status"] == "recommendation_unconditional" for item in result["warnings"])
