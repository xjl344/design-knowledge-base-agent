import asyncio
from types import SimpleNamespace

from langchain_core.documents import Document

from src.generator import NO_EVIDENCE_ANSWER
from src.graph_builder import build_graph


def local_doc() -> Document:
    return Document(
        page_content="测试本地资料",
        metadata={"source_type": "local", "source": "guide.md", "title": "Guide"},
    )


def web_doc() -> Document:
    return Document(
        page_content="测试网络摘要",
        metadata={
            "source_type": "web",
            "source": "https://example.com",
            "title": "Example",
        },
    )


class FakeServices:
    def __init__(
        self,
        retrieved=None,
        relevant=None,
        searched=None,
        grade_error: Exception | None = None,
        search_error: Exception | None = None,
    ):
        self.retrieved = retrieved or []
        self.relevant = relevant or []
        self.searched = searched or []
        self.grade_error = grade_error
        self.search_error = search_error
        self.search_queries = []

    async def retrieve(self, question):
        return self.retrieved

    async def grade(self, question, documents):
        if self.grade_error:
            raise self.grade_error
        return self.relevant

    async def search(self, question):
        self.search_queries.append(question)
        if self.search_error:
            raise self.search_error
        return self.searched

    async def generate(self, question, documents):
        if not documents:
            return NO_EVIDENCE_ANSWER
        return f"answer from {documents[0].metadata['source_type']}"


def invoke(services: FakeServices):
    return asyncio.run(build_graph(services).ainvoke({"question": "test"}))


def test_local_hit_skips_web_search():
    document = local_doc()
    result = invoke(FakeServices(retrieved=[document], relevant=[document]))

    assert result["route"] == "local"
    assert result["web_search_triggered"] is False
    assert result["sources"][0]["id"] == "L1"


def test_irrelevant_local_documents_trigger_web_search():
    result = invoke(FakeServices(retrieved=[local_doc()], searched=[web_doc()]))

    assert result["route"] == "web"
    assert result["web_search_triggered"] is True
    assert [source["type"] for source in result["sources"]] == ["local", "web"]


def test_empty_knowledge_base_can_return_no_evidence():
    result = invoke(FakeServices())

    assert result["web_search_triggered"] is True
    assert result["sources"] == []
    assert result["generation"] == NO_EVIDENCE_ANSWER


def test_grading_failure_degrades_to_web_search():
    result = invoke(
        FakeServices(
            retrieved=[local_doc()],
            searched=[web_doc()],
            grade_error=RuntimeError("grader unavailable"),
        )
    )

    assert [source["type"] for source in result["sources"]] == ["local", "web"]
    assert any("相关性评分失败" in error for error in result["errors"])


def test_indirect_local_evidence_is_supplemented_and_preserved():
    local = Document(
        page_content="间接本地资料",
        metadata={
            "source_type": "local",
            "source": "guide.md",
            "title": "Guide",
            "retrieval_evidence_status": "indirect",
        },
    )
    services = FakeServices(retrieved=[local], relevant=[local], searched=[web_doc()])
    result = invoke(services)

    assert result["web_search_triggered"] is True
    assert [source["type"] for source in result["sources"]] == ["local", "web"]
    assert result["web_search_attempted"] is True
    assert len(services.search_queries) == 1


def test_search_failure_is_reported_without_fabricated_sources():
    result = invoke(FakeServices(search_error=RuntimeError("search unavailable")))

    assert result["sources"] == []
    assert result["generation"] == NO_EVIDENCE_ANSWER
    assert any("网络搜索失败" in error for error in result["errors"])


def test_web_policy_blocks_graph_search_without_calling_service():
    services = FakeServices(searched=[web_doc()])
    services.policy = SimpleNamespace(allow_web=False, allow_planning=True, max_web_queries=3, max_retrieval_tasks=3)
    result = invoke(services)
    assert result["web_search_triggered"] is False
    assert not services.search_queries
    assert any(item["status"] == "blocked" for item in result["execution_trace"] if item["node"] == "web_search")


