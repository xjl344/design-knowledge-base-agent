"""Domain-agnostic problem understanding and task planning.

The planner deliberately knows nothing about a particular product, material,
standard, or design discipline. It extracts a reusable task contract from the
user question and leaves domain vocabulary to the indexed documents.
"""

from __future__ import annotations

import re
import uuid
import json
from typing import Any, TypedDict


class ProblemSpec(TypedDict, total=False):
    intent: str
    object: str
    task_mode: str
    constraints: list[str]
    criteria: list[str]
    requested_outputs: list[str]
    explicit_references: list[str]
    candidates: list[str]
    assumptions: list[str]
    evidence_requirements: list[str]
    confidence: float


class TaskSpec(TypedDict, total=False):
    id: str
    operation: str
    objective: str
    query: str
    question: str
    dependencies: list[str]
    evidence_requirements: list[str]
    priority: str


_COMPARISON_MARKERS = ("对比", "比较", "区别", "优劣", "差异")
_RECOMMENDATION_MARKERS = ("推荐", "选择", "适合", "应该用", "怎么选", "方案")
_ANALYSIS_MARKERS = ("分析", "评估", "如何", "为什么", "要求", "影响", "需要考虑")
_DESIGN_TASK_MARKERS = ("设计", "尺寸", "材料", "结构", "工艺", "验证", "测试", "选型", "选材")
_CALCULATION_MARKERS = ("计算", "估算", "容量", "体积", "面积", "换算")
_DESIGN_FACETS = (
    (
        "收集尺寸与人体工学依据",
        "人体工学 手部尺寸 握持直径 杯径 杯体高度 杯口尺寸",
        ["目标人群及百分位数据", "直接握持或操作研究", "容量与尺寸计算输入", "稳定性或可用性验证"],
    ),
    (
        "收集材料与合规依据",
        "材料 牌号 食品接触 性能 限制",
        ["具体牌号 TDS", "食品接触法规或合规文件", "加工窗口", "老化、清洗和使用条件限制"],
    ),
    (
        "收集结构与接口依据",
        "结构 接口 密封 开合 装配",
        ["接口尺寸", "密封与装配依据", "失效模式", "结构验证方法"],
    ),
    (
        "收集制造工艺依据",
        "制造工艺 成型 加工 DFM 工艺限制",
        ["DFM", "壁厚与收缩", "残余应力", "制造窗口和过程控制"],
    ),
    (
        "收集测试验证依据",
        "测试 验证 可靠性 风险 失效",
        ["跌落、泄漏或强度测试", "冷热循环或热冲击", "清洗/洗碗机条件", "食品接触与耐久验证"],
    ),
)
_REFERENCE_PATTERN = re.compile(
    r"(?:GB/T|ISO|IEC|ASTM|EN|DIN|JIS|RFC)\s*[A-Za-z0-9./—\-]+",
    flags=re.IGNORECASE,
)


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        value = re.sub(r"\s+", " ", value).strip(" ，,。；;:：")
        if value and value not in result:
            result.append(value)
    return result


def classify_question(question: str) -> str:
    """Classify intent using generic language signals only."""
    normalized = question.strip()
    if any(marker in normalized for marker in _COMPARISON_MARKERS):
        return "comparison"
    if any(marker in normalized for marker in _RECOMMENDATION_MARKERS):
        return "recommendation"
    # Numeric lookups/calculations should stay on the single-pass CRAG path.
    # Otherwise a word such as "尺寸" upgrades a straightforward calculation
    # into a multi-task design plan, multiplying slow local retrieval calls.
    if (
        any(marker in normalized for marker in _CALCULATION_MARKERS)
        and re.search(r"\d", normalized)
        and not any(marker in normalized for marker in _ANALYSIS_MARKERS)
    ):
        return "simple"
    if (
        any(marker in normalized for marker in _ANALYSIS_MARKERS)
        or any(marker in normalized for marker in _DESIGN_TASK_MARKERS)
        or normalized.count("、") >= 2
    ):
        return "complex"
    return "simple"


