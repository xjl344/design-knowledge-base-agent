"""External service boundary for production code and deterministic tests."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Protocol

from langchain_core.documents import Document

from src.generator import generate_answer
from src.grader import grade_documents
from src.retriever import _lexical_terms, retrieve_documents_multi
from src.web_search import web_search
from config import settings
from src.tools import ToolRegistry
from src.tools.builtin import register_builtin_tools
from src.planning import heuristic_plan
from src.planning import parse_structured_analysis
from src.resilience import CircuitBreaker
from src.evidence import audit_claims
from src.embeddings import preload_embeddings
from langchain_core.prompts import ChatPromptTemplate
from src.model_clients import chat_model


@dataclass(frozen=True)
class EvaluationPolicy:
    """Execution limits shared by evaluation services and graph nodes."""

    allow_web: bool = True
    allow_planning: bool = True
    local_timeout_seconds: float = 30.0
    max_web_queries: int = 3
    max_retrieval_tasks: int = 3
    query_decompose_enabled: bool = True
    shared_retrieval_budget_seconds: float | None = None
    web_fallback_max: int = 0
    web_fallback_budget_seconds: float = 0.0
    calculation_timeout_seconds: float = 5.0
    # ``local_timeout_seconds`` is retained as a backwards-compatible alias;
    # this is the deadline for the whole local retrieval operation.
    local_total_timeout_seconds: float | None = None
    planner_timeout_seconds: float = 15.0
    total_pipeline_timeout_seconds: float = 120.0
    cancel_on_timeout: bool = True
    offline_mode: bool = False
    allow_web_cache: bool = True


class Services(Protocol):
    async def retrieve(self, question: str) -> list[Document]: ...

    async def grade(
        self, question: str, documents: list[Document]
    ) -> list[Document]: ...

    async def search(self, question: str) -> list[Document]: ...

    async def search_evidence_gaps(
        self, question: str, gaps: list[dict]
    ) -> dict: ...

    async def generate(self, question: str, documents: list[Document]) -> str: ...

    async def analyze_complexity(self, question: str) -> dict: ...

    async def execute_subtasks(self, tasks: list[dict]) -> list[dict]: ...

    async def aggregate_answers(self, question: str, results: list[dict]) -> str: ...

    async def audit_evidence(self, problem_spec: dict, results: list[dict]) -> dict: ...

    async def audit_answer(self, answer: str, documents: list[Document], problem_spec: dict) -> dict: ...


class DefaultServices:
    def __init__(self, policy: EvaluationPolicy | None = None) -> None:
        self.policy = policy or EvaluationPolicy(
            local_timeout_seconds=settings.local_retrieval_timeout_seconds,
            max_web_queries=settings.max_web_queries,
            max_retrieval_tasks=settings.max_retrieval_tasks,
            local_total_timeout_seconds=settings.local_retrieval_total_timeout_seconds,
            planner_timeout_seconds=settings.planner_timeout_seconds,
            total_pipeline_timeout_seconds=settings.total_pipeline_timeout_seconds,
            cancel_on_timeout=settings.cancel_on_timeout,
            offline_mode=settings.offline_mode,
            allow_web_cache=settings.allow_web_cache,
            query_decompose_enabled=settings.query_decompose_enabled,
            shared_retrieval_budget_seconds=settings.local_retrieval_total_timeout_seconds,
            web_fallback_max=settings.max_web_queries,
        )
        if self.policy.offline_mode:
            # Keep local evaluation traces local even when the process imported
            # LangChain before the evaluation profile was constructed.
            os.environ["LANGCHAIN_TRACING_V2"] = "false"
            os.environ["LANGCHAIN_CALLBACKS_BACKGROUND"] = "false"
            os.environ["LANGCHAIN_ENDPOINT"] = ""
        self.tool_registry = ToolRegistry()
        register_builtin_tools(
            self.tool_registry,
            timeout_seconds=settings.tool_timeout_seconds,
            local_retrieval_timeout_seconds=(self.policy.local_total_timeout_seconds or self.policy.local_timeout_seconds),
            max_retries=settings.tool_max_retries,
            allow_web=self.policy.allow_web,
            query_decompose_enabled=self.policy.query_decompose_enabled,
        )
        self.planner_breaker = CircuitBreaker()
        self.grader_breaker = CircuitBreaker()
        self.generator_breaker = CircuitBreaker()
        self.last_planner_status = "not_attempted"
        self.progress_snapshot: dict[str, Any] = {}
        self.embedding_preload_ok = None
        if settings.embedding_preload:
            self.embedding_preload_ok = preload_embeddings()

    async def call_tool(self, name: str, **kwargs):
        return await self.tool_registry.call(name, **kwargs)

    async def retrieve(self, question: str) -> list[Document]:
        result = await self.call_tool("local_retrieval", question=question)
        if not result.success:
            raise RuntimeError(result.error or "local retrieval failed")
        return result.data or []

    async def grade(
        self, question: str, documents: list[Document]
    ) -> list[Document]:
        try:
            return await self.grader_breaker.call(grade_documents, question, documents)
        except Exception:
            # A deterministic fallback keeps the local path usable when the LLM is unavailable.
            terms = [term.lower() for term in _lexical_terms(question)]
            scored = []
            for document in documents:
                if document.metadata.get("retrieval_evidence_status") == "scope_mismatch":
                    continue
                searchable = " ".join([
                    str(document.page_content or ""),
                    str(document.metadata.get("source", "")),
                    str(document.metadata.get("title", "")),
                ]).lower()
                score = sum(searchable.count(term) for term in terms)
                if score:
                    scored.append((score, document))
            scored.sort(key=lambda item: item[0], reverse=True)
            relevant = [document for _, document in scored]
            if relevant:
                direct = [
                    document for document in relevant
                    if document.metadata.get("retrieval_evidence_status") == "direct"
                ]
                return direct + [document for document in relevant if document not in direct]
            usable = [
                document for document in documents
                if document.metadata.get("retrieval_evidence_status") != "scope_mismatch"
            ]
            return usable[:1]

    async def search(self, question: str) -> list[Document]:
        if not self.policy.allow_web:
            raise RuntimeError("web_disabled: 当前评测策略禁止网络搜索")
        result = await self.call_tool("web_search", question=question)
        if not result.success:
            raise RuntimeError(result.error or "web search failed")
        return result.data or []

    async def search_evidence_gaps(self, question: str, gaps: list[dict]) -> dict:
        """Search only the highest-priority evidence gaps once each."""
        documents: list[Document] = []
        queries: list[str] = []
        errors: list[str] = []
        if not self.policy.allow_web:
            return {"documents": [], "queries": [], "errors": ["web_disabled: 当前评测策略禁止网络搜索"]}
        max_queries = self.policy.web_fallback_max or self.policy.max_web_queries
        for gap in gaps[: max_queries]:
            query_parts = [
                question,
                str(gap.get("query", "")),
                str(gap.get("objective", "")),
                "、".join(str(item) for item in gap.get("requirements", [])[:4]),
            ]
            query = "；".join(part.strip() for part in query_parts if part.strip())
            if not query or query in queries:
                continue
            queries.append(query)
            try:
                found = await self.search(query)
                documents.extend(found[:3])
            except Exception as exc:
                errors.append(f"网络搜索失败（{query[:80]}）：{exc}")
        return {"documents": documents, "queries": queries, "errors": errors}

    async def generate(self, question: str, documents: list[Document]) -> str:
        return await self.generator_breaker.call(generate_answer, question, documents)

    async def analyze_complexity(self, question: str) -> dict:
        fallback = heuristic_plan(question)
        local_planner = settings.planner_backend == "local" or os.getenv("RAG_LOCAL_MODEL", "false").lower() == "true"
        if fallback.get("question_type") == "simple" or (not settings.llm_api_key and not local_planner):
            self.last_planner_status = "not_attempted"
            return fallback
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是设计领域通用任务规划器。只分析问题结构，不引入问题中没有的领域事实。"
                "输出严格 JSON，字段为：intent、object、task_mode、constraints、criteria、"
                "requested_outputs、explicit_references、candidates、assumptions、evidence_requirements、confidence。"
                "intent 只能是 simple、comparison、recommendation、analysis、complex 之一；"
                "所有列表字段必须是字符串数组；不要输出 Markdown。",
            ),
            ("human", "用户问题：{question}"),
        ])
        try:
            llm = chat_model("planner", temperature=0.0)
            planner_call = self.planner_breaker.call((prompt | llm).ainvoke, {"question": question})
            response = await asyncio.wait_for(planner_call, timeout=self.policy.planner_timeout_seconds)
            raw = response.content if hasattr(response, "content") else str(response)
            self.last_planner_status = "completed"
            return parse_structured_analysis(str(raw), question)
        except asyncio.TimeoutError:
            self.last_planner_status = "timeout"
            return fallback
        except Exception:
            self.last_planner_status = "failed"
            return fallback

    async def execute_subtasks(self, tasks: list[dict]) -> list[dict]:
        tasks = list(tasks)[: self.policy.max_retrieval_tasks]
        started = asyncio.get_running_loop().time()
        budget = self.policy.shared_retrieval_budget_seconds
        deadline = started + budget if budget and budget > 0 else None
        task_map = {task.get("id", f"task-{index}"): task for index, task in enumerate(tasks, 1)}
        completed: dict[str, dict] = {}

        async def execute(task: dict) -> dict:
            task_id = task.get("id", "")
            if task_id in completed:
                return completed[task_id]
            dependencies = [task_map[dep] for dep in task.get("dependencies", []) if dep in task_map]
            if dependencies:
                await asyncio.gather(*(execute(dep) for dep in dependencies))
            operation = task.get("operation", "retrieve")
            if operation not in {"retrieve", "validate"}:
                dependency_results = [completed.get(dep.get("id"), {}) for dep in dependencies]
                documents = []
                seen: set[str] = set()
                for result in dependency_results:
                    for document in result.get("documents", []):
                        key = document.metadata.get("chunk_id") or str(id(document))
                        if key not in seen:
                            seen.add(key)
                            documents.append(document)
                result = {**task, "status": "completed", "documents": documents, "error": None, "tool_calls": []}
                completed[task_id] = result
                return result
            try:
                task_question = task.get("query") or task.get("question") or ""
                if deadline is not None:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        result = {**task, "status": "skipped", "documents": [], "error": "shared retrieval budget exhausted", "tool_calls": []}
                        completed[task_id] = result
                        return result
                    retrieval = await asyncio.wait_for(
                        self.call_tool("local_retrieval", question=task_question),
                        timeout=remaining,
                    )
                else:
                    retrieval = await self.call_tool("local_retrieval", question=task_question)
                if not retrieval.success:
                    result = {
                        **task,
                        "status": "timeout" if any(token in str(retrieval.error or "").lower() for token in ("timeout", "超时", "deadline")) else "failed",
                        "documents": [],
                        "error": retrieval.error or "子任务检索失败",
                        "tool_calls": [{"tool": "local_retrieval", **retrieval.as_dict()}],
                    }
                    completed[task_id] = result
                    return result
                docs = retrieval.data or []
                relevant = await self.grade(task_question, docs) if docs else []
                if docs and not relevant:
                    relevant = docs
                result = {
                    **task,
                    "status": "completed",
                    "documents": relevant,
                    "error": None,
                    "tool_calls": [{"tool": "local_retrieval", **retrieval.as_dict()}],
                }
                completed[task_id] = result
                return result
            except Exception as exc:
                result = {**task, "status": "timeout" if any(token in str(exc).lower() for token in ("deadline", "timeout", "超时", "超过")) else "failed", "documents": [], "error": str(exc), "tool_calls": [{"tool": "local_retrieval", "success": False, "error": str(exc), "duration_seconds": 0.0, "attempts": 1}]}
                completed[task_id] = result
                return result

        return list(await asyncio.gather(*(execute(task) for task in tasks)))

    async def audit_evidence(self, problem_spec: dict, results: list[dict]) -> dict:
        """Build task and requirement-level coverage from retrieved evidence."""
        explicit = problem_spec.get("explicit_references", [])
        matrix = []
        for result in results:
            documents = result.get("documents", [])
            locations = [str(document.metadata.get("source", "")) for document in documents]
            titles = [str(document.metadata.get("title", "")) for document in documents]
            evidence = list(result.get("evidence_requirements", []))
            if not evidence:
                evidence = list(problem_spec.get("requirements", []))
            source_text = " ".join(locations + titles)
            reference_hits = [ref for ref in explicit if ref.lower().replace(" ", "") in source_text.lower().replace(" ", "")]
            evidence_statuses = [
                str(document.metadata.get("retrieval_evidence_status", "unknown"))
                for document in documents
            ]
            direct_count = evidence_statuses.count("direct")
            indirect_count = evidence_statuses.count("indirect")
            mismatch_count = evidence_statuses.count("scope_mismatch")
            requirement_details = []
            for index, requirement in enumerate(evidence, 1):
                req_text = str(requirement).strip()
                tokens = [token for token in _lexical_terms(req_text) if len(token) > 1]
                supporting = []
                for document in documents:
                    haystack = f"{document.page_content} {document.metadata.get('source', '')} {document.metadata.get('title', '')}".lower()
                    if not tokens or sum(token.lower() in haystack for token in tokens) >= max(1, min(2, len(tokens))):
                        supporting.append(document)
                direct_support = [doc for doc in supporting if doc.metadata.get("retrieval_evidence_status") == "direct"]
                mismatch_support = [doc for doc in supporting if doc.metadata.get("retrieval_evidence_status") == "scope_mismatch"]
                if direct_support:
                    req_status = "covered"
                elif mismatch_support:
                    req_status = "conflict"
                elif supporting:
                    req_status = "partial"
                else:
                    req_status = "missing"
                requirement_details.append({
                    "id": f"{result.get('id', 'task')}-req-{index}",
                    "text": req_text,
                    "status": req_status,
                    "supporting_chunks": [str(doc.metadata.get("chunk_id") or doc.metadata.get("source", "")) for doc in supporting],
                    "citations": [str(doc.metadata.get("source", "")) for doc in supporting],
                })
            covered_requirements = sum(item["status"] == "covered" for item in requirement_details)
            if requirement_details and covered_requirements == len(requirement_details):
                status = "covered"
                reason = None
            elif requirement_details and covered_requirements:
                status = "partial"
                reason = "部分 requirement 已有直接证据"
            elif result.get("operation") == "validate" and explicit and not reference_hits:
                status = "missing" if not documents else "partial"
                reason = "检索结果未命中用户指定来源"
            elif direct_count:
                status = "covered"
                reason = None
            elif indirect_count or mismatch_count or documents:
                status = "partial"
                reason = "已找到相关背景或范围不匹配资料，但缺少满足任务要求的直接证据"
            else:
                status = "missing"
                reason = "未找到满足任务要求的直接资料"
            matrix.append({
                "task_id": result.get("id"),
                "objective": result.get("objective", ""),
                "requirements": evidence,
                "requirement_details": requirement_details,
                "requirement_coverage": round(covered_requirements / len(requirement_details), 3) if requirement_details else (1.0 if status == "covered" else 0.0),
                "sources": locations,
                "coverage": status,
                "evidence_statuses": evidence_statuses,
                "evidence_levels": sorted({
                    str(document.metadata.get("evidence_level", "unknown"))
                    for document in documents
                }),
                "confidence": 1.0 if status == "covered" else 0.5 if status == "partial" else 0.0,
                "missing_reason": reason,
            })
        return {
            "matrix": matrix,
            "covered": sum(item["coverage"] == "covered" for item in matrix),
            "total": len(matrix),
        }

    async def audit_answer(self, answer: str, documents: list[Document], problem_spec: dict) -> dict:
        return audit_claims(answer, documents, problem_spec)

    async def aggregate_answers(self, question: str, results: list[dict]) -> str:
        documents: list[Document] = []
        seen: set[str] = set()
        for result in results:
            for document in result.get("documents", []):
                key = document.metadata.get("chunk_id") or str(id(document))
                if key not in seen:
                    seen.add(key)
                    documents.append(document)
        return await self.generate(question, documents)
