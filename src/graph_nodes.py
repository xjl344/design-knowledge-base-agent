"""Node factory for the CRAG + Web Search workflow."""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import time
from typing import Any

from src.generator import NO_EVIDENCE_ANSWER, build_fallback_answer, build_refusal_answer, build_sources
from src.graph_state import GraphState
from src.services import Services
from src.planning import heuristic_plan, make_plan
from src.evaluation import decision_quality_metrics
from src.retriever import confidence_from_documents
from src.model_clients import model_descriptor
from src.evidence import audit_claims, delivery_decision
from src.calculation import calculate_cylinder_capacity, validate_derivation
import re


def _errors(state: GraphState) -> list[str]:
    return list(state.get("errors", []))


def _compact_documents(documents: list[Any], per_source: int = 2, total: int = 18) -> list[Any]:
    result: list[Any] = []
    counts: dict[str, int] = {}
    seen: set[Any] = set()
    for document in documents:
        key = document.metadata.get("chunk_id") or (document.metadata.get("source", ""), document.metadata.get("page", ""), document.page_content[:120])
        if key in seen:
            continue
        seen.add(key)
        source = str(document.metadata.get("source", ""))
        if counts.get(source, 0) >= per_source:
            continue
        counts[source] = counts.get(source, 0) + 1
        result.append(document)
        if len(result) >= total:
            break
    return result


