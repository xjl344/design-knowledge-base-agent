"""Gradio entry point for the design knowledge assistant."""

from __future__ import annotations

import html
import os
import uuid
from typing import Any
from urllib.parse import urlparse

from config import ensure_runtime_dirs, settings
import gradio as gr

from src.graph_builder import build_graph
from src.checkpoint import CheckpointManager
from src.resilience import CostLimit, CostTracker, RequestLimiter


ensure_runtime_dirs()
crag_app = build_graph()
request_limiter = RequestLimiter(settings.max_concurrent_requests)
cost_tracker = CostTracker(CostLimit(settings.per_query_token_limit, settings.daily_token_limit))
checkpoints = CheckpointManager(settings.checkpoint_dir)


def _chunk_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", "")) if isinstance(block, dict) else str(block)
            for block in content
        )
    return ""


def format_route(state: dict[str, Any]) -> str:
    if state.get("web_search_triggered") and state.get("route") == "planned_web":
        return "**检索路径：** Agent 规划 → 本地检索 → 证据缺口联网补充"
    if state.get("web_search_triggered"):
        return "**检索路径：** 本地知识库 → 网络搜索补充"
    if state.get("route") == "local":
        return "**检索路径：** 本地知识库"
    if state.get("route") in {"planning", "planned"}:
        return "**检索路径：** Agent 规划与并行检索"
    return "**检索路径：** 未获得资料"