def test_cylinder_calculation_route_does_not_retrieve_or_search():
    services = FakeServices(retrieved=[local_doc()], searched=[web_doc()])
    result = asyncio.run(build_graph(services).ainvoke({
        "question": "直筒杯内径65 mm、有效液高120 mm，按公式估算理想容量"
    }))
    assert result["route"] == "calculation"
    assert result["deliverable"] is True
    assert "398" in result["generation"]
    assert not services.search_queries


def test_complex_question_enters_planning_path():
    document = local_doc()
    result = invoke(FakeServices(retrieved=[document], relevant=[document]))
    # The simple test question remains backward-compatible with the CRAG path.
    assert result["question_type"] == "simple"


def test_planning_service_can_execute_and_aggregate():
    class PlanningServices(FakeServices):
        async def analyze_complexity(self, question):
            return {"type": "comparison", "sub_questions": ["材料 A", "材料 B"], "parallel": True}

        async def execute_subtasks(self, tasks):
            return [
                {**task, "status": "completed", "documents": [local_doc()], "error": None}
                for task in tasks
            ]

        async def aggregate_answers(self, question, results):
            return "aggregated answer"

    result = invoke(PlanningServices())
    assert result["question_type"] == "simple"  # invoke uses the fixed simple question


def test_generic_planning_graph_keeps_task_contract_and_evidence():
    class GenericPlanningServices(FakeServices):
        async def analyze_complexity(self, question):
            return {
                "type": "comparison",
                "question_type": "comparison",
                "problem_spec": {"intent": "comparison", "criteria": ["可靠性"], "explicit_references": []},
                "task_specs": [
                    {"id": "task-1", "operation": "retrieve", "query": "方案甲", "question": "方案甲", "dependencies": [], "evidence_requirements": ["方案甲资料"]},
                    {"id": "task-2", "operation": "retrieve", "query": "方案乙", "question": "方案乙", "dependencies": [], "evidence_requirements": ["方案乙资料"]},
                    {"id": "task-3", "operation": "synthesize", "query": "综合", "question": "综合", "dependencies": ["task-1", "task-2"], "evidence_requirements": ["可追溯结论"]},
                ],
            }

        async def execute_subtasks(self, tasks):
            return [
                {**task, "status": "completed", "documents": [local_doc()], "error": None, "tool_calls": []}
                for task in tasks
            ]

        async def audit_evidence(self, problem_spec, results):
            return {"matrix": [{"task_id": "task-1", "coverage": "covered", "sources": ["guide.md"]}]}

        async def aggregate_answers(self, question, results):
            return "generic aggregate"

    result = asyncio.run(build_graph(GenericPlanningServices()).ainvoke({"question": "比较方案甲和方案乙"}))
    assert result["generation"] == "generic aggregate"
    assert result["evidence_matrix"][0]["coverage"] == "covered"


def test_planning_evidence_gap_triggers_bounded_web_search_and_reaudit():
    indirect = Document(
        page_content="只有背景信息",
        metadata={
            "source_type": "local",
            "source": "background.md",
            "title": "Background",
            "retrieval_evidence_status": "indirect",
        },
    )
    searched = web_doc()

    class GapPlanningServices(FakeServices):
        async def analyze_complexity(self, question):
            return {
                "type": "comparison",
                "question_type": "comparison",
                "problem_spec": {"intent": "comparison", "criteria": ["可靠性"]},
                "task_specs": [
                    {
                        "id": "task-1",
                        "operation": "retrieve",
                        "objective": "收集直接可靠性依据",
                        "query": "方案可靠性测试",
                        "question": "方案可靠性测试",
                        "dependencies": [],
                        "evidence_requirements": ["可靠性测试"],
                        "priority": "high",
                    }
                ],
            }

        async def execute_subtasks(self, tasks):
            return [{**tasks[0], "status": "completed", "documents": [indirect], "error": None}]

        async def audit_evidence(self, problem_spec, results):
            has_web = any(
                document.metadata.get("source_type") == "web"
                for result in results
                for document in result.get("documents", [])
            )
            return {
                "matrix": [{
                    "task_id": "task-1",
                    "coverage": "partial" if not has_web else "covered",
                    "requirements": ["可靠性测试"],
                    "missing_reason": "缺少直接资料" if not has_web else None,
                }]
            }

        async def aggregate_answers(self, question, results):
            return "aggregated with supplemented evidence"

    services = GapPlanningServices(searched=[searched])
    result = asyncio.run(build_graph(services).ainvoke({"question": "比较两个方案的可靠性"}))

    assert result["web_search_triggered"] is True
    assert result["web_search_attempted"] is True
    assert len(services.search_queries) == 1
    assert any(source["type"] == "web" for source in result["sources"])
    assert any(item["node"] == "search_evidence_gaps" for item in result["execution_trace"])