def _estimate_tokens(question: str, answer: str) -> int:
    return max(1, len(f"{question}\n{answer}") // 4)


@dataclass
class GraphNodes:
    services: Services

    def _trace(self, state: GraphState, node: str, status: str, started: float, error: str | None = None) -> list[dict[str, Any]]:
        state["active_node"] = node
        if status in {"success", "completed", "accepted", "empty", "blocked", "refused", "rewritten"}:
            state["last_completed_node"] = node
        trace = list(state.get("execution_trace", []))
        trace.append({"node": node, "status": status, "duration_seconds": round(time.perf_counter() - started, 3), "error": error})
        snapshot = getattr(self.services, "progress_snapshot", None)
        if isinstance(snapshot, dict):
            snapshot.update({
                "last_completed_node": state.get("last_completed_node"),
                "active_node": node,
                "execution_trace": trace,
                "timeout_stage": state.get("timeout_stage"),
                "tool_calls": list(state.get("tool_calls", [])),
            })
        return trace

    def _tool_calls(self, state: GraphState, name: str, result: Any) -> list[dict[str, Any]]:
        calls = list(state.get("tool_calls", []))
        calls.append({"tool": name, **result.as_dict()})
        return calls

    def _policy(self):
        return getattr(self.services, "policy", None)

    def _web_allowed(self) -> bool:
        policy = self._policy()
        return True if policy is None else bool(getattr(policy, "allow_web", True))

    async def _audit_generation(self, generation: str, documents: list[Any], problem_spec: dict[str, Any]) -> dict[str, Any]:
        auditor = getattr(self.services, "audit_answer", None)
        if auditor:
            return await auditor(generation, documents, problem_spec)
        return audit_claims(generation, documents, problem_spec)

    async def _generation_with_audit(self, question: str, documents: list[Any], problem_spec: dict[str, Any],
                                     task_errors: list[str] | None = None) -> tuple[str, dict[str, Any], dict[str, Any]]:
        generation = await self.services.generate(question, documents)
        audit = await self._audit_generation(generation, documents, problem_spec) if generation else {}
        decision = delivery_decision(generation, documents, audit, task_errors)
        return generation, audit, decision

    async def analyze_question(self, state: GraphState) -> GraphState:
        started = time.perf_counter()
        question = state["question"]
        try:
            fallback = heuristic_plan(question)
            analyzer = getattr(self.services, "analyze_complexity", None)
            policy = self._policy()
            allow_planning = True if policy is None else bool(getattr(policy, "allow_planning", True))
            calculation_question = bool(re.search(
                r"内径\s*[0-9]+(?:\.[0-9]+)?\s*mm.*?(?:有效液高|液高)\s*[0-9]+(?:\.[0-9]+)?\s*mm",
                question,
            ))
            analysis = fallback if calculation_question else (await analyzer(question) if analyzer and allow_planning else fallback)
            # A custom/LLM analyzer must not upgrade an obviously simple input.
            # This keeps the simple CRAG contract stable when an analyzer emits
            # an over-eager classification for a short test or status message.
            if fallback.get("question_type") == "simple":
                analysis = fallback
            question_type = analysis.get("type", "simple")
            if calculation_question:
                question_type = "calculation"
            model_calls = list(state.get("model_calls", []))
            if analyzer and allow_planning and not calculation_question:
                model_calls.append(model_descriptor("planner"))
            planner_status = getattr(self.services, "last_planner_status", "not_attempted")
            planner_counter = {
                "scheduled": 1 if analyzer and allow_planning and not calculation_question and planner_status != "not_attempted" else 0,
                "attempted": 1 if analyzer and allow_planning and not calculation_question and planner_status != "not_attempted" else 0,
                "completed": 1 if planner_status == "completed" else 0,
                "timeout": 1 if planner_status == "timeout" else 0,
                "failed": 1 if planner_status == "failed" else 0,
                "skipped": 0,
                "cancelled": 0,
            }
            execution_trace = self._trace(state, "analyze_question", "success", started)
            if not self._web_allowed():
                execution_trace.append({
                    "node": "web_search_blocked",
                    "status": "blocked",
                    "duration_seconds": 0.0,
                    "error": "web_disabled",
                })
            return {
                "question_type": question_type,
                "problem_spec": analysis.get("problem_spec", {}),
                "task_specs": analysis.get("task_specs", []),
                "sub_tasks": analysis.get("task_specs", []) or [{"question": text} for text in analysis.get("sub_questions", [])],
                "route": "planning" if question_type != "simple" else "retrieving",
                "model_calls": model_calls,
                "status": "analyzed",
                "retrieval_budget": {
                    "max_retrieval_tasks": getattr(self._policy(), "max_retrieval_tasks", 3),
                    "max_web_queries": getattr(self._policy(), "max_web_queries", 3),
                    "local_timeout_seconds": getattr(self._policy(), "local_timeout_seconds", 30.0),
                    "local_total_timeout_seconds": getattr(self._policy(), "local_total_timeout_seconds", None),
                    "planner_timeout_seconds": getattr(self._policy(), "planner_timeout_seconds", 15.0),
                    "cancel_on_timeout": getattr(self._policy(), "cancel_on_timeout", True),
                },
                "retrieval_failures": [],
                "offline_mode": bool(getattr(self._policy(), "offline_mode", False)),
                "execution_counters": {"planner": planner_counter},
                "execution_trace": execution_trace,
            }
        except Exception as exc:
            return {
                "question_type": "simple",
                "route": "retrieving",
                "errors": _errors(state) + [f"问题分析失败，已回退简单流程：{exc}"],
                "execution_trace": self._trace(state, "analyze_question", "fallback", started, str(exc)),
            }

    async def calculation(self, state: GraphState) -> GraphState:
        started = time.perf_counter()
        question = state["question"]
        match = re.search(
            r"内径\s*([0-9]+(?:\.[0-9]+)?)\s*mm.*?(?:有效液高|液高)\s*([0-9]+(?:\.[0-9]+)?)\s*mm",
            question,
        )
        if not match:
            return {
                "generation": "无法从题目中解析出内径和有效液高。",
                "deliverable": False,
                "delivery_status": "degraded_insufficient_evidence",
                "status": "degraded_insufficient_evidence",
                "blocking_issues": [{"status": "calculation_input_missing", "reason": "缺少可识别的几何输入"}],
                "allowed_citations": [],
                "execution_trace": self._trace(state, "calculation", "error", started, "calculation_input_missing"),
            }
        record = calculate_cylinder_capacity(float(match.group(1)), float(match.group(2)))
        errors = validate_derivation(record)
        result = record.as_dict()
        if errors:
            return {
                "calculation_result": result,
                "generation": "计算记录不完整，无法交付。",
                "deliverable": False,
                "delivery_status": "degraded_insufficient_evidence",
                "status": "degraded_insufficient_evidence",
                "blocking_issues": [{"status": "calculation_invalid", "reason": "; ".join(errors)}],
                "allowed_citations": [],
                "execution_trace": self._trace(state, "calculation", "error", started, "; ".join(errors)),
            }
        value = result["result"]["ideal_capacity_ml"]
        generation = (
            f"计算公式：{record.formula}\n\n"
            f"代入：V = π × {match.group(1)}² / 4 × {match.group(2)} = "
            f"{result['result']['volume_mm3']:.0f} mm³ ≈ {value:.1f} mL。\n\n"
            "以上是忽略杯底圆角、壁厚、杯盖和液面余量的理想几何容量，"
            "不等同于额定容量；应通过实测内腔尺寸和装液测试确认。"
        )
        return {
            "calculation_result": result,
            "generation": generation,
            "deliverable": True,
            "delivery_status": "completed",
            "status": "completed",
            "blocking_issues": [],
            "allowed_citations": [],
            "route": "calculation",
            "execution_trace": self._trace(state, "calculation", "success", started),
        }

    async def create_plan(self, state: GraphState) -> GraphState:
        started = time.perf_counter()
        analysis = {
            "type": state.get("question_type", "complex"),
            "question_type": state.get("question_type", "complex"),
            "problem_spec": state.get("problem_spec", {}),
            "task_specs": state.get("task_specs") or state.get("sub_tasks", []),
            "parallel": True,
        }
        plan = make_plan(state["question"], analysis)
        return {
            "plan_id": plan["plan_id"],
            "problem_spec": plan["problem_spec"],
            "task_specs": plan["task_specs"],
            "sub_tasks": plan["sub_tasks"],
            "route": "planning",
            "status": "planned",
            "execution_trace": self._trace(state, "create_plan", "success", started),
        }

    async def execute_plan(self, state: GraphState) -> GraphState:
        started = time.perf_counter()
        try:
            executor = getattr(self.services, "execute_subtasks", None)
            tasks = list(state.get("sub_tasks", []))
            policy = self._policy()
            max_tasks = getattr(policy, "max_retrieval_tasks", 3) if policy else 3
            if executor:
                results = await executor(tasks[:max_tasks])
            else:
                results = []
            retrieval_tasks = results[:max_tasks]
            retrieval_counter = {
                "scheduled": len(retrieval_tasks),
                "attempted": sum(1 for result in retrieval_tasks if result.get("status") != "skipped"),
                "completed": sum(1 for result in retrieval_tasks if result.get("status") == "completed"),
                "timeout": sum(1 for result in retrieval_tasks if result.get("status") == "timeout"),
                "failed": sum(1 for result in retrieval_tasks if result.get("status") == "failed"),
                "skipped": sum(1 for result in retrieval_tasks if result.get("status") == "skipped"),
                "cancelled": sum(1 for result in retrieval_tasks if result.get("status") == "cancelled"),
            }
            return {
                "task_results": results,
                "tool_calls": list(state.get("tool_calls", [])) + [
                    call for result in results for call in result.get("tool_calls", [])
                ],
                "route": "planned",
                "status": "tasks_completed",
                "execution_counters": {"retrieval": retrieval_counter},
                "execution_trace": self._trace(state, "execute_plan", "success", started),
            }
        except Exception as exc:
            return {
                "task_results": [],
                "errors": _errors(state) + [f"计划执行失败，已回退简单流程：{exc}"],
                "route": "retrieving",
                "status": "fallback",
                "execution_trace": self._trace(state, "execute_plan", "fallback", started, str(exc)),
            }

    async def aggregate_results(self, state: GraphState) -> GraphState:
        started = time.perf_counter()
        try:
            aggregator = getattr(self.services, "aggregate_answers", None)
            documents = []
            seen = set()
            for result in state.get("task_results", []):
                for document in result.get("documents", []):
                    key = document.metadata.get("chunk_id") or str(id(document))
                    if key not in seen:
                        seen.add(key)
                        documents.append(document)
            documents = _compact_documents(documents, per_source=3, total=30)
            compact_results = [{**result, "documents": _compact_documents(result.get("documents", []), per_source=3, total=30)} for result in state.get("task_results", [])]
            generation = await aggregator(state["question"], compact_results) if aggregator else await self.services.generate(state["question"], documents)
            task_errors = [result.get("error") for result in state.get("task_results", []) if result.get("error")]
            evidence_matrix = state.get("evidence_matrix", [])
            audit = await self._audit_generation(generation, documents, state.get("problem_spec", {})) if generation else {}
            decision = delivery_decision(generation, documents, audit, task_errors)
            return {
                "documents": documents,
                "generation": generation,
                "token_count": _estimate_tokens(state["question"], generation),
                "model_calls": list(state.get("model_calls", [])) + [model_descriptor("generator")],
                "sources": build_sources(documents),
                "route": "planned_web" if state.get("web_search_triggered") else "planned",
                "status": decision["delivery_status"],
                **decision,
                "audit_warnings": audit.get("warnings", []),
                "rewrite_attempts": state.get("rewrite_attempts", 0),
                "evidence_policy": {"direct_only_for_facts": True, "indirect_is_lead_only": True},
                "errors": _errors(state) + [f"子任务失败：{error}" for error in task_errors],
                "unsupported_claims": [
                    item.get("missing_reason", "") for item in evidence_matrix
                    if item.get("coverage") in {"missing", "partial", "conflict"} and item.get("missing_reason")
                ] + audit.get("unsupported_claims", []),
                "claims": audit.get("claims", []),
                "source_assessments": audit.get("source_assessments", []),
                "conflicts": audit.get("conflicts", []),
                "claim_support_rate": audit.get("claim_support_rate", 0.0),
                "recommendation_conditions": audit.get("recommendation_conditions", []),
                "decision_metrics": decision_quality_metrics(audit),
                "answer_sections": [
                    {"title": "结论", "status": "generated"},
                    {"title": "证据覆盖", "status": "audited", "items": evidence_matrix},
                    {"title": "限制与待验证项", "status": "included"},
                ],
                "execution_trace": self._trace(state, "aggregate_results", "success", started),
            }
        except Exception as exc:
            fallback_documents = _compact_documents([
                document
                for result in state.get("task_results", [])
                for document in result.get("documents", [])
            ], per_source=3, total=30)
            fallback_generation = build_fallback_answer(state["question"], fallback_documents)
            return {
                "errors": _errors(state) + [f"计划结果聚合失败：{exc}"],
                "status": "degraded_insufficient_evidence",
                "deliverable": False,
                "delivery_status": "degraded_insufficient_evidence",
                "blocking_issues": [{"status": "generation_error", "reason": str(exc)}],
                "allowed_citations": [],
                "rewrite_attempts": state.get("rewrite_attempts", 0),
                "documents": fallback_documents,
                "generation": fallback_generation,
                "sources": build_sources(fallback_documents),
                "token_count": _estimate_tokens(state["question"], fallback_generation),
                "model_calls": list(state.get("model_calls", [])) + [model_descriptor("generator")],
                "execution_trace": self._trace(state, "aggregate_results", "error", started, str(exc)),
            }

    @staticmethod
    def _evidence_gaps(state: GraphState) -> list[dict[str, Any]]:
        """Return bounded, task-specific gaps that can benefit from web search."""
        matrix_by_task = {
            str(item.get("task_id")): item
            for item in state.get("evidence_matrix", [])
            if item.get("task_id") is not None
        }
        gaps: list[dict[str, Any]] = []
        for result in state.get("task_results", []):
            task_id = str(result.get("id", ""))
            matrix = matrix_by_task.get(task_id, {})
            coverage = matrix.get("coverage")
            documents = result.get("documents", [])
            statuses = {
                str(document.metadata.get("retrieval_evidence_status", "unknown"))
                for document in documents
            }
            has_direct = "direct" in statuses
            if coverage == "covered":
                continue
            if coverage is None and documents and has_direct:
                continue
            if coverage is None and not documents and not result.get("error"):
                reason = "未完成证据覆盖判断"
            else:
                reason = matrix.get("missing_reason") or (
                    "只有间接资料或适用范围不匹配资料，缺少直接证据"
                    if documents else "未找到满足任务要求的直接资料"
                )
            gaps.append({
                "task_id": task_id,
                "objective": result.get("objective", ""),
                "query": result.get("query") or result.get("question", ""),
                "requirements": list(
                    matrix.get("requirements")
                    or result.get("evidence_requirements", [])
                ),
                "reason": reason,
                "priority": result.get("priority", "normal"),
            })
        gaps.sort(key=lambda item: (item.get("priority") != "high", item.get("task_id", "")))
        return gaps[:3]

    def decide_after_evidence_audit(self, state: GraphState) -> str:
        if state.get("web_search_attempted") or not self._web_allowed():
            return "aggregate_results"
        return "search_evidence_gaps" if self._evidence_gaps(state) else "aggregate_results"

    @staticmethod
    def _annotate_web_document(document: Any, query: str) -> Any:
        """Apply conservative metadata to results from search snippets."""
        metadata = document.metadata
        verified = all(metadata.get(key) for key in (
            "source_url", "retrieved_content", "content_hash", "source_version",
            "locator", "applicability", "verified_by", "verified_at",
        )) and metadata.get(
            "verification_status"
        ) in {"原文已核验", "verified"}
        metadata.update({
            "source_type": "web",
            "source_category": metadata.get("source_category", "web_search"),
            "evidence_level": metadata.get("evidence_level", "web_search"),
            "source_title": metadata.get("source_title") or metadata.get("title", "未知来源"),
            "population": metadata.get("population", "unknown"),
            "material_grade": metadata.get("material_grade", "unknown"),
            "applicability": metadata.get("applicability", "unknown"),
            "content_hash": metadata.get("content_hash"),
            "source_version": metadata.get("source_version"),
            "verified_by": metadata.get("verified_by"),
            "verified_at": metadata.get("verified_at"),
            "retrieval_evidence_status": "direct" if verified else "indirect",
            "retrieval_evidence_reason": "原文、定位和适用范围已核验" if verified else "网络搜索摘要，未核验原始文件或完整适用范围",
            "web_query": query,
        })
        return document

    async def search_evidence_gaps(self, state: GraphState) -> GraphState:
        """Search the top three gaps and merge results back into their tasks."""
        started = time.perf_counter()
        if not self._web_allowed():
            return {
                "web_search_attempted": True,
                "web_search_triggered": False,
                "web_search_errors": list(state.get("web_search_errors", [])) + ["web_disabled"],
                "route": "planned",
                "status": "web_disabled",
                "web_search_blocked": True,
                "errors": _errors(state) + ["web_disabled: 当前评测策略禁止网络搜索"],
                "execution_trace": self._trace(state, "search_evidence_gaps", "blocked", started, "web_disabled"),
            }
        web_deadline = state.get("web_fallback_deadline")
        if web_deadline is not None and time.perf_counter() >= float(web_deadline):
            return {
                "web_search_attempted": False,
                "web_search_triggered": False,
                "route": "degraded",
                "status": "web_fallback_unreachable",
                "timeout_stage": "web_fallback_unreachable",
                "degradation_reason": "infra_timeout",
                "exit_reason": "budget_exhausted",
                "errors": _errors(state) + ["web fallback budget unavailable"],
                "execution_trace": self._trace(state, "search_evidence_gaps", "blocked", started, "web_fallback_unreachable"),
            }
        gaps = self._evidence_gaps(state)
        errors = _errors(state)
        tool_calls = list(state.get("tool_calls", []))
        all_documents = []
        queries: list[str] = []
        search_errors: list[str] = []
        task_results = [dict(result) for result in state.get("task_results", [])]
        by_task = {str(result.get("id", "")): result for result in task_results}

        async def search_one(gap: dict[str, Any]) -> tuple[list[Any], str, list[str], Any | None]:
            query_parts = [
                str(state.get("question", "")),
                str(gap.get("query", "")),
                str(gap.get("objective", "")),
                "、".join(str(item) for item in gap.get("requirements", [])[:4]),
            ]
            query = "；".join(part.strip() for part in query_parts if part.strip())
            if not query:
                return [], query, [], None
            if query in queries:
                return [], query, [], None
            try:
                caller = getattr(self.services, "call_tool", None)
                if caller:
                    remaining = max(0.0, float(state.get("web_fallback_deadline", time.perf_counter())) - time.perf_counter())
                    if remaining <= 0:
                        raise TimeoutError("web fallback budget exhausted")
                    result = await asyncio.wait_for(caller("web_search", question=query), timeout=remaining)
                    if not result.success:
                        raise RuntimeError(result.error or "网络搜索工具失败")
                    return list(result.data or [])[:3], query, [], result
                found = await self.services.search(query)
                return list(found or [])[:3], query, [], None
            except Exception as exc:
                return [], query, [f"网络搜索失败（{query[:80]}）：{exc}"], None

        policy = self._policy()
        max_queries = (
            getattr(policy, "web_fallback_max", 0) or getattr(policy, "max_web_queries", 3)
            if policy else 3
        )
        for gap in gaps[:max_queries]:
            found, query, found_errors, tool_result = await search_one(gap)
            if query:
                queries.append(query)
            search_errors.extend(found_errors)
            if tool_result is not None:
                tool_calls.append({"tool": "web_search", **tool_result.as_dict()})
            found = [self._annotate_web_document(document, query) for document in found]
            task = by_task.get(str(gap.get("task_id", "")))
            if task is not None:
                task["documents"] = list(task.get("documents", [])) + found
                task["tool_calls"] = list(task.get("tool_calls", [])) + [
                    {"tool": "web_search", "query": query, "source": "evidence_gap"}
                ] if found else list(task.get("tool_calls", []))
            all_documents.extend(found)

        errors.extend(search_errors)
        return {
            "task_results": task_results,
            "documents": list(state.get("documents", [])) + all_documents,
            "local_documents": list(state.get("local_documents", state.get("documents", []))),
            "web_search_attempted": True,
            "web_search_triggered": bool(queries),
            "web_search_queries": list(state.get("web_search_queries", [])) + queries,
            "web_search_errors": list(state.get("web_search_errors", [])) + search_errors,
            "tool_calls": tool_calls,
            "route": "planned_web",
            "status": "web_hit" if all_documents else "web_empty",
            "errors": errors,
            "execution_trace": self._trace(
                state, "search_evidence_gaps", "success" if all_documents else "empty", started
            ),
        }
    async def audit_evidence(self, state: GraphState) -> GraphState:
        started = time.perf_counter()
        auditor = getattr(self.services, "audit_evidence", None)
        if not auditor:
            return {
                "evidence_matrix": [],
                "execution_trace": self._trace(state, "audit_evidence", "skipped", started),
            }
        try:
            audit = await auditor(state.get("problem_spec", {}), state.get("task_results", []))
            return {
                "evidence_matrix": audit.get("matrix", []),
                "requirement_coverage": round(
                    sum(item.get("requirement_coverage", 0.0) for item in audit.get("matrix", []))
                    / max(1, len(audit.get("matrix", []))), 3
                ),
                "web_search_triggered": state.get("web_search_triggered", False),
                "execution_trace": self._trace(state, "audit_evidence", "success", started),
            }
        except Exception as exc:
            return {
                "evidence_matrix": [],
                "errors": _errors(state) + [f"证据覆盖审计失败：{exc}"],
                "execution_trace": self._trace(state, "audit_evidence", "error", started, str(exc)),
            }

    async def retrieve(self, state: GraphState) -> GraphState:
        started = time.perf_counter()
        errors = _errors(state)
        tool_calls = list(state.get("tool_calls", []))
        try:
            caller = getattr(self.services, "call_tool", None)
            if caller:
                result = await caller("local_retrieval", question=state["question"])
                tool_calls = self._tool_calls(state, "local_retrieval", result)
                if not result.success:
                    raise RuntimeError(result.error or "本地检索工具失败")
                documents = result.data or []
            else:
                documents = await self.services.retrieve(state["question"])
        except Exception as exc:
            documents = []
            errors.append(f"本地知识库检索失败：{exc}")
            timeout = any(token in str(exc).lower() for token in ("超过", "timeout", "deadline", "timed out"))
            retrieval_failures = list(state.get("retrieval_failures", [])) + [{
                "stage": "local_retrieval",
                "status": "retrieval_timeout" if timeout else "retrieval_error",
                "error": str(exc),
            }]
        else:
            retrieval_failures = list(state.get("retrieval_failures", []))
        return {
            "documents": documents,
            "local_documents": documents,
            "generation": "",
            "sources": [],
            "route": "retrieving",
            "web_search_triggered": state.get("web_search_triggered", False),
            "web_search_attempted": state.get("web_search_attempted", False),
            "errors": errors,
            "tool_calls": tool_calls,
            "retrieval_failures": retrieval_failures,
            "timeout_stage": "local_retrieval" if retrieval_failures and retrieval_failures[-1].get("status") == "retrieval_timeout" else state.get("timeout_stage"),
            "retrieval_profile": {
                "embedding_seconds": 0.0,
                "dense_seconds": 0.0,
                "bm25_seconds": 0.0,
                "reranker_seconds": 0.0,
                "fusion_seconds": 0.0,
                "dedup_seconds": 0.0,
                "cache_hit": False,
                "error_type": "retrieval_timeout" if retrieval_failures and retrieval_failures[-1].get("status") == "retrieval_timeout" else None,
                "local_retrieval_seconds": round(float(tool_calls[-1].get("duration_seconds", 0.0)), 3)
                if tool_calls and tool_calls[-1].get("tool") == "local_retrieval" else 0.0,
                "timeout_stage": "local_retrieval" if retrieval_failures and retrieval_failures[-1].get("status") == "retrieval_timeout" else None,
                **(documents[0].metadata.get("retrieval_profile", {}) if documents else {}),
            },
            "execution_trace": self._trace(state, "retrieve", "success" if documents else "empty", started),
        }

    async def grade_documents(self, state: GraphState) -> GraphState:
        started = time.perf_counter()
        documents = _compact_documents(list(state.get("documents", [])))
        if not documents:
            return {
                "documents": [], "route": "web", "errors": _errors(state),
                "status": "local_retrieval_empty",
                "execution_trace": self._trace(state, "grade_documents", "empty", started),
            }

        errors = _errors(state)
        try:
            relevant = await self.services.grade(state["question"], documents)
        except Exception as exc:
            relevant = []
            errors.append(f"文档相关性评分失败：{exc}")
        statuses = {
            str(document.metadata.get("retrieval_evidence_status", "unknown"))
            for document in relevant
        }
        confidence, confidence_status, confidence_reason = confidence_from_documents(relevant)
        low_confidence = confidence_status in {"low", "uncertain"}
        needs_web = not relevant or statuses.issubset({"indirect", "scope_mismatch"}) or low_confidence
        confidence_error = (
            [f"本地检索置信度不足：{confidence_reason}，将补充精确/网络证据"]
            if low_confidence else []
        )
        return {
            "documents": relevant,
            "route": "web" if needs_web else "local",
            "errors": errors + confidence_error,
            "status": "local_indirect" if needs_web and relevant else "local_hit" if relevant else "local_irrelevant",
            "confidence": confidence,
            "confidence_status": confidence_status,
            "confidence_reason": confidence_reason,
            "fallback_reason": confidence_reason if low_confidence else state.get("fallback_reason"),
            "execution_trace": self._trace(state, "grade_documents", "success" if relevant else "empty", started),
        }

    def decide_next_step(self, state: GraphState) -> str:
        if state.get("documents") and state.get("route") == "local":
            return "generate"
        return "generate" if not self._web_allowed() else "web_search"

    async def web_search(self, state: GraphState) -> GraphState:
        started = time.perf_counter()
        errors = _errors(state)
        if not self._web_allowed():
            return {
                "documents": list(state.get("documents", [])),
                "local_documents": list(state.get("local_documents", state.get("documents", []))),
                "route": "web_disabled",
                "web_search_triggered": False,
                "web_search_attempted": False,
                "web_search_errors": list(state.get("web_search_errors", [])) + ["web_disabled"],
                "errors": errors + ["web_disabled: 当前评测策略禁止网络搜索"],
                "status": "web_disabled",
                "web_search_blocked": True,
                "execution_trace": self._trace(state, "web_search", "blocked", started, "web_disabled"),
            }
        web_deadline = state.get("web_fallback_deadline")
        if web_deadline is not None and time.perf_counter() >= float(web_deadline):
            return {
                "documents": list(state.get("documents", [])),
                "route": "degraded",
                "web_search_triggered": False,
                "web_search_attempted": False,
                "status": "web_fallback_unreachable",
                "timeout_stage": "web_fallback_unreachable",
                "degradation_reason": "infra_timeout",
                "exit_reason": "budget_exhausted",
                "errors": errors + ["web fallback budget unavailable"],
                "execution_trace": self._trace(state, "web_search", "blocked", started, "web_fallback_unreachable"),
            }
        tool_calls = list(state.get("tool_calls", []))
        try:
            caller = getattr(self.services, "call_tool", None)
            if caller:
                remaining = max(0.0, float(web_deadline) - time.perf_counter()) if web_deadline is not None else None
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("web fallback budget exhausted")
                call = caller("web_search", question=state["question"])
                result = await asyncio.wait_for(call, timeout=remaining) if remaining is not None else await call
                tool_calls = self._tool_calls(state, "web_search", result)
                if not result.success:
                    raise RuntimeError(result.error or "网络搜索工具失败")
                documents = result.data or []
            else:
                documents = await self.services.search(state["question"])
        except Exception as exc:
            documents = []
            errors.append(f"网络搜索失败：{exc}")
        local_documents = list(state.get("documents") or state.get("local_documents", []))
        merged_documents = local_documents + [
            self._annotate_web_document(document, state["question"]) for document in documents
        ]
        return {
            "documents": merged_documents,
            "local_documents": local_documents,
            "route": "web",
            "web_search_triggered": True,
            "web_search_attempted": True,
            "web_search_queries": list(state.get("web_search_queries", [])) + [state["question"]],
            "errors": errors,
            "status": "web_hit" if documents else "web_empty",
            "tool_calls": tool_calls,
            "execution_trace": self._trace(state, "web_search", "success" if documents else "empty", started),
        }

    async def generate(self, state: GraphState) -> GraphState:
        started = time.perf_counter()
        documents = state.get("documents", [])
        errors = _errors(state)
        try:
            generation = await self.services.generate(state["question"], documents)
        except Exception as exc:
            errors.append(f"答案生成失败：{exc}")
            generation = build_fallback_answer(state["question"], documents)
            """legacy fallback below"""
            generation_legacy = (
                NO_EVIDENCE_ANSWER
                if not documents
                else "资料已经找到，但答案生成服务暂时不可用，请稍后重试。"
            )
        audit = {}
        if generation:
            try:
                audit = await self._audit_generation(generation, documents, state.get("problem_spec", {}))
            except Exception as exc:
                errors.append(f"主张证据审计失败：{exc}")
        decision = delivery_decision(generation, documents, audit)
        return {
            "generation": generation,
            "token_count": _estimate_tokens(state["question"], generation),
            "model_calls": list(state.get("model_calls", [])) + [model_descriptor("generator")],
            "sources": build_sources(documents),
            "errors": errors,
            "status": decision["delivery_status"] if documents else state.get("status", "no_evidence"),
            **decision,
            "audit_warnings": audit.get("warnings", []),
            "rewrite_attempts": state.get("rewrite_attempts", 0),
            "evidence_policy": {"direct_only_for_facts": True, "indirect_is_lead_only": True},
            "claims": audit.get("claims", []),
            "source_assessments": audit.get("source_assessments", []),
            "conflicts": audit.get("conflicts", []),
            "claim_support_rate": audit.get("claim_support_rate", 0.0),
            "unsupported_claims": audit.get("unsupported_claims", []),
            "recommendation_conditions": audit.get("recommendation_conditions", []),
            "decision_metrics": decision_quality_metrics(audit),
            "execution_trace": self._trace(state, "generate", "success", started),
        }

    async def validate_delivery(self, state: GraphState) -> GraphState:
        """Perform one strict rewrite, then refuse instead of exposing an audited failure."""
        started = time.perf_counter()
        documents = list(state.get("documents", []))
        generation = str(state.get("generation", ""))
        blocking = list(state.get("blocking_issues", []))
        if state.get("deliverable") or not documents or not blocking:
            return {"execution_trace": self._trace(state, "validate_delivery", "accepted", started)}
        attempts = int(state.get("rewrite_attempts", 0))
        if attempts < 1:
            strict_question = (
                f"{state.get('question', '')}\n\n"
                "严格重写要求：只使用上下文中列出的允许引用；direct 才能写成资料事实；"
                "推荐必须写明适用条件、风险/限制、验证要求，并在证据不足时明确拒答。"
            )
            try:
                rewritten = await self.services.generate(strict_question, documents)
                audit = await self._audit_generation(rewritten, documents, state.get("problem_spec", {}))
                decision = delivery_decision(rewritten, documents, audit)
                if decision["deliverable"]:
                    return {
                        "generation": rewritten,
                        **decision,
                        "rewrite_attempts": 1,
                        "audit_warnings": audit.get("warnings", []),
                        "claims": audit.get("claims", []),
                        "source_assessments": audit.get("source_assessments", []),
                        "conflicts": audit.get("conflicts", []),
                        "unsupported_claims": audit.get("unsupported_claims", []),
                        "recommendation_conditions": audit.get("recommendation_conditions", []),
                        "claim_support_rate": audit.get("claim_support_rate", 0.0),
                        "decision_metrics": decision_quality_metrics(audit),
                        "execution_trace": self._trace(state, "validate_delivery", "rewritten", started),
                    }
                blocking = decision["blocking_issues"]
                generation = build_refusal_answer(blocking, documents)
                return {
                    "generation": generation,
                    **decision,
                    "rewrite_attempts": 1,
                    "audit_warnings": audit.get("warnings", []),
                    "claims": audit.get("claims", []),
                    "source_assessments": audit.get("source_assessments", []),
                    "conflicts": audit.get("conflicts", []),
                    "unsupported_claims": audit.get("unsupported_claims", []),
                    "recommendation_conditions": audit.get("recommendation_conditions", []),
                    "claim_support_rate": audit.get("claim_support_rate", 0.0),
                    "decision_metrics": decision_quality_metrics(audit),
                    "execution_trace": self._trace(state, "validate_delivery", "refused", started),
                }
            except Exception as exc:
                blocking.append({"status": "rewrite_failed", "reason": str(exc)})
        refusal = build_refusal_answer(blocking, documents)
        return {
            "generation": refusal,
            "deliverable": False,
            "delivery_status": "degraded_insufficient_evidence",
            "status": "degraded_insufficient_evidence",
            "blocking_issues": blocking,
            "rewrite_attempts": attempts,
            "execution_trace": self._trace(state, "validate_delivery", "refused", started),
        }
