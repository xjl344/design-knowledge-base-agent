"""Compile the CRAG + Web Search LangGraph."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.graph_nodes import GraphNodes
from src.graph_state import GraphState
from src.services import DefaultServices, Services
from config import settings


def build_graph(services: Services | None = None):
    nodes = GraphNodes(services or DefaultServices())
    workflow = StateGraph(GraphState)

    def after_analysis(state: GraphState) -> str:
        if state.get("question_type") == "calculation":
            return "calculation"
        policy = getattr(nodes.services, "policy", None)
        allow_planning = True if policy is None else bool(getattr(policy, "allow_planning", True))
        if settings.agent_planning_enabled and allow_planning and state.get("question_type") != "simple":
            return "create_plan"
        return "retrieve"

    workflow.add_node("analyze_question", nodes.analyze_question)
    workflow.add_node("create_plan", nodes.create_plan)
    workflow.add_node("execute_plan", nodes.execute_plan)
    workflow.add_node("audit_evidence", nodes.audit_evidence)
    workflow.add_node("search_evidence_gaps", nodes.search_evidence_gaps)
    workflow.add_node("aggregate_results", nodes.aggregate_results)
    workflow.add_node("retrieve", nodes.retrieve)
    workflow.add_node("calculation", nodes.calculation)
    workflow.add_node("grade_documents", nodes.grade_documents)
    workflow.add_node("web_search", nodes.web_search)
    workflow.add_node("generate", nodes.generate)
    workflow.add_node("validate_delivery", nodes.validate_delivery)

    workflow.add_edge(START, "analyze_question")
    workflow.add_conditional_edges(
        "analyze_question",
        after_analysis,
        {"create_plan": "create_plan", "retrieve": "retrieve", "calculation": "calculation"},
    )
    workflow.add_edge("create_plan", "execute_plan")
    workflow.add_conditional_edges(
        "execute_plan",
        lambda state: "audit_evidence" if state.get("task_results") else "retrieve",
        {"audit_evidence": "audit_evidence", "retrieve": "retrieve"},
    )
    workflow.add_conditional_edges(
        "audit_evidence",
        nodes.decide_after_evidence_audit,
        {"search_evidence_gaps": "search_evidence_gaps", "aggregate_results": "aggregate_results"},
    )
    workflow.add_edge("search_evidence_gaps", "audit_evidence")
    workflow.add_conditional_edges(
        "aggregate_results",
        lambda state: "validate_delivery" if state.get("generation") else "retrieve",
        {"validate_delivery": "validate_delivery", "retrieve": "retrieve"},
    )
    workflow.add_edge("retrieve", "grade_documents")
    workflow.add_edge("calculation", "validate_delivery")
    workflow.add_conditional_edges(
        "grade_documents",
        nodes.decide_next_step,
        {"generate": "generate", "web_search": "web_search"},
    )
    workflow.add_edge("web_search", "generate")
    workflow.add_edge("generate", "validate_delivery")
    workflow.add_edge("validate_delivery", END)
    return workflow.compile()