def _extract_candidates(question: str) -> list[str]:
    """Extract explicit alternatives without assuming their domain."""
    segments = re.split(r"(?:、|,|，|和|与|还是)", question)
    candidates: list[str] = []
    for segment in segments:
        segment = segment.strip(" ，,。；;:：")
        if 1 < len(segment) <= 40 and not any(
            marker in segment for marker in ("对比", "比较", "哪个", "哪些", "请", "是否")
        ):
            candidates.append(segment)
    return _unique(candidates[:6])


def _extract_criteria(question: str) -> list[str]:
    patterns = (
        r"(?:从|按|按照|根据|考虑|比较|评价|关注)([^。！？?！]*)",
        r"(?:在|以)([^。！？?！]*?)(?:方面|条件|要求)",
    )
    values: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, question):
            values.extend(re.split(r"、|,|，|和|与|及", match))
    return _unique(values)[:8]


def _extract_object(question: str, candidates: list[str]) -> str:
    cleaned = question
    for candidate in candidates:
        cleaned = cleaned.replace(candidate, "")
    cleaned = re.sub(r"[，,、。；;:：?!？！]", " ", cleaned)
    cleaned = re.sub(
        r"(请|帮我|综合|比较|对比|分析|评估|推荐|选择|给出|说明|需要|应该|如何|哪个|哪些|为什么)",
        " ",
        cleaned,
    )
    words = [part.strip() for part in cleaned.split() if len(part.strip()) > 1]
    return " ".join(words[:10]).strip()


def _is_design_task(question: str, spec: ProblemSpec) -> bool:
    text = f"{question} {spec.get('object', '')} {' '.join(spec.get('criteria', []))}"
    return any(marker in text for marker in _DESIGN_TASK_MARKERS)


def build_problem_spec(question: str) -> ProblemSpec:
    intent = classify_question(question)
    candidates = _extract_candidates(question) if intent == "comparison" else []
    references = _unique(_REFERENCE_PATTERN.findall(question))
    criteria = _extract_criteria(question)
    outputs: list[str] = []
    if intent == "comparison":
        outputs.extend(["比较结果", "差异与取舍"])
    if intent == "recommendation":
        outputs.extend(["推荐方案", "推荐依据", "风险与限制"])
    if not outputs:
        outputs.append("基于资料的直接回答")
    if references:
        outputs.append("引用指定来源")
    object_text = _extract_object(question, candidates)
    evidence_requirements: list[str] = []
    if _is_design_task(question, {"object": object_text, "criteria": criteria}):
        evidence_requirements = _unique([
            requirement
            for _, _, requirements in _DESIGN_FACETS
            for requirement in requirements
        ])
    return {
        "intent": intent,
        "task_mode": intent,
        "object": object_text,
        "constraints": [],
        "criteria": criteria,
        "requested_outputs": _unique(outputs),
        "explicit_references": references,
        "candidates": candidates,
        "assumptions": [],
        "evidence_requirements": evidence_requirements,
        "confidence": 0.65 if intent != "simple" else 0.9,
    }


def build_task_specs(question: str, spec: ProblemSpec) -> list[TaskSpec]:
    """Create a bounded generic task graph from the extracted contract."""
    intent = spec.get("intent", "simple")
    criteria = spec.get("criteria", [])
    references = spec.get("explicit_references", [])
    tasks: list[TaskSpec] = []


    def add(operation: str, objective: str, query: str, evidence: list[str], dependencies: list[str] | None = None, priority: str = "normal"):
        task_id = f"task-{len(tasks) + 1}"
        tasks.append({
            "id": task_id,
            "operation": operation,
            "objective": objective,
            "query": query,
            "question": query,
            "dependencies": dependencies or [],
            "evidence_requirements": evidence,
            "priority": priority,
        })
        return task_id

    if intent == "simple":
        return []

    candidates = spec.get("candidates", [])
    design_task = _is_design_task(question, spec)
    if intent == "comparison" and candidates:
        for candidate in candidates:
            add(
                "retrieve",
                f"收集候选项“{candidate}”与问题相关的事实和限制",
                f"{candidate} 的相关性能、特征、应用条件和限制",
                [f"候选项 {candidate} 的可核验资料"],
                priority="high",
            )
    else:
        add(
            "retrieve",
            "收集回答问题所需的直接事实和背景依据",
            question,
            ["能够直接支持问题答案的相关资料"],
            priority="high",
        )

    if design_task and intent in {"complex", "recommendation"}:
        for objective, facet_query, evidence in _DESIGN_FACETS:
            add(
                "retrieve",
                objective,
                f"{question}；{facet_query}",
                evidence,
                priority="high",
            )

    if criteria:
        add(
            "compare" if intent == "comparison" else "analyze",
            "按用户提出的评价维度整理证据并识别取舍",
            f"{question}；重点评价：{'、'.join(criteria)}",
            [f"评价维度：{criterion}" for criterion in criteria],
            dependencies=[task["id"] for task in tasks],
            priority="high",
        )

    if references:
        add(
            "validate",
            "核验用户明确指定的来源是否包含可引用的直接依据",
            "；".join(references) + " 相关条款、数据或适用范围",
            [f"指定来源：{reference}" for reference in references],
            priority="high",
        )

    synthesis_dependencies = [task["id"] for task in tasks]
    add(
        "recommend" if intent == "recommendation" else "synthesize",
        "综合已验证证据，形成面向用户目标的回答和限制说明",
        question,
        ["结论能够追溯到已完成任务和来源"],
        dependencies=synthesis_dependencies,
        priority="high",
    )
    if len(tasks) <= 8:
        return tasks
    return tasks[:7] + [tasks[-1]]


