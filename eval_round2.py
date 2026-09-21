"""Run reproducible local/controlled evaluation profiles.

LangSmith upload is deliberately opt-in.  The default runner is local-only so
``with_web=false`` also means no external telemetry or historical web cache.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
import sys
import time
import subprocess
import importlib.metadata
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from dataclasses import dataclass
from typing import Any
from src.eval_contracts import CallCounter, DegradationReason, classify_degradation, counter_from_tool_calls, reserve_budget

DATASET_NAME = "design-knowledge-qa-round2-8"
@dataclass(frozen=True)
class EvaluationProfile:
    name: str
    allow_web: bool
    allow_planning: bool
    offline_mode: bool
    allow_web_cache: bool
    local_total_timeout_seconds: float
    planner_timeout_seconds: float
    total_pipeline_timeout_seconds: float
    max_retrieval_tasks: int
    max_web_queries: int
    query_decompose_enabled: bool
    shared_retrieval_budget_seconds: float | None
    web_fallback_max: int
    web_fallback_budget_seconds: float
    calculation_timeout_seconds: float
    cancel_on_timeout: bool = True


EVALUATION_PROFILES = {
    "A_local_single": EvaluationProfile("A_local_single", False, False, True, False, 20.0, 0.0, 30.0, 1, 0, False, 20.0, 0, 0.0, 5.0),
    "B_local_planned": EvaluationProfile("B_local_planned", False, True, True, False, 20.0, 10.0, 90.0, 3, 0, True, 40.0, 0, 0.0, 5.0),
    "C_controlled_web": EvaluationProfile("C_controlled_web", True, True, False, False, 20.0, 10.0, 120.0, 3, 1, True, 95.0, 1, 15.0, 5.0),
}


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        return None


def _package_versions() -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for name in ("torch", "langchain-core", "langgraph", "chromadb", "sentence-transformers", "pytest"):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def effective_config(profile: EvaluationProfile) -> dict[str, Any]:
    return {
        "allow_web": profile.allow_web,
        "allow_planning": profile.allow_planning,
        "offline_mode": profile.offline_mode,
        "allow_web_cache": profile.allow_web_cache,
        "query_decompose_enabled": profile.query_decompose_enabled,
        "max_retrieval_tasks": profile.max_retrieval_tasks,
        "total_budget_s": profile.total_pipeline_timeout_seconds,
        "local_budget_s": profile.total_pipeline_timeout_seconds - profile.web_fallback_budget_seconds,
        "shared_retrieval_budget_s": profile.shared_retrieval_budget_seconds,
        "per_retrieval_timeout_s": profile.local_total_timeout_seconds,
        "planner_timeout_s": profile.planner_timeout_seconds,
        "web_fallback_max": profile.web_fallback_max,
        "web_fallback_budget_s": profile.web_fallback_budget_seconds,
        "calculation_timeout_s": profile.calculation_timeout_seconds,
        "total_pipeline_timeout_s": profile.total_pipeline_timeout_seconds,
        "cancel_on_timeout": profile.cancel_on_timeout,
    }


def validate_profile_config(profile: EvaluationProfile) -> None:
    config = effective_config(profile)
    expected = {
        "A_local_single": (not config["allow_web"] and not config["allow_planning"] and not config["query_decompose_enabled"] and config["max_retrieval_tasks"] == 1 and config["web_fallback_max"] == 0),
        "B_local_planned": (not config["allow_web"] and config["allow_planning"] and config["query_decompose_enabled"] and config["max_retrieval_tasks"] == 3 and config["web_fallback_max"] == 0),
        "C_controlled_web": (config["allow_web"] and config["allow_planning"] and config["web_fallback_max"] == 1),
    }
    if not expected.get(profile.name, False):
        raise ValueError(f"Profile 配置断言失败：{profile.name} / {config}")
RESULT_FIELDS = (
    "question_id", "question", "category", "difficulty", "expected_mode",
    "answer", "sources", "retrieved_text", "route", "web_search_triggered", "web_search_queries", "web_search_errors", "status", "delivery_status", "deliverable",
    "blocking_issues", "blocking_issue_summary", "allowed_citations", "unsupported_claims", "evidence_matrix",
    "requirement_coverage", "claim_support_rate", "direct_evidence_count",
    "indirect_evidence_count", "confidence", "confidence_status", "rewrite_attempts",
    "tool_calls", "model_calls", "execution_trace", "duration_seconds",
    "retrieval_duration_seconds", "generation_duration_seconds", "token_count", "errors",
    "retrieval_profile", "retrieval_failures", "timeout_stage", "expected_answer_spans",
    "expected_outcome", "task_type", "web_source_count", "planner_call_count", "offline_mode", "web_search_blocked",
    "execution_summary", "effective_config", "experiment_status", "valid_for_comparison",
)


def load_questions(path: Path) -> list[dict[str, Any]]:
    questions = json.loads(path.read_text(encoding="utf-8")).get("questions", [])
    if not questions:
        raise ValueError("评测集不能为空")
    return questions


def _expected(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "expected_mode": item.get("expected_mode", ""),
        "expected_outcome": item.get("expected_outcome", ""),
        "task_type": item.get("task_type", item.get("type", "")),
        "focus": item.get("focus", []),
        "category": item.get("type", ""),
        "difficulty": item.get("difficulty", ""),
        "expected_answer_spans": item.get("expected_answer_spans", []),
    }


def expected_rule(expected_mode: str) -> str:
    """Map natural-language expected modes to deterministic evaluation rules."""
    text = str(expected_mode or "")
    if "无效引用" in text or "必须拒" in text or "必须拒答" in text:
        return "must_refuse"
    if "若缺" in text or "资料不足" in text or "条件化推荐" in text:
        return "conditional_or_refuse"
    if "公式" in text or "计算" in text:
        return "calculation"
    if "可交付" in text:
        return "deliver_or_degrade"
    return "observe"


def item_rule(item: dict[str, Any]) -> str:
    outcome = str(item.get("expected_outcome", "")).lower()
    if outcome in {"refuse", "拒答"} or item.get("task_type") == "refusal":
        return "must_refuse"
    if outcome in {"deliver", "conditional_deliver", "交付", "条件化交付"}:
        return "calculation" if item.get("task_type") == "calculation" else "deliver_or_degrade"
    return expected_rule(item.get("expected_mode", ""))


def _set_cli_overrides(args: argparse.Namespace) -> None:
    """Set env overrides before importing config (Settings is initialized at import time)."""
    overrides = {
        "RETRIEVER_TOP_K": args.retriever_top_k,
        "RETRIEVER_DENSE_TOP_K": args.dense_top_k,
        "RETRIEVER_BM25_TOP_K": args.bm25_top_k,
        "RERANKER_ENABLED": args.reranker_enabled,
        "AGENT_PLANNING_ENABLED": args.planning_enabled,
        "QUERY_DECOMPOSE_ENABLED": args.query_decompose_enabled,
        "LLM_MODEL": args.llm_model,
    }
    for key, value in overrides.items():
        if value is not None:
            os.environ[key] = str(value).lower() if isinstance(value, bool) else str(value)


def _set_profile_overrides(profile: EvaluationProfile) -> None:
    os.environ.update({
        "AGENT_PLANNING_ENABLED": str(profile.allow_planning).lower(),
        "QUERY_DECOMPOSE_ENABLED": str(profile.query_decompose_enabled).lower(),
        "OFFLINE_MODE": str(profile.offline_mode).lower(),
        "ALLOW_WEB_CACHE": str(profile.allow_web_cache).lower(),
        "LOCAL_RETRIEVAL_TOTAL_TIMEOUT_SECONDS": str(profile.local_total_timeout_seconds),
        "PLANNER_TIMEOUT_SECONDS": str(profile.planner_timeout_seconds),
        "TOTAL_PIPELINE_TIMEOUT_SECONDS": str(profile.total_pipeline_timeout_seconds),
        "CANCEL_ON_TIMEOUT": str(profile.cancel_on_timeout).lower(),
    })


def _runtime_config(args: argparse.Namespace, dataset: Path, profile: EvaluationProfile) -> dict[str, Any]:
    from config import settings
    return {
        "dataset": str(dataset),
        "dataset_name": DATASET_NAME,
        "profile": profile.name,
        "with_web": profile.allow_web,
        "allow_planning": profile.allow_planning,
        "offline_mode": profile.offline_mode,
        "allow_web_cache": profile.allow_web_cache,
        "with_judge": bool(args.with_judge),
        "repeat": args.repeat,
        "run_label": args.run_label,
        "retriever_top_k": settings.retriever_top_k,
        "dense_top_k": settings.retriever_dense_top_k,
        "bm25_top_k": settings.retriever_bm25_top_k,
        "reranker_enabled": settings.reranker_enabled,
        "planning_enabled": profile.allow_planning,
        "query_decompose_enabled": profile.query_decompose_enabled,
        "llm_model": settings.llm_model,
        "embedding_model_path": str(settings.embedding_model_path),
        "chunking_strategy": settings.chunking_strategy,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "reranker_model_path": str(settings.reranker_model_path) if settings.reranker_model_path else None,
        "python": sys.executable,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "local_retrieval_timeout_seconds": profile.local_total_timeout_seconds,
        "max_retrieval_tasks": profile.max_retrieval_tasks,
        "max_web_queries": profile.max_web_queries,
        "web_fallback_max": profile.web_fallback_max,
        "shared_retrieval_budget_seconds": profile.shared_retrieval_budget_seconds,
        "local_total_timeout_seconds": profile.local_total_timeout_seconds,
        "planner_timeout_seconds": profile.planner_timeout_seconds,
        "total_pipeline_timeout_seconds": profile.total_pipeline_timeout_seconds,
    }


def sync_dataset(client: Any, name: str, questions: list[dict[str, Any]]) -> Any:
    from langsmith.utils import LangSmithNotFoundError

    try:
        dataset = client.read_dataset(dataset_name=name)
        existing = list(client.list_examples(dataset_id=dataset.id, limit=1000))
    except LangSmithNotFoundError:
        dataset = client.create_dataset(
            dataset_name=name,
            description="设计知识库助手第二轮8题端到端评测集；每次运行上传一个新的 Experiment。",
        )
        existing = []
    by_question = {(example.inputs or {}).get("question", ""): example for example in existing}
    for item in questions:
        question = item["question"]
        outputs = _expected(item)
        example = by_question.get(question)
        if example is None:
            client.create_examples(
                dataset_id=dataset.id,
                examples=[{"inputs": {"question": question}, "outputs": outputs}],
            )
        elif (example.outputs or {}) != outputs:
            client.update_example(example.id, inputs={"question": question}, outputs=outputs)
    return dataset


def _outputs(run: Any) -> dict[str, Any]:
    return getattr(run, "outputs", None) or (run if isinstance(run, dict) else {})


def _call_counter(tool_calls: list[dict[str, Any]], name: str) -> dict[str, int]:
    calls = [call for call in tool_calls if call.get("tool") == name]
    attempted = len(calls)
    completed = sum(bool(call.get("success")) for call in calls)
    timeout = sum(
        not call.get("success") and any(token in str(call.get("error", "")).lower() for token in ("timeout", "超时", "deadline", "timed out"))
        for call in calls
    )
    return {"attempted": attempted, "completed": completed, "timeout": timeout, "failed": max(0, attempted - completed - timeout)}


def _execution_summary(state: dict[str, Any], profile: EvaluationProfile, documents: list[Any], trace: list[dict[str, Any]]) -> dict[str, Any]:
    tool_calls = list(state.get("tool_calls", []))
    planner_data = dict((state.get("execution_counters", {}) or {}).get("planner", {}))
    planner = CallCounter(**{key: int(planner_data.get(key, 0)) for key in CallCounter().__dict__})
    if not planner_data:
        planner_calls = sum(1 for call in state.get("model_calls", []) if isinstance(call, dict) and call.get("role") == "planner")
        planner = CallCounter(scheduled=planner_calls, attempted=planner_calls, completed=planner_calls)
    retrieval_data = dict((state.get("execution_counters", {}) or {}).get("retrieval", {}))
    retrieval = CallCounter(**{key: int(retrieval_data.get(key, 0)) for key in CallCounter().__dict__}) if retrieval_data else counter_from_tool_calls(tool_calls, "local_retrieval")
    web = counter_from_tool_calls(tool_calls, "web_search")
    profile_data = state.get("retrieval_profile", {}) or {}
    timeout_stage = state.get("timeout_stage")
    if timeout_stage == "total_pipeline":
        timeout_stage = "total_pipeline"
    audit_result = "insufficient_evidence" if any(
        str(issue.get("status", "")) in {"indirect_evidence", "unsupported", "unreferenced", "scope_mismatch", "evidence_insufficient"}
        for issue in state.get("blocking_issues", []) if isinstance(issue, dict)
    ) else None
    reason = classify_degradation(
        {"planner": planner, "retrieval": retrieval, "web": web},
        audit_result,
        timeout_stage,
        bool(state.get("deliverable")),
    ).value
    route = state.get("route") or ("calculation" if state.get("calculation_result") else "degraded")
    if web.completed > 0:
        route = "web_fallback"
    elif retrieval.completed > 0:
        route = "local_retrieval"
    exit_reason = "success" if state.get("deliverable") else (
        "budget_exhausted" if retrieval.skipped or web.skipped else
        "cancelled" if retrieval.cancelled or web.cancelled or planner.cancelled else
        "timeout" if reason == DegradationReason.INFRA_TIMEOUT.value else "failed"
    )
    return {
        "route": route,
        "planner_seconds": round(sum(item.get("duration_seconds", 0.0) for item in trace if item.get("node") == "analyze_question"), 3),
        "embedding_seconds": profile_data.get("embedding_seconds", 0.0),
        "dense_seconds": profile_data.get("dense_seconds", 0.0),
        "bm25_seconds": profile_data.get("bm25_seconds", 0.0),
        "reranker_seconds": profile_data.get("reranker_seconds", 0.0),
        "generation_seconds": round(sum(item.get("duration_seconds", 0.0) for item in trace if item.get("node") in {"generate", "aggregate_results", "validate_delivery"}), 3),
        "model_load_seconds": profile_data.get("model_load_seconds", 0.0),
        "timeout_stage": timeout_stage,
        "degradation_reason": reason,
        "planner_calls": planner.as_dict(),
        "retrieval_calls": retrieval.as_dict(),
        "web_search_calls": web.as_dict(),
        "web_source_count": sum(1 for document in documents if document.metadata.get("source_type") == "web" or document.metadata.get("source_category") == "web_search"),
        "deliverable": bool(state.get("deliverable")),
        "refusal_correct": False,
        "exit_reason": exit_reason,
        "last_completed_node": state.get("last_completed_node") or (trace[-1].get("node") if trace else None),
        "active_node": state.get("active_node") or (trace[-1].get("node") if trace else None),
    }


def _make_target(profile: EvaluationProfile | bool, questions: list[dict[str, Any]], rows: list[dict[str, Any]]):
    from src.graph_builder import build_graph
    from src.services import DefaultServices, EvaluationPolicy

    if isinstance(profile, bool):
        profile = EVALUATION_PROFILES["C_controlled_web" if profile else "A_local_single"]
    policy = EvaluationPolicy(
        allow_web=profile.allow_web,
        allow_planning=profile.allow_planning,
        local_timeout_seconds=profile.local_total_timeout_seconds,
        local_total_timeout_seconds=profile.local_total_timeout_seconds,
        planner_timeout_seconds=profile.planner_timeout_seconds,
        total_pipeline_timeout_seconds=profile.total_pipeline_timeout_seconds,
        cancel_on_timeout=profile.cancel_on_timeout,
        offline_mode=profile.offline_mode,
        allow_web_cache=profile.allow_web_cache,
        query_decompose_enabled=profile.query_decompose_enabled,
        shared_retrieval_budget_seconds=profile.shared_retrieval_budget_seconds,
        web_fallback_max=profile.web_fallback_max,
        max_web_queries=profile.max_web_queries,
        max_retrieval_tasks=profile.max_retrieval_tasks,
    )

    by_question = {item["question"]: item for item in questions}

    def target(example: dict[str, Any]) -> dict[str, Any]:
        services = DefaultServices(policy=policy)
        graph = build_graph(services)
        started = time.perf_counter()
        loop = asyncio.new_event_loop()
        started_clock = time.perf_counter()
        pipeline_deadline = started_clock + profile.total_pipeline_timeout_seconds
        local_deadline, web_deadline = reserve_budget(
            pipeline_deadline, profile.web_fallback_budget_seconds, started_clock
        )
        initial_state = {
            "question": example["question"],
            "pipeline_deadline": pipeline_deadline,
            "local_deadline": local_deadline,
            "web_fallback_deadline": web_deadline,
            "active_node": "analyze_question",
            "last_completed_node": "",
        }
        try:
            try:
                state = loop.run_until_complete(asyncio.wait_for(
                    graph.ainvoke(initial_state),
                    timeout=profile.total_pipeline_timeout_seconds,
                ))
            except asyncio.TimeoutError:
                partial = dict(getattr(services, "progress_snapshot", {}) or {})
                state = {
                    "question": example["question"],
                    "status": "degraded_infra_timeout",
                    "delivery_status": "degraded_infra_timeout",
                    "deliverable": False,
                    "errors": [f"pipeline timeout after {profile.total_pipeline_timeout_seconds:g}s"],
                    "timeout_stage": "total_pipeline",
                    "retrieval_failures": [{"stage": "pipeline", "status": "pipeline_timeout"}],
                    "execution_trace": partial.get("execution_trace", []),
                    "tool_calls": partial.get("tool_calls", []),
                    "last_completed_node": partial.get("last_completed_node", ""),
                    "active_node": partial.get("active_node", "unknown"),
                    "retrieval_profile": {},
                    "exit_reason": "timeout",
                }
        finally:
            loop.close()
        trace = state.get("execution_trace", [])
        documents = list(state.get("documents", []))
        execution_summary = _execution_summary(state, profile, documents, trace)
        result = {
            "question_id": by_question.get(example["question"], {}).get("id", ""),
            "question": example["question"],
            "answer": state.get("generation", ""),
            "sources": state.get("sources", []),
            "retrieved_text": [str(doc.page_content) for doc in documents],
            "route": state.get("route", ""),
            "web_search_triggered": bool(state.get("web_search_triggered", False)),
            "web_search_queries": state.get("web_search_queries", []),
            "web_search_errors": state.get("web_search_errors", []),
            "status": state.get("status", ""),
            "delivery_status": state.get("delivery_status", state.get("status", "")),
            "deliverable": bool(state.get("deliverable", False)),
            "blocking_issues": state.get("blocking_issues", []),
            "blocking_issue_summary": state.get("blocking_issue_summary", {}),
            "allowed_citations": state.get("allowed_citations", []),
            "rewrite_attempts": state.get("rewrite_attempts", 0),
            "errors": state.get("errors", []),
            "token_count": state.get("token_count", 0),
            "duration_seconds": round(time.perf_counter() - started, 3),
            "trace_duration_seconds": round(sum(item.get("duration_seconds", 0) for item in trace), 3),
            "retrieval_duration_seconds": round(sum(item.get("duration_seconds", 0) for item in trace if item.get("node") in {"retrieve", "grade_documents", "web_search", "search_evidence_gaps"}), 3),
            "generation_duration_seconds": round(sum(item.get("duration_seconds", 0) for item in trace if item.get("node") in {"generate", "aggregate_results", "validate_delivery"}), 3),
            "unsupported_claims": state.get("unsupported_claims", []),
            "evidence_matrix": state.get("evidence_matrix", []),
            "requirement_coverage": state.get("requirement_coverage", 1.0 if state.get("route") == "calculation" and state.get("deliverable") else 0.0),
            "claim_support_rate": state.get("claim_support_rate", 0.0),
            "direct_evidence_count": sum(1 for doc in documents if doc.metadata.get("retrieval_evidence_status") == "direct"),
            "indirect_evidence_count": sum(1 for doc in documents if doc.metadata.get("retrieval_evidence_status") == "indirect"),
            "confidence": state.get("confidence"),
            "confidence_status": state.get("confidence_status", ""),
            "tool_calls": state.get("tool_calls", []),
            "model_calls": state.get("model_calls", []),
            "execution_trace": trace,
            "retrieval_profile": state.get("retrieval_profile", {}) or {
                "embedding_seconds": 0.0, "dense_seconds": 0.0, "bm25_seconds": 0.0,
                "reranker_seconds": 0.0, "fusion_seconds": 0.0, "dedup_seconds": 0.0,
                "cache_hit": False, "timeout_stage": state.get("timeout_stage"), "error_type": None,
            },
            "retrieval_failures": state.get("retrieval_failures", []),
            "timeout_stage": state.get("timeout_stage"),
            "offline_mode": profile.offline_mode,
            "web_search_blocked": bool(state.get("web_search_blocked", False)),
            "web_source_count": sum(1 for source in state.get("sources", []) if isinstance(source, dict) and (source.get("source_type") == "web" or source.get("retrieval_evidence_status") == "indirect" and source.get("source_category") == "web_search")),
            "planner_call_count": sum(1 for call in state.get("model_calls", []) if isinstance(call, dict) and call.get("role") == "planner"),
            "execution_summary": execution_summary,
            "effective_config": effective_config(profile),
        }
        item = by_question.get(example["question"], {})
        result["execution_summary"]["refusal_correct"] = bool(
            item_rule(item) == "must_refuse"
            and not result.get("deliverable")
            and result.get("execution_summary", {}).get("degradation_reason") in {"evidence_insufficient", "none"}
        )
        result.update({
            "category": item.get("type", ""),
            "task_type": item.get("task_type", item.get("type", "")),
            "difficulty": item.get("difficulty", ""),
            "expected_mode": item.get("expected_mode", ""),
            "expected_outcome": item.get("expected_outcome", ""),
        })
        result["deterministic_scores"] = deterministic_scores(result, item)
        rows.append(result)
        return result

    return target


def delivery_contract_evaluator(run: Any, example: Any) -> dict[str, Any]:
    outputs = _outputs(run)
    expected = (example.outputs or {}).get("expected_mode", "")
    rule = expected_rule(expected)
    if rule == "must_refuse":
        score = 1.0 if outputs.get("deliverable") is False and outputs.get("delivery_status") == "degraded_insufficient_evidence" else 0.0
    else:
        has_contract = all(key in outputs for key in ("deliverable", "delivery_status", "blocking_issues", "allowed_citations"))
        score = 1.0 if has_contract else 0.0
    return {
        "key": "delivery_contract",
        "score": score,
        "comment": "交付状态符合题目预期" if score else "交付状态不符合题目预期",
    }


def evidence_coverage_evaluator(run: Any, example: Any) -> dict[str, Any]:
    outputs = _outputs(run)
    rule = expected_rule((example.outputs or {}).get("expected_mode", ""))
    coverage = float(outputs.get("requirement_coverage", 0.0) or 0.0)
    if rule == "must_refuse":
        score = 1.0 if not outputs.get("deliverable", False) else 0.0
    elif rule == "calculation":
        score = 1.0 if outputs.get("deliverable") and re.search(r"公式|π|单位", str(outputs.get("answer", ""))) else 0.0
    else:
        score = 1.0 if coverage > 0 else 0.0
    return {"key": "evidence_coverage", "score": score, "comment": f"requirement_coverage={coverage:.3f}"}


def answer_structure_evaluator(run: Any, example: Any) -> dict[str, Any]:
    answer = str(_outputs(run).get("answer", ""))
    focus = " ".join((example.outputs or {}).get("focus", []))
    checks = []
    if "公式" in focus:
        checks.append(bool(re.search(r"公式|π|单位|假设", answer)))
    if "验证" in focus:
        checks.append(bool(re.search(r"验证|测试|核实|待验证", answer)))
    if "条件化推荐" in focus or "条件" in focus:
        checks.append(bool(re.search(r"条件|前提|取决于|如果", answer)))
    score = sum(checks) / len(checks) if checks else (1.0 if answer.strip() else 0.0)
    return {"key": "answer_structure", "score": score, "comment": f"checks={sum(checks)}/{len(checks)}" if checks else "非结构化题"}


def failure_diagnosis_evaluator(run: Any, example: Any) -> dict[str, Any]:
    del example
    outputs = _outputs(run)
    issues = outputs.get("blocking_issues", []) or []
    errors = outputs.get("errors", []) or []
    return {"key": "failure_diagnosis", "score": 1.0 if (issues or errors or outputs.get("deliverable")) else 0.0, "comment": f"blocking={len(issues)}, errors={len(errors)}"}


def _span_recall(result: dict[str, Any], item: dict[str, Any]) -> float:
    spans = item.get("expected_answer_spans", []) or []
    if not spans:
        return 0.0
    corpus = " ".join([
        str(result.get("answer", "")),
        *[str(text) for text in result.get("retrieved_text", [])],
        *[str(source.get("title", "")) for source in result.get("sources", [])],
        *[str(source.get("source", "")) for source in result.get("sources", [])],
    ]).lower()
    normalize = lambda value: re.sub(r"\s+|[，。；：、（）()【】\[\],.:;]", "", str(value).lower())
    normalized_corpus = normalize(corpus)
    return round(sum(normalize(span.get("text", "")) in normalized_corpus for span in spans) / len(spans), 3)


def _retrieval_ir_metrics(result: dict[str, Any], item: dict[str, Any]) -> dict[str, float]:
    """Content-level retrieval metrics; no chunk IDs are required."""
    normalize = lambda value: re.sub(r"\s+|[，。；：、（）()【】\[\],.:;]", "", str(value).lower())
    spans = [normalize(span.get("text", "")) for span in item.get("expected_answer_spans", []) if span.get("text")]
    docs = [normalize(text) for text in result.get("retrieved_text", [])]
    if not spans:
        return {"recall_at_5": 0.0, "recall_at_10": 0.0, "precision_at_5": 0.0, "precision_at_10": 0.0, "mrr": 0.0, "ndcg_at_10": 0.0}
    hits = [next((index + 1 for index, doc in enumerate(docs) if span in doc), None) for span in spans]
    def hit_at(k: int) -> int:
        return sum(rank is not None and rank <= k for rank in hits)
    def precision(k: int) -> float:
        return round(hit_at(k) / max(1, min(k, len(docs))), 3)
    reciprocal = next((1.0 / rank for rank in hits if rank is not None), 0.0)
    dcg = sum((1.0 / ((rank or 11) ** 0.5)) for rank in hits if rank and rank <= 10)
    ideal = sum(1.0 / ((index + 1) ** 0.5) for index in range(min(len(spans), 10)))
    return {
        "recall_at_5": round(hit_at(5) / len(spans), 3),
        "recall_at_10": round(hit_at(10) / len(spans), 3),
        "precision_at_5": precision(5),
        "precision_at_10": precision(10),
        "mrr": round(reciprocal, 3),
        "ndcg_at_10": round(dcg / ideal, 3) if ideal else 0.0,
    }


def aggregate_metrics(rows: list[dict[str, Any]], questions: list[dict[str, Any]]) -> dict[str, Any]:
    """Separate delivery outcomes from refusal recognition and latency."""
    by_id = {item.get("id"): item for item in questions}
    eligible = [row for row in rows if row.get("question_id") in by_id]
    normal = [row for row in eligible if item_rule(by_id[row["question_id"]]) != "must_refuse"]
    refusal = [row for row in eligible if item_rule(by_id[row["question_id"]]) == "must_refuse"]
    latencies = sorted(float(row.get("duration_seconds", 0.0) or 0.0) for row in eligible)
    p = lambda q: latencies[min(len(latencies) - 1, max(0, int((len(latencies) - 1) * q)))] if latencies else 0.0
    contract = sum(all(key in row for key in ("deliverable", "delivery_status", "blocking_issues", "allowed_citations")) for row in eligible)
    refusal_ok = sum((not row.get("deliverable")) and row.get("delivery_status") == "degraded_insufficient_evidence" for row in refusal)
    delivered = sum(bool(row.get("deliverable")) and (float(row.get("requirement_coverage", 0) or 0) > 0 or row.get("route") == "calculation") for row in normal)
    def has_status(row: dict[str, Any], statuses: set[str]) -> bool:
        return any(str(issue.get("status", "")) in statuses for issue in row.get("blocking_issues", []) if isinstance(issue, dict))
    def degradation_reason(row: dict[str, Any]) -> str:
        explicit = row.get("execution_summary", {}).get("degradation_reason")
        if explicit:
            return str(explicit)
        if row.get("timeout_stage") or any(token in str(error).lower() for error in row.get("errors", []) for token in ("timeout", "超时", "锁", "加载")):
            return "infra_timeout"
        if not row.get("deliverable") and (has_status(row, {"indirect_evidence", "unsupported", "unreferenced", "scope_mismatch", "evidence_insufficient"}) or bool(row.get("blocking_issues"))):
            return "evidence_insufficient"
        return "none"
    infra = sum(bool(row.get("timeout_stage")) or row.get("execution_summary", {}).get("degradation_reason") == "infra_timeout" or any(token in str(error).lower() for error in row.get("errors", []) for token in ("timeout", "超时", "超过", "deadline", "锁", "加载")) for row in eligible)
    evidence_degraded = sum(
        (not row.get("deliverable"))
        and degradation_reason(row) == "evidence_insufficient"
        for row in normal
    )
    timeout_rate = round(infra / len(eligible), 3) if eligible else 0.0
    span_rows = [
        _retrieval_ir_metrics(row, by_id[row["question_id"]])
        for row in eligible
        if by_id[row["question_id"]].get("expected_answer_spans") and row.get("route") != "calculation"
    ]
    ir = {key: round(sum(metrics[key] for metrics in span_rows) / len(span_rows), 3) if span_rows else 0.0 for key in ("recall_at_5", "recall_at_10", "precision_at_5", "precision_at_10", "mrr", "ndcg_at_10")}
    direct_docs = sum(int(row.get("direct_evidence_count", 0) or 0) for row in eligible)
    indirect_docs = sum(int(row.get("indirect_evidence_count", 0) or 0) for row in eligible)
    mismatch_docs = sum(sum(1 for doc in row.get("sources", []) if isinstance(doc, dict) and doc.get("retrieval_evidence_status") == "scope_mismatch") for row in eligible)
    result = {
        "sample_size": len(eligible),
        "stability_note": "描述性结果，样本量低于30，不作稳定性结论" if len(eligible) < 30 else "可用于阶段性门槛判断",
        "normal_delivery": {"value": round(delivered / len(normal), 3) if normal else 0.0, "numerator": delivered, "denominator": len(normal)},
        "correct_refusal": {"value": round(refusal_ok / len(refusal), 3) if refusal else 0.0, "numerator": refusal_ok, "denominator": len(refusal)},
        "evidence_insufficiency_degraded": {"value": round(evidence_degraded / len(normal), 3) if normal else 0.0, "numerator": evidence_degraded, "denominator": len(normal)},
        "infra_timeout_degraded": {"value": timeout_rate, "numerator": infra, "denominator": len(eligible)},
        "normal_delivery_denominator": len(normal),
        "correct_refusal_denominator": len(refusal),
        "contract_compliance_rate": round(contract / len(eligible), 3) if eligible else 0.0,
        "normal_delivery_rate": round(delivered / len(normal), 3) if normal else 0.0,
        "correct_refusal_rate": round(refusal_ok / len(refusal), 3) if refusal else 0.0,
        "evidence_insufficiency_degraded_rate": round(evidence_degraded / len(normal), 3) if normal else 0.0,
        "infra_timeout_degraded_rate": timeout_rate,
        "degraded_recognition_rate": round((evidence_degraded + infra) / len(eligible), 3) if eligible else 0.0,
        "citation_validity_rate": round(sum(not any(item.get("status") == "citation_invalid" for item in row.get("blocking_issues", [])) for row in eligible) / len(eligible), 3) if eligible else 0.0,
        "direct_evidence_rate": round(sum(int(row.get("direct_evidence_count", 0) or 0) > 0 for row in eligible) / len(eligible), 3) if eligible else 0.0,
        "local_retrieval_timeout_rate": timeout_rate,
        "direct_evidence_hit_rate": round(direct_docs / max(1, direct_docs + indirect_docs + mismatch_docs), 3),
        "indirect_evidence_hit_rate": round(indirect_docs / max(1, direct_docs + indirect_docs + mismatch_docs), 3),
        "scope_mismatch_hit_rate": round(mismatch_docs / max(1, direct_docs + indirect_docs + mismatch_docs), 3),
        "p50_total_latency": round(p(0.50), 3),
        "p95_total_latency": round(p(0.95), 3),
        "answer_span_recall": round(sum(_span_recall(row, by_id[row["question_id"]]) for row in eligible if row.get("route") != "calculation") / max(1, sum(1 for row in eligible if row.get("route") != "calculation")), 3) if any(row.get("route") != "calculation" for row in eligible) else 0.0,
        "quality_metrics": {
            "faithfulness": {"value": None, "n_effective": 0, "status": "not_applicable"},
            "answer_relevancy": {"value": None, "n_effective": 0, "status": "not_applicable"},
        },
    }
    result.update(ir)
    return result


def validate_experiment(profile: EvaluationProfile, rows: list[dict[str, Any]], questions: list[dict[str, Any]]) -> dict[str, Any]:
    """Hard gate: invalid profiles cannot be compared as baselines."""
    failures: list[str] = []
    by_id = {item.get("id"): item for item in questions}
    summaries = [row.get("execution_summary", {}) for row in rows]
    for row, summary in zip(rows, summaries):
        for name in ("planner_calls", "retrieval_calls", "web_search_calls"):
            counter = summary.get(name, {})
            scheduled = int(counter.get("scheduled", 0))
            attempted = int(counter.get("attempted", 0))
            skipped = int(counter.get("skipped", 0))
            completed = int(counter.get("completed", 0))
            timeout = int(counter.get("timeout", 0))
            failed = int(counter.get("failed", 0))
            cancelled = int(counter.get("cancelled", 0))
            if scheduled != attempted + skipped or attempted != completed + timeout + failed + cancelled:
                failures.append(f"{row.get('question_id')}: {name} counter invariant failed")
    if profile.name == "A_local_single":
        for row, summary in zip(rows, summaries):
            if summary.get("planner_calls", {}).get("attempted", 0) != 0:
                failures.append(f"{row.get('question_id')}: planner called")
            if summary.get("web_search_calls", {}).get("attempted", 0) != 0:
                failures.append(f"{row.get('question_id')}: web called")
            if summary.get("web_source_count", 0) != 0:
                failures.append(f"{row.get('question_id')}: web source present")
            if summary.get("retrieval_calls", {}).get("attempted", 0) > 1:
                failures.append(f"{row.get('question_id')}: retrieval attempts > 1")
        calc = next((row for row in rows if row.get("question_id") == "r2-02"), None)
        calc_summary = calc.get("execution_summary", {}) if calc else {}
        if calc and (calc.get("route") != "calculation" or not calc.get("deliverable") or calc_summary.get("degradation_reason") != "none" or calc.get("duration_seconds", 999) >= profile.calculation_timeout_seconds or not re.search(r"398(?:\.\d+)?\s*mL", str(calc.get("answer", "")))):
            failures.append("r2-02 calculation contract failed")
        if calc and any(calc_summary.get(name, {}).get("attempted", 0) for name in ("planner_calls", "retrieval_calls", "web_search_calls")):
            failures.append("r2-02 calculation invoked a forbidden tool")
    elif profile.name == "B_local_planned":
        for row, summary in zip(rows, summaries):
            if summary.get("retrieval_calls", {}).get("attempted", 0) > profile.max_retrieval_tasks:
                failures.append(f"{row.get('question_id')}: retrieval tasks > {profile.max_retrieval_tasks}")
            if row.get("question_id") == "r2-02" and any(summary.get(key, {}).get("attempted", 0) for key in ("planner_calls", "retrieval_calls", "web_search_calls")):
                failures.append("r2-02 calculation did not short circuit")
    elif profile.name == "C_controlled_web":
        valid_web = [summary for summary in summaries if summary.get("web_search_calls", {}).get("attempted", 0) > 0 and summary.get("web_search_calls", {}).get("completed", 0) > 0 and summary.get("web_source_count", 0) > 0]
        if not valid_web:
            failures.append("not_executed_to_web: no completed web fallback with web sources")
        for row, summary in zip(rows, summaries):
            if summary.get("web_search_calls", {}).get("attempted", 0) == 0 and summary.get("timeout_stage") not in {"planner", "local_retrieval", "web_fallback_unreachable", "total_pipeline", None}:
                failures.append(f"{row.get('question_id')}: invalid pre-web timeout stage")
    return {"experiment_status": "invalid_baseline" if failures else "valid_baseline", "valid_for_comparison": not failures, "validation_failures": failures}


def deterministic_scores(result: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """Run non-LLM evaluators locally so every exported row is self-explanatory."""
    example = SimpleNamespace(inputs={"question": item.get("question", "")}, outputs=_expected(item))
    scores = {}
    evaluators = (delivery_contract_evaluator, citation_validity_evaluator, evidence_coverage_evaluator, answer_structure_evaluator, failure_diagnosis_evaluator)
    for evaluator in evaluators:
        feedback = evaluator(result, example)
        scores[feedback["key"]] = {key: value for key, value in feedback.items() if key != "key"}
    return scores


def citation_validity_evaluator(run: Any, example: Any) -> dict[str, Any]:
    del example
    outputs = _outputs(run)
    used = set(re.findall(r"\[([LW]\d+)\]", str(outputs.get("answer", ""))))
    allowed = set(outputs.get("allowed_citations", []))
    invalid = sorted(used - allowed)
    return {
        "key": "citation_validity",
        "score": 1.0 if not invalid else 0.0,
        "comment": "所有引用均来自本次证据集合" if not invalid else f"发现无效引用：{', '.join(invalid)}",
    }


def correctness_evaluator():
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_openai import ChatOpenAI
    from config import settings

    settings.require_llm()
    llm = ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=0,
        timeout=settings.llm_timeout_seconds,
        max_retries=1,
    )
    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            "你是严格的中文设计知识库评测员。只输出JSON：{\\\"score\\\":0-10,\\\"reason\\\":\\\"一句话\\\"}。"
            "重点检查回答是否覆盖题目要求、是否区分事实/推导/待验证、是否遵守证据约束。",
        ),
        (
            "human",
            "问题：{question}\n题目类型：{category}\n评分重点：{focus}\n模型回答：{answer}",
        ),
    ])
    chain = prompt | llm

    def evaluator(run: Any, example: Any) -> dict[str, Any]:
        try:
            response = chain.invoke({
                "question": (example.inputs or {}).get("question", ""),
                "category": (example.outputs or {}).get("category", ""),
                "focus": "、".join((example.outputs or {}).get("focus", [])),
                "answer": _outputs(run).get("answer", ""),
            })
            raw = response.content if hasattr(response, "content") else str(response)
            match = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(match.group(0)) if match else {}
            score = max(0.0, min(1.0, float(data.get("score", 0)) / 10))
            return {"key": "answer_correctness", "score": score, "comment": str(data.get("reason", ""))[:200]}
        except Exception as exc:
            return {"key": "answer_correctness", "score": None, "status": "evaluator_failed", "comment": str(exc)[:200]}

    return evaluator


def main() -> int:
    parser = argparse.ArgumentParser(description="运行可重复的本地/受控网络评测")
    parser.add_argument("--dataset", default="data/test_qa_round2.json")
    parser.add_argument("--profile", choices=sorted(EVALUATION_PROFILES), default="A_local_single")
    parser.add_argument("--with-web", action="store_true", help="兼容旧参数：等价于 C_controlled_web（profile 优先）")
    parser.add_argument("--with-judge", action="store_true", help="额外上传LLM答案质量评分")
    parser.add_argument("--upload-langsmith", action="store_true", help="显式上传 LangSmith；默认完全本地运行")
    parser.add_argument("--experiment-prefix", default="design-kb-round2")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--retriever-top-k", type=int, default=None)
    parser.add_argument("--dense-top-k", type=int, default=None)
    parser.add_argument("--bm25-top-k", type=int, default=None)
    parser.add_argument("--reranker-enabled", type=lambda value: value.lower() == "true", default=None)
    parser.add_argument("--planning-enabled", type=lambda value: value.lower() == "true", default=None)
    parser.add_argument("--query-decompose-enabled", type=lambda value: value.lower() == "true", default=None)
    parser.add_argument("--llm-model", default=None)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--run-label", default="")
    parser.add_argument("--dry-run", action="store_true", help="只输出有效配置，不执行评测")
    parser.add_argument("--print-effective-config", action="store_true", help="打印有效配置快照")
    parser.add_argument("--question-id", default=None, help="只运行指定题目，用于单题 smoke")
    args = parser.parse_args()

    if args.repeat < 1:
        parser.error("--repeat 必须大于等于1")
    profile = EVALUATION_PROFILES[args.profile]
    if args.with_web and args.profile == "A_local_single":
        profile = EVALUATION_PROFILES["C_controlled_web"]
    _set_cli_overrides(args)
    _set_profile_overrides(profile)
    validate_profile_config(profile)
    from config import LOG_DIR
    dataset_path = Path(args.dataset)
    questions = load_questions(dataset_path)
    if args.question_id:
        questions = [item for item in questions if item.get("id") == args.question_id]
        if not questions:
            raise ValueError(f"题目不存在：{args.question_id}")
    if args.print_effective_config or args.dry_run:
        snapshot = {
            "profile": profile.name,
            "effective_config": effective_config(profile),
            "python_executable": sys.executable,
            "python_version": platform.python_version(),
            "git_commit": _git_commit(),
            "package_versions": _package_versions(),
        }
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    if args.dry_run:
        return 0
    prefix = f"{args.experiment_prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    output_dir = Path(args.output_dir) if args.output_dir else LOG_DIR / "round2_runs"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    rows: list[dict[str, Any]] = []
    runtime = _runtime_config(args, dataset_path, profile)
    manifest = output_dir / f"{stamp}_manifest.json"
    manifest_data = {
        "dataset_name": DATASET_NAME,
        "dataset_id": None,
        "experiment_prefix": prefix,
        "question_count": len(questions),
        "profile": profile.name,
        "with_web": profile.allow_web,
        "with_judge": args.with_judge,
        "uploaded": False,
        "runtime": runtime,
        "effective_config": effective_config(profile),
        "python_executable": sys.executable,
        "git_commit": _git_commit(),
        "package_versions": _package_versions(),
        "run_label": args.run_label,
        "repeat": args.repeat,
        "local_output_dir": str(output_dir),
    }
    manifest.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")
    upload_error = None
    target = _make_target(profile, questions, rows)
    if args.upload_langsmith:
        if profile.offline_mode:
            raise RuntimeError("offline profile 禁止上传 LangSmith；请改用非离线 profile")
        try:
            from langsmith import Client
            from langsmith.evaluation import evaluate
            client = Client()
            dataset = sync_dataset(client, DATASET_NAME, questions)
            manifest_data["dataset_id"] = str(dataset.id)
            examples = list(client.list_examples(dataset_id=dataset.id, limit=1000))
            by_question = {(example.inputs or {}).get("question", ""): example for example in examples}
            selected = [by_question[item["question"]] for item in questions]
            evaluators = [delivery_contract_evaluator, citation_validity_evaluator, evidence_coverage_evaluator, answer_structure_evaluator, failure_diagnosis_evaluator]
            if args.with_judge:
                evaluators.append(correctness_evaluator())
            evaluate(target, data=selected * args.repeat, evaluators=evaluators, experiment_prefix=prefix,
                     description=f"设计知识库助手评测 profile={profile.name}", metadata=runtime,
                     max_concurrency=1, client=client, blocking=True, upload_results=True)
            manifest_data["uploaded"] = True
        except Exception as exc:
            upload_error = str(exc)
    else:
        for _ in range(args.repeat):
            for item in questions:
                target({"question": item["question"]})
    rows.sort(key=lambda row: next((i for i, q in enumerate(questions) if q["question"] == row.get("question")), 999))
    summary = aggregate_metrics(rows, questions)
    validation = validate_experiment(profile, rows, questions)
    summary.update(validation)
    for row in rows:
        row["experiment_status"] = validation["experiment_status"]
        row["valid_for_comparison"] = validation["valid_for_comparison"]
    result_payload = {"experiment": prefix, "runtime": runtime, "question_count": len(rows), "summary": summary, "validation": validation, "rows": rows}
    (output_dir / f"{stamp}.json").write_text(json.dumps(result_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    lines = [f"# Evaluation: {prefix}", "", f"- 题目数：{len(rows)}", f"- profile：{profile.name}", f"- experiment_status：{validation['experiment_status']}", f"- with_judge：{args.with_judge}", f"- uploaded：{bool(args.upload_langsmith and not upload_error)}", "", "## 汇总", "", *[f"- {key}：{value}" for key, value in summary.items()], "", "| # | ID | 状态 | 交付 | 覆盖率 | 引用 | 延迟(s) | 阻断 |", "|---:|---|---|---|---:|---|---:|---|"]
    for index, row in enumerate(rows, 1):
        issues = ", ".join(str(item.get("status", "")) for item in row.get("blocking_issues", [])) or "—"
        lines.append(f"| {index} | {row.get('question_id', '')} | {row.get('status', '')} | {row.get('deliverable', False)} | {row.get('requirement_coverage', 0):.3f} | {', '.join(row.get('allowed_citations', [])) or '—'} | {row.get('duration_seconds', 0):.3f} | {issues} |")
    (output_dir / f"{stamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest_data.update({
        "uploaded": bool(args.upload_langsmith and upload_error is None),
        "experiment_status": validation["experiment_status"],
        "valid_for_comparison": validation["valid_for_comparison"],
        "upload_error": upload_error,
        "result_json": str(output_dir / f"{stamp}.json"),
        "result_markdown": str(output_dir / f"{stamp}.md"),
    })
    manifest.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"评测 profile：{profile.name}")
    print(f"LangSmith 上传：{bool(args.upload_langsmith and not upload_error)}")
    print(f"本地上传清单：{manifest}")
    if upload_error:
        raise RuntimeError(f"LangSmith Experiment 上传失败；本地结果已保存：{upload_error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
