from src.planning import build_problem_spec, build_task_specs, classify_question


def test_design_question_is_treated_as_complex():
    assert classify_question("成人500 mL水杯设计") == "complex"


def test_design_question_generates_generic_design_facets():
    spec = build_problem_spec("成人500 mL水杯设计")
    tasks = build_task_specs("成人500 mL水杯设计", spec)
    queries = [task["query"] for task in tasks if task.get("operation") == "retrieve"]

    assert any("手部尺寸" in query for query in queries)
    assert any("材料" in query for query in queries)
    assert any("结构" in query for query in queries)
    assert any("工艺" in query for query in queries)
    assert any("测试" in query for query in queries)
    assert tasks[-1]["operation"] in {"synthesize", "recommend"}


def test_design_plan_requires_direct_evidence_types():
    spec = build_problem_spec("设计一种产品的尺寸、材料、结构和验证方案")
    requirements = spec["evidence_requirements"]

    assert "直接握持或操作研究" in requirements
    assert "具体牌号 TDS" in requirements
    assert "失效模式" in requirements
    assert "DFM" in requirements
