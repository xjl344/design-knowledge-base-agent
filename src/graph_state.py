"""State shared by the CRAG workflow nodes."""

from __future__ import annotations

from typing import Any, TypedDict

from langchain_core.documents import Document


class GraphState(TypedDict, total=False):
    question: str
    documents: list[Document]
    local_documents: list[Document]
    generation: str
    sources: list[dict[str, Any]]
    route: str
    web_search_triggered: bool
    web_search_attempted: bool
    web_search_queries: list[str]
    web_search_errors: list[str]
    errors: list[str]
    question_type: str
    problem_spec: dict[str, Any]
    task_specs: list[dict[str, Any]]
    plan_id: str
    sub_tasks: list[dict[str, Any]]
    task_results: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    execution_trace: list[dict[str, Any]]
    metrics: list[dict[str, Any]]
    status: str
    session_id: str
    token_count: int
    token_usage: dict[str, Any]
    model_calls: list[dict[str, Any]]
    fallback_reason: str | None
    confidence: float | None
    confidence_status: str
    confidence_reason: str | None
    evidence_matrix: list[dict[str, Any]]
    requirement_coverage: float
    unsupported_claims: list[str]
    answer_sections: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    source_assessments: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    claim_support_rate: float
    recommendation_conditions: list[dict[str, Any]]
    decision_metrics: dict[str, float]
    deliverable: bool
    delivery_status: str
    blocking_issues: list[dict[str, Any]]
    blocking_issue_summary: dict[str, int]
    allowed_citations: list[str]
    rewrite_attempts: int
    evidence_policy: dict[str, Any]
    audit_warnings: list[dict[str, Any]]
    retrieval_budget: dict[str, int | float]
    retrieval_failures: list[dict[str, Any]]
    timeout_stage: str | None
    retrieval_profile: dict[str, Any]
    calculation_result: dict[str, Any]
    web_search_blocked: bool
    offline_mode: bool
    execution_counters: dict[str, Any]
    degradation_reason: str
    exit_reason: str
    pipeline_deadline: float
    local_deadline: float
    web_fallback_deadline: float
    last_completed_node: str
    active_node: str