def format_sources(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return "**参考来源**\n\n暂无可用来源。"

    lines = ["**参考来源**"]
    for source in sources:
        source_id = html.escape(str(source["id"]))
        title = html.escape(str(source.get("title") or "未知来源"))
        location = str(source.get("location") or "未知来源")
        page = source.get("page")
        page_text = f"，第 {page} 页" if page else ""
        evidence_status = source.get("retrieval_evidence_status", "unknown")
        status_text = f"，证据状态：{html.escape(str(evidence_status))}"
        if source.get("type") == "web" and urlparse(location).scheme in {"http", "https"}:
            lines.append(f"- [{source_id}] [{title}]({location}){status_text}（网络资料，需核验原文）")
        else:
            lines.append(f"- [{source_id}] {title}{page_text}{status_text}：`{html.escape(location)}`")
    return "\n".join(lines)


def format_errors(errors: list[str]) -> str:
    if not errors:
        return "**运行状态：** 正常"
    escaped = "\n".join(f"- {html.escape(error)}" for error in errors)
    return f"**运行状态：** 部分能力降级\n\n{escaped}"


def format_delivery_status(state: dict[str, Any]) -> str:
    """Expose delivery semantics separately from infrastructure errors."""
    deliverable = state.get("deliverable")
    status = state.get("delivery_status") or state.get("status")
    if deliverable is True and status == "completed":
        return "**状态：** 可交付\n\n**可交付：** 是"
    issues = state.get("blocking_issues", [])
    lines = ["**状态：** 证据不足，已降级", "", "**可交付：** 否"]
    if issues:
        lines.extend(["", "**阻断原因：**"])
        seen = set()
        for issue in issues[:8]:
            key = (issue.get("status"), issue.get("reason"))
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- `{html.escape(str(issue.get('status', 'unknown')))}`：{html.escape(str(issue.get('reason', '需要补充证据')))}")
    return "\n".join(lines)


def format_no_evidence_status(state: dict[str, Any]) -> str:
    """Explain why no source was available instead of blaming the document set."""
    errors = state.get("errors", [])
    status = state.get("status")
    if errors:
        return format_errors(errors)
    if status == "local_retrieval_empty":
        return (
            "**运行状态：** 本地知识库没有返回结果\n\n"
            "请检查 Chroma 数据库是否已建立，或尝试更具体的关键词。"
        )
    if status == "web_empty":
        return "**运行状态：** 本地知识库和网络搜索都没有返回结果"
    if state.get("web_search_attempted") and state.get("web_search_errors"):
        return "**运行状态：** 已尝试网络补充，但搜索部分失败；回答中的网络资料需核验"
    return "**运行状态：** 没有可用参考资料"


def format_metrics(state: dict[str, Any], session_id: str) -> dict[str, Any]:
    trace = state.get("execution_trace", [])
    return {
        "session_id": session_id,
        "status": state.get("status", "unknown"),
        "delivery_status": state.get("delivery_status", state.get("status", "unknown")),
        "deliverable": state.get("deliverable", False),
        "blocking_issues": state.get("blocking_issues", []),
        "allowed_citations": state.get("allowed_citations", []),
        "rewrite_attempts": state.get("rewrite_attempts", 0),
        "route": state.get("route", "unknown"),
        "question_type": state.get("question_type", "simple"),
        "problem_spec": state.get("problem_spec", {}),
        "task_specs": state.get("task_specs", []),
        "plan_id": state.get("plan_id"),
        "sub_tasks": state.get("sub_tasks", []),
        "tool_calls": state.get("tool_calls", []),
        "web_search_attempted": state.get("web_search_attempted", False),
        "web_search_triggered": state.get("web_search_triggered", False),
        "web_search_queries": state.get("web_search_queries", []),
        "web_search_errors": state.get("web_search_errors", []),
        "execution_trace": trace,
        "total_duration_seconds": round(sum(item.get("duration_seconds", 0) for item in trace), 3),
        "slowest_node": max(trace, key=lambda item: item.get("duration_seconds", 0)).get("node") if trace else None,
        "token_count_estimate": state.get("token_count", 0),
        "token_usage": state.get("token_usage", {}),
        "model_calls": state.get("model_calls", []),
        "fallback_reason": state.get("fallback_reason"),
        "confidence": state.get("confidence"),
        "confidence_status": state.get("confidence_status"),
        "confidence_reason": state.get("confidence_reason"),
        "chunking_strategy": settings.chunking_strategy,
        "evidence_matrix": state.get("evidence_matrix", []),
        "requirement_coverage": state.get("requirement_coverage", 0.0),
        "claims": state.get("claims", []),
        "source_assessments": state.get("source_assessments", []),
        "conflicts": state.get("conflicts", []),
        "claim_support_rate": state.get("claim_support_rate", 0.0),
        "unsupported_claims": state.get("unsupported_claims", []),
        "recommendation_conditions": state.get("recommendation_conditions", []),
        "decision_metrics": state.get("decision_metrics", {}),
        "direct_evidence_count": sum(1 for item in state.get("sources", []) if item.get("retrieval_evidence_status") == "direct"),
        "indirect_evidence_count": sum(1 for item in state.get("sources", []) if item.get("retrieval_evidence_status") == "indirect"),
    }


async def chat(message: str, history: list[dict[str, Any]]):
    del history  # The MVP deliberately treats every question as a single turn.
    partial_answer = ""
    session_id = str(uuid.uuid4())
    final_state: dict[str, Any] = {"question": message, "session_id": session_id}

    try:
        async with request_limiter.semaphore:
            async for mode, payload in crag_app.astream(
                {"question": message, "session_id": session_id}, stream_mode=["messages", "updates"]
            ):
                if mode == "messages":
                    chunk, metadata = payload
                    if metadata.get("langgraph_node") == "generate":
                        text = _chunk_text(chunk.content)
                        if text:
                            partial_answer += text
                            yield (
                                partial_answer,
                                "**检索路径：** 正在处理",
                                "**参考来源**\n\n正在整理来源...",
                                "**运行状态：** 正在生成",
                                format_metrics(final_state, session_id),
                            )
                elif mode == "updates":
                    for update in payload.values():
                        if isinstance(update, dict):
                            final_state.update(update)
    except Exception as exc:
        final_state.setdefault("errors", []).append(f"工作流运行失败：{exc}")
        final_state.setdefault("generation", "系统暂时无法完成回答，请检查配置后重试。")

    answer = final_state.get("generation") or partial_answer or "未生成有效答案。"
    token_estimate = max(1, len(message + answer) // 4)
    final_state["token_count"] = token_estimate
    try:
        cost_tracker.check_and_record(token_estimate)
    except RuntimeError as exc:
        final_state.setdefault("errors", []).append(str(exc))
    checkpoints.save(session_id, final_state)
    delivery_text = format_delivery_status(final_state)
    infrastructure_text = format_no_evidence_status(final_state) if not final_state.get("sources") else format_errors(final_state.get("errors", []))
    status_text = delivery_text + "\n\n" + infrastructure_text
    yield (
        answer,
        format_route(final_state),
        format_sources(final_state.get("sources", [])),
        status_text,
        format_metrics(final_state, session_id),
    )


CSS = """
.gradio-container { max-width: 1240px !important; }
/* The execution-trace panel lives in a right-hand sidebar, so it no longer needs the
   hand-rolled left border that the old two-column Row used. */
#status-column { border-left: 1px solid var(--border-color-primary); }
"""


with gr.Blocks(title="设计知识库助手") as demo:
    # The trace panel is a `gr.Sidebar`, not a column of a `gr.Row`.
    #
    # The previous layout put `chatbot = gr.Chatbot(...)` inside a Row and passed it to
    # `gr.ChatInterface(chatbot=chatbot)`.  ChatInterface re-renders the chatbot inside its
    # own layout, so the Row's left column stayed empty: the page showed a blank ~860x270
    # region on the left and the conversation rendered full-width *below* the trace panel
    # instead of beside it.  Measured on the running app, not guessed.
    with gr.Sidebar(
        label="本次执行追踪",
        position="right",
        open=True,
        width=380,
        elem_id="status-column",
    ):
        route_output = gr.Markdown("**检索路径：** 等待提问")
        source_output = gr.Markdown("**参考来源**\n\n暂无可用来源。")
        error_output = gr.Markdown("**运行状态：** 等待提问")
        metrics_output = gr.JSON(label="本次执行追踪", value={})

    gr.Markdown("# 设计知识库助手")

    gr.ChatInterface(
        fn=chat,
        additional_outputs=[route_output, source_output, error_output, metrics_output],
        examples=[
            "比较两种产品方案在成本、可靠性和可维护性方面的差异，并给出推荐。",
            "一个新产品从概念到量产需要关注哪些设计与工艺问题？",
            "某项设计标准的适用范围和关键要求是什么？",
        ],
        cache_examples=False,
    )

demo.queue(default_concurrency_limit=settings.max_concurrent_requests, max_size=32)


if __name__ == "__main__":
    demo.launch(
        server_name="127.0.0.1",
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7860")),
        share=False,
        css=CSS,
    )