def heuristic_plan(question: str) -> dict[str, Any]:
    """Return the new contract plus legacy fields for existing callers."""
    spec = build_problem_spec(question)
    task_specs = build_task_specs(question, spec)
    return {
        "type": spec.get("intent", "simple"),
        "question_type": spec.get("intent", "simple"),
        "parallel": bool(task_specs),
        "problem_spec": spec,
        "task_specs": task_specs,
        "sub_questions": [task["query"] for task in task_specs if task.get("operation") == "retrieve"],
    }


def make_plan(question: str, analysis: dict[str, Any]) -> dict[str, Any]:
    task_specs = analysis.get("task_specs") or build_task_specs(question, analysis.get("problem_spec", build_problem_spec(question)))
    return {
        "plan_id": str(uuid.uuid4()),
        "question_type": analysis.get("question_type", analysis.get("type", "simple")),
        "problem_spec": analysis.get("problem_spec", build_problem_spec(question)),
        "task_specs": task_specs,
        "sub_tasks": task_specs,
        "parallel": bool(task_specs),
    }


def parse_structured_analysis(raw: str, question: str) -> dict[str, Any]:
    """Validate an LLM JSON response and rebuild tasks from the safe contract."""
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        raise ValueError("planner did not return a JSON object")
    payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("planner response must be an object")
    fallback = build_problem_spec(question)
    spec: ProblemSpec = {}
    for key in ("intent", "object", "task_mode"):
        if isinstance(payload.get(key), str) and payload[key].strip():
            spec[key] = payload[key].strip()
    for key in ("constraints", "criteria", "requested_outputs", "explicit_references", "candidates", "assumptions", "evidence_requirements"):
        value = payload.get(key, fallback.get(key, []))
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"planner field {key} must be a list of strings")
        spec[key] = _unique(value)[:8]
    # Preserve explicit entities from the user even if the model omitted them.
    spec["explicit_references"] = _unique(
        list(spec.get("explicit_references", [])) + list(fallback.get("explicit_references", []))
    )[:8]
    if not spec.get("candidates"):
        spec["candidates"] = list(fallback.get("candidates", []))
    spec["intent"] = spec.get("intent", fallback["intent"])
    spec["task_mode"] = spec.get("task_mode", spec["intent"])
    spec["object"] = spec.get("object", fallback.get("object", ""))
    spec["confidence"] = float(payload.get("confidence", 0.5))
    if fallback["intent"] != "simple" and spec["intent"] == "simple":
        spec["intent"] = fallback["intent"]
        spec["task_mode"] = spec["intent"]
    if spec["intent"] == "simple":
        return {"type": "simple", "question_type": "simple", "problem_spec": spec, "task_specs": [], "sub_questions": [], "parallel": False}
    task_specs = build_task_specs(question, spec)
    return {
        "type": spec["intent"],
        "question_type": spec["intent"],
        "problem_spec": spec,
        "task_specs": task_specs,
        "sub_questions": [task["query"] for task in task_specs if task.get("operation") == "retrieve"],
        "parallel": bool(task_specs),
    }