def test_planning_covered_evidence_skips_web_search():
    document = Document(
        page_content="直接测试依据",
        metadata={
            "source_type": "local",
            "source": "direct.md",
            "title": "Direct",
            "retrieval_evidence_status": "direct",
        },
    )

    class CoveredPlanningServices(FakeServices):
        async def analyze_complexity(self, question):
            return {
                "type": "comparison",
                "question_type": "comparison",
                "problem_spec": {"intent": "comparison"},
                "task_specs": [{
                    "id": "task-1", "operation": "retrieve", "query": "直接依据",
                    "question": "直接依据", "dependencies": [],
                    "evidence_requirements": ["直接依据"], "priority": "high",
                }],
            }

        async def execute_subtasks(self, tasks):
            return [{**tasks[0], "status": "completed", "documents": [document], "error": None}]

        async def audit_evidence(self, problem_spec, results):
            return {"matrix": [{"task_id": "task-1", "coverage": "covered"}]}

        async def aggregate_answers(self, question, results):
            return "covered answer"

    services = CoveredPlanningServices(searched=[web_doc()])
    result = asyncio.run(build_graph(services).ainvoke({"question": "比较两个方案"}))

    assert result["web_search_triggered"] is False
    assert services.search_queries == []


def test_partial_local_answer_triggers_single_web_search_supplement():
    """When local direct hits still leave an evidence gap, fallback to web search once."""

    class PartialThenSupplementedServices(FakeServices):
        def __init__(self):
            super().__init__(retrieved=[local_doc()], relevant=[local_doc()], searched=[web_doc()])
            self.generate_calls = 0

        async def generate(self, question, documents):
            self.generate_calls += 1
            has_web = any(d.metadata.get("source_type") == "web" for d in documents)
            if not has_web:
                return "当前资料仅能确认折弯环节 [L1]。完整加工流程当前资料无法确认。"
            return (
                "本地知识库已确认内容：\n- 折弯环节说明。[L1]\n\n"
                "网络补充参考（需核验）：\n- 完整流程包含下料、切割、折弯与表面处理。[W1]"
            )

    services = PartialThenSupplementedServices()
    result = asyncio.run(build_graph(services).ainvoke({"question": "钣金的加工过程是什么？"}))

    assert services.generate_calls == 2
    assert len(services.search_queries) == 1
    assert result["web_search_triggered"] is True
    assert result["web_search_attempted"] is True
    assert [source["id"] for source in result["sources"]] == ["L1", "W1"]
    assert result["deliverable"] is True


def test_partial_local_answer_respects_allow_web_false():
    class GapLocalOnlyServices(FakeServices):
        async def generate(self, question, documents):
            return "当前资料仅能确认部分内容 [L1]。其余流程当前资料无法确认。"

    services = GapLocalOnlyServices(retrieved=[local_doc()], relevant=[local_doc()], searched=[web_doc()])
    services.policy = SimpleNamespace(allow_web=False, allow_planning=True, max_web_queries=3, max_retrieval_tasks=3)
    result = asyncio.run(build_graph(services).ainvoke({"question": "钣金的加工过程是什么？"}))

    assert result["web_search_triggered"] is False
    assert services.search_queries == []


def test_web_query_normalization_strips_conversational_fillers():
    from src.web_search import _normalize_web_query

    assert _normalize_web_query("钣金的加工过程是什么？") == "钣金加工过程"
    assert _normalize_web_query("CNC 铝件表面处理有哪些？") == "CNC 铝件表面处理"
    assert (
        _normalize_web_query("MIM金属注射成型适合生产什么零件？；重点评价：能否明确说明零件类型")
        == "MIM金属注射成型适合生产什么零件"
    )


def test_grader_partial_coverage_triggers_web_search_before_generate():
    """Grader sufficiency assessment routes partial local hits to web_search BEFORE generate."""
    from src.grader import GradingAssessment

    class SufficiencyServices(FakeServices):
        def __init__(self):
            super().__init__(retrieved=[local_doc()], relevant=[local_doc()], searched=[web_doc()])
            self.generate_calls = 0

        async def grade_with_assessment(self, question, documents):
            return GradingAssessment(
                documents=documents,
                coverage="partial",
                missing_aspects="缺少完整工艺步骤顺序",
                search_query="钣金加工 完整工艺流程",
            )

        async def generate(self, question, documents):
            self.generate_calls += 1
            return "折弯说明 [L1]。完整流程包括下料、切割、折弯与表面处理 [W1]。"

    services = SufficiencyServices()
    result = asyncio.run(build_graph(services).ainvoke({"question": "钣金的加工过程是什么？"}))

    assert services.generate_calls == 1, "不应在联网补充前白跑一次 generate"
    assert services.search_queries == ["钣金加工 完整工艺流程"]
    assert result["web_search_triggered"] is True
    assert result["deliverable"] is True
    assert [source["id"] for source in result["sources"]] == ["L1", "W1"]


def test_search_evidence_gaps_without_deadline_uses_concise_query():
    """Complex/planned branch must not time out when web_fallback_deadline is unset."""
    from src.tools import ToolResult

    class ToolGapPlanningServices(FakeServices):
        async def call_tool(self, name, **kwargs):
            self.search_queries.append(kwargs.get("question", ""))
            return ToolResult(success=True, data=[web_doc()], duration_seconds=0.1, attempts=1)

        async def analyze_complexity(self, question):
            return {
                "type": "comparison",
                "question_type": "comparison",
                "problem_spec": {"intent": "comparison", "criteria": ["耐腐蚀", "成本"]},
                "task_specs": [
                    {
                        "id": "task-1",
                        "operation": "retrieve",
                        "objective": "收集电镀和阳极氧化对比依据",
                        "query": "电镀 阳极氧化 区别",
                        "question": "电镀 阳极氧化 区别",
                        "dependencies": [],
                        "evidence_requirements": ["耐腐蚀", "成本"],
                        "priority": "high",
                    }
                ],
            }

        async def execute_subtasks(self, tasks):
            return [{**tasks[0], "status": "completed", "documents": [], "error": None}]

        async def audit_evidence(self, problem_spec, results):
            has_web = any(
                d.metadata.get("source_type") == "web"
                for r in results
                for d in r.get("documents", [])
            )
            return {
                "matrix": [{
                    "task_id": "task-1",
                    "coverage": "covered" if has_web else "missing",
                    "missing_reason": None if has_web else "缺少对比资料",
                }]
            }

        async def aggregate_answers(self, question, results):
            return "电镀与阳极氧化在机理与基材适用性上不同 [W1]。"

    services = ToolGapPlanningServices()
    result = asyncio.run(build_graph(services).ainvoke({"question": "电镀和阳极氧化有什么区别？"}))

    assert services.search_queries == ["电镀 阳极氧化 区别"]
    assert result["web_search_triggered"] is True
    assert not result.get("web_search_errors")
    assert result["deliverable"] is True
