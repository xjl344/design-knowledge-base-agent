"""Gradio entry point for the design knowledge assistant."""

from __future__ import annotations

import html
import os
import time
import uuid
from typing import Any
from urllib.parse import urlparse

from config import ensure_runtime_dirs, settings
import gradio as gr

from src.checkpoint import CheckpointManager
from src.cost_ledger import (
    QueryCostLedger,
    build_query_cost_record,
    format_cost_markdown,
)
from src.feedback import (
    REASON_CODES,
    FeedbackLedger,
    build_feedback_record,
    compute_snapshot_id,
    short_hash,
)
from src.generator import build_sources
from src.graph_builder import build_graph
from src.resilience import CostLimit, CostTracker, RequestLimiter
from src.ui_theme import (
    APP_CSS,
    CHATBOT_PLACEHOLDER_HTML,
    HEADER_HTML,
    SIDEBAR_HEADER_HTML,
    build_theme,
    render_cost_card,
    render_delivery_card,
    render_sources_card,
    render_trace_card,
)


ensure_runtime_dirs()
crag_app = build_graph()
request_limiter = RequestLimiter(settings.max_concurrent_requests)
cost_tracker = CostTracker(CostLimit(settings.per_query_token_limit, settings.daily_token_limit))
checkpoints = CheckpointManager(settings.checkpoint_dir)
feedback_ledger = FeedbackLedger(settings.feedback_ledger_path)
query_cost_ledger = QueryCostLedger(settings.query_cost_ledger_path)

_REASON_LABEL_TO_CODE: dict[str, str] = {label: code for code, label in REASON_CODES.items()}
_REASON_LABELS: list[str] = list(REASON_CODES.values())

_NODE_STAGE_LABELS: dict[str, str] = {
    "analyze_question": "正在分析问题结构与检索策略…",
    "create_plan": "已拆分检索子任务，准备查询本地知识库…",
    "execute_plan": "已完成子任务本地检索，正在审计证据覆盖…",
    "retrieve": "已完成本地检索，正在评估文档相关性…",
    "grade_documents": "已完成证据相关性评估，准备生成回答…",
    "audit_evidence": "已完成证据覆盖审计，准备整理回答…",
    "search_evidence_gaps": "本地证据存在缺口，已尝试联网补充…",
    "web_search": "已完成联网补充检索，准备生成回答…",
    "generate": "已生成初稿，正在执行引用与交付审计…",
    "aggregate_results": "已综合多路证据生成初稿，正在执行交付审计…",
    "calculation": "已完成确定性工程计算，正在校验交付边界…",
    "validate_delivery": "交付审计完成。",
}


def _chunk_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", "")) if isinstance(block, dict) else str(block)
            for block in content
        )
    return ""


def _preview_sources(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Surface retrieved sources as early as possible before generation finishes."""
    if state.get("sources"):
        return list(state["sources"])
    documents = list(state.get("documents", []) or [])
    if not documents and state.get("task_results"):
        seen: set[str] = set()
        for result in state.get("task_results", []):
            for doc in result.get("documents", []) or []:
                key = str(doc.metadata.get("chunk_id") or id(doc))
                if key not in seen:
                    seen.add(key)
                    documents.append(doc)
    return build_sources(documents) if documents else []


def _stage_progress_text(state: dict[str, Any], node_name: str, elapsed: float) -> str:
    label = _NODE_STAGE_LABELS.get(node_name, f"正在执行 `{node_name}`…")
    sources = _preview_sources(state)
    if sources:
        direct = sum(1 for item in sources if item.get("retrieval_evidence_status") == "direct")
        return (
            f"⏳ {label}\n\n"
            f"> 已耗时 {elapsed:.1f} s · 已找到 **{len(sources)}** 条参考来源"
            f"（其中 `direct` {direct} 条，已在右侧栏先展示）"
        )
    return f"⏳ {label}\n\n> 已耗时 {elapsed:.1f} s（首次冷检索或重排可能需要 20–45 秒，请稍候）"


def _evidence_choices_from_sources(sources: list[dict[str, Any]]) -> list[str]:
    choices: list[str] = []
    for source in sources:
        sid = str(source.get("id", "")).strip()
        if not sid:
            continue
        title = str(source.get("title") or source.get("location") or "未知来源").strip()
        choices.append(f"[{sid}] {title[:42]}")
    return choices


def _extract_evidence_ids(selected_choices: list[str] | None) -> list[str]:
    ids: list[str] = []
    for item in selected_choices or []:
        text = str(item).strip()
        if text.startswith("[") and "]" in text:
            ids.append(text[1 : text.index("]")].strip())
        elif text:
            ids.append(text)
    return ids


def _route_label(state: dict[str, Any]) -> str:
    if state.get("web_search_triggered") and state.get("route") == "planned_web":
        return "Agent 规划 → 本地检索 → 证据缺口联网补充"
    if state.get("web_search_triggered"):
        return "本地知识库 → 网络搜索补充"
    if state.get("route") == "local":
        return "本地知识库"
    if state.get("route") in {"planning", "planned"}:
        return "Agent 规划与并行检索"
    if state.get("route") == "calculation":
        return "确定性工程计算"
    return "未获得资料"


def format_route(state: dict[str, Any]) -> str:
    return f"**检索路径：** {_route_label(state)}"


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


def format_trace_summary(state: dict[str, Any]) -> str:
    """Compact, screenshot-readable header for the execution-trace panel."""
    trace = state.get("execution_trace", []) or []
    sub_tasks = state.get("sub_tasks", []) or []
    tool_calls = state.get("tool_calls", []) or []
    sources = _preview_sources(state)
    direct = sum(1 for item in sources if item.get("retrieval_evidence_status") == "direct")
    indirect = sum(1 for item in sources if item.get("retrieval_evidence_status") == "indirect")
    total = round(sum(item.get("duration_seconds", 0) for item in trace), 1)
    slowest = max(trace, key=lambda item: item.get("duration_seconds", 0)) if trace else None
    slowest_text = (
        f"{slowest.get('node')}（{round(slowest.get('duration_seconds', 0), 1)} s）" if slowest else "—"
    )

    lines = [
        f"**问题类型：** {state.get('question_type', 'simple')}　**路由：** {state.get('route', 'unknown')}",
        f"**子任务：** {len(sub_tasks)}　**工具调用：** {len(tool_calls)}　"
        f"**重写次数：** {state.get('rewrite_attempts', 0)}",
        f"**节点总耗时：** {total} s　**最慢节点：** {slowest_text}",
        f"**证据：** direct {direct} / indirect {indirect}　"
        f"**主张支持率：** {state.get('claim_support_rate', 0.0)}",
    ]
    if sub_tasks:
        lines.extend(["", "**子任务：**"])
        for task in sub_tasks[:6]:
            if isinstance(task, dict):
                text = str(task.get("objective") or task.get("query") or "")[:60]
                lines.append(f"- `{task.get('id', '-')}` {task.get('operation', '')}：{text}")
            else:
                lines.append(f"- {task}")
    if trace:
        lines.extend(["", "**节点耗时：** " + "；".join(
            f"{item.get('node')} {round(item.get('duration_seconds', 0), 1)}s" for item in trace[:8]
        )])
    return "\n".join(lines)


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
        "cost_record": state.get("cost_record", {}),
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


def _feedback_prompt_for_turn(turn: dict[str, Any] | None) -> str:
    if not turn:
        return "完成一次提问后，可在此一键反馈（默认仅记录哈希，不保存问题原文）。"
    if not turn.get("deliverable", False):
        return (
            "💬 本次回答因证据不足已降级——你认为知识库里其实应该有相关资料吗？"
            "点 **👍** 或 **👎** 帮助我们定位缺口。"
        )
    return "💬 这个回答能直接用于你的设计决策吗？点 **👍**（一键完成）或 **👎** 告诉我们卡在哪。"


def submit_positive_feedback(
    turn: dict[str, Any] | None,
    ledger: FeedbackLedger | None = None,
) -> tuple[str, Any]:
    """One-click positive feedback handler (PRD §4 guardrail: <= 1 click)."""
    if not turn:
        return "请先完成一次提问后再提交反馈。", gr.update(visible=False)
    target_ledger = ledger or feedback_ledger
    record = build_feedback_record(
        session_id=str(turn.get("session_id", "")),
        question=str(turn.get("question", "")),
        answer=str(turn.get("answer", "")),
        verdict="up",
        include_question_text=False,
        generator_backend=str(turn.get("generator_backend", settings.generator_backend)),
        latency_seconds=float(turn.get("latency_seconds", 0.0)),
        evidence_count=int(turn.get("evidence_count", 0)),
        snapshot_id=str(turn.get("snapshot_id", "unknown")),
        delivery_status=str(turn.get("delivery_status", "unknown")),
        deliverable=bool(turn.get("deliverable", False)),
    )
    target_ledger.append(record)
    return (
        f"✅ 已记录正面反馈（编号 `{record.feedback_id}`，仅保存哈希）。感谢！",
        gr.update(visible=False),
    )


def open_negative_panel(turn: dict[str, Any] | None) -> tuple[str, Any]:
    if not turn:
        return "请先完成一次提问后再提交反馈。", gr.update(visible=False)
    return "请选择主要问题原因（勾选后点「提交问题反馈」）：", gr.update(visible=True)


def submit_negative_feedback(
    turn: dict[str, Any] | None,
    reason_label: str | None,
    blamed_choices: list[str] | None,
    free_text: str,
    include_question_text: bool,
    ledger: FeedbackLedger | None = None,
) -> tuple[str, Any, Any, Any, Any]:
    """Record negative feedback with reason, blamed citations, and opt-in privacy."""
    if not turn:
        return (
            "请先完成一次提问后再提交反馈。",
            gr.update(visible=False),
            gr.update(),
            gr.update(),
            gr.update(),
        )
    reason_code = _REASON_LABEL_TO_CODE.get(str(reason_label or "").strip())
    if not reason_code:
        return (
            "⚠️ 请先在四项原因中勾选一项，再提交。",
            gr.update(visible=True),
            gr.update(),
            gr.update(),
            gr.update(),
        )
    target_ledger = ledger or feedback_ledger
    record = build_feedback_record(
        session_id=str(turn.get("session_id", "")),
        question=str(turn.get("question", "")),
        answer=str(turn.get("answer", "")),
        verdict="down",
        reason_code=reason_code,
        blamed_evidence_ids=_extract_evidence_ids(blamed_choices),
        free_text=free_text or "",
        include_question_text=bool(include_question_text),
        generator_backend=str(turn.get("generator_backend", settings.generator_backend)),
        latency_seconds=float(turn.get("latency_seconds", 0.0)),
        evidence_count=int(turn.get("evidence_count", 0)),
        snapshot_id=str(turn.get("snapshot_id", "unknown")),
        delivery_status=str(turn.get("delivery_status", "unknown")),
        deliverable=bool(turn.get("deliverable", False)),
    )
    target_ledger.append(record)
    privacy_note = "已按你的勾选附带问题原文" if record.question_text else "仅保存哈希"
    return (
        f"✅ 已记入坏案例台账（编号 `{record.feedback_id}`，原因：`{reason_label}`，{privacy_note}）。",
        gr.update(visible=False),
        gr.update(value=None),
        gr.update(value=[]),
        gr.update(value=""),
    )


def _sync_feedback_bar(turn: dict[str, Any] | None) -> tuple[str, Any, Any]:
    if not turn:
        return (
            "回答生成完成后可在此一键反馈（默认仅保存哈希）。",
            gr.update(visible=False),
            gr.update(choices=[], value=[]),
        )
    sources = list(turn.get("sources", []) or [])
    return (
        _feedback_prompt_for_turn(turn),
        gr.update(visible=False),
        gr.update(choices=_evidence_choices_from_sources(sources), value=[]),
    )


async def chat(message: str, history: list[dict[str, Any]]):
    del history  # The MVP deliberately treats every question as a single turn.
    started = time.perf_counter()
    partial_answer = ""
    session_id = str(uuid.uuid4())
    final_state: dict[str, Any] = {"question": message, "session_id": session_id}

    # Immediate progress acknowledgement so cold retrieval never shows a blank pane.
    yield (
        "⏳ 正在分析问题并检索本地知识库…\n\n> 首次冷检索或重排可能需要 20–45 秒，检索命中后会先在右侧栏展示参考来源。",
        render_delivery_card(final_state, route_label="分析中…", stage_note="正在检索本地知识库…"),
        render_cost_card(pending=True),
        render_sources_card([], loading=True),
        render_trace_card(final_state),
        format_metrics(final_state, session_id),
        None,
    )

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
                            preview_sources = _preview_sources(final_state)
                            yield (
                                partial_answer,
                                render_delivery_card(
                                    final_state,
                                    route_label=_route_label(final_state),
                                    stage_note="正在生成带证据分级的回答…",
                                ),
                                render_cost_card(pending=True),
                                render_sources_card(preview_sources, loading=False),
                                render_trace_card(final_state),
                                format_metrics(final_state, session_id),
                                None,
                            )
                elif mode == "updates":
                    last_node = ""
                    for node_name, update in payload.items():
                        last_node = str(node_name)
                        if isinstance(update, dict):
                            final_state.update(update)
                    elapsed = time.perf_counter() - started
                    preview_sources = _preview_sources(final_state)
                    progress_md = partial_answer or _stage_progress_text(final_state, last_node, elapsed)
                    yield (
                        progress_md,
                        render_delivery_card(
                            final_state,
                            route_label=_route_label(final_state),
                            stage_note=f"已完成 {last_node}（{elapsed:.1f} s）",
                        ),
                        render_cost_card(pending=True),
                        render_sources_card(preview_sources, loading=not bool(preview_sources)),
                        render_trace_card(final_state),
                        format_metrics(final_state, session_id),
                        None,
                    )
    except Exception as exc:
        final_state.setdefault("errors", []).append(f"工作流运行失败：{exc}")
        final_state.setdefault("generation", "系统暂时无法完成回答，请检查配置后重试。")

    wall_seconds = time.perf_counter() - started
    answer = final_state.get("generation") or partial_answer or "未生成有效答案。"
    snapshot_id = compute_snapshot_id(
        settings.chroma_dir, settings.collection_name, settings.chunking_strategy
    )
    cost_record = build_query_cost_record(
        session_id=session_id,
        question_hash=short_hash(message),
        question=message,
        answer=answer,
        state=final_state,
        generator_backend=settings.generator_backend,
        input_price_per_1m_cny=settings.llm_input_price_per_1m_cny,
        output_price_per_1m_cny=settings.llm_output_price_per_1m_cny,
        snapshot_id=snapshot_id,
        wall_seconds=wall_seconds,
    )
    final_state["token_count"] = cost_record.total_tokens_est
    final_state["cost_record"] = cost_record.as_dict()
    try:
        cost_tracker.check_and_record(cost_record.total_tokens_est)
    except RuntimeError as exc:
        final_state.setdefault("errors", []).append(str(exc))
    try:
        query_cost_ledger.append(cost_record)
    except OSError as exc:
        final_state.setdefault("errors", []).append(f"成本账本写入失败：{exc}")
    checkpoints.save(session_id, final_state)

    final_sources = _preview_sources(final_state)
    turn_payload = {
        "session_id": session_id,
        "question": message,
        "answer": answer,
        "sources": final_sources,
        "generator_backend": settings.generator_backend,
        "latency_seconds": cost_record.total_latency_seconds,
        "evidence_count": len(final_sources),
        "snapshot_id": snapshot_id,
        "delivery_status": final_state.get("delivery_status") or final_state.get("status") or "unknown",
        "deliverable": bool(final_state.get("deliverable", False)),
    }
    yield (
        answer,
        render_delivery_card(final_state, route_label=_route_label(final_state)),
        render_cost_card(cost_record.as_dict()),
        render_sources_card(final_sources),
        render_trace_card(final_state),
        format_metrics(final_state, session_id),
        turn_payload,
    )


THEME = build_theme()
CSS = APP_CSS


with gr.Blocks(title="设计知识库助手 · DesignKB Agent", fill_height=True) as demo:
    latest_turn_state = gr.State(value=None)

    gr.HTML(HEADER_HTML)

    with gr.Row(elem_id="dkb-main-row"):
        with gr.Column(scale=7, min_width=520):
            chatbot = gr.Chatbot(
                height=460,
                show_label=False,
                layout="panel",
                buttons=["copy"],
                placeholder=CHATBOT_PLACEHOLDER_HTML,
                render=False,
            )
            textbox = gr.Textbox(
                placeholder="输入材料选型、标准合规、注塑/钣金/CNC 工艺或尺寸推导问题，按 Enter 发送…",
                container=False,
                scale=7,
                render=False,
            )

            # Define right-panel outputs first (unrendered) so ChatInterface can bind them cleanly.
            delivery_card_output = gr.HTML(render_delivery_card(None), render=False)
            cost_card_output = gr.HTML(render_cost_card(None), render=False)
            source_card_output = gr.HTML(render_sources_card([]), render=False)
            trace_card_output = gr.HTML(render_trace_card(None), render=False)
            metrics_output = gr.JSON(label="本次执行追踪（原始）", value={}, render=False)

            gr.ChatInterface(
                fn=chat,
                chatbot=chatbot,
                textbox=textbox,
                additional_outputs=[
                    delivery_card_output,
                    cost_card_output,
                    source_card_output,
                    trace_card_output,
                    metrics_output,
                    latest_turn_state,
                ],
                examples=[
                    "PC注塑件壁厚一般做多少毫米？",
                    "铝件用CNC铣削最大能做多大？",
                    "压铸件为什么不能做尖角？圆角怎么定？",
                    "比较两种产品方案在成本、可靠性和可维护性方面的差异，并给出推荐。",
                ],
                example_labels=[
                    "注塑壁厚 · PC",
                    "CNC 最大尺寸 · 铝件",
                    "压铸圆角与冷隔",
                    "多维度方案对比推荐",
                ],
                show_progress="minimal",
                cache_examples=False,
            )

            with gr.Group(elem_id="feedback-bar"):
                with gr.Row(elem_classes=["dkb-fb-row"]):
                    feedback_status = gr.Markdown(
                        "完成一次提问后，可在此一键反馈（默认仅记录哈希，不保存问题原文）。",
                        scale=6,
                        elem_classes=["dkb-fb-prompt"],
                    )
                    btn_up = gr.Button("👍 有帮助", variant="secondary", size="sm", scale=0, min_width=104)
                    btn_down = gr.Button("👎 有问题", variant="secondary", size="sm", scale=0, min_width=104)
                with gr.Column(visible=False, elem_id="down-panel") as down_panel:
                    reason_radio = gr.Radio(
                        choices=_REASON_LABELS,
                        label="主要问题（四选一）",
                        value=None,
                    )
                    blamed_evidence_input = gr.CheckboxGroup(
                        choices=[],
                        label="可选：勾选你认为有问题的证据编号",
                        value=[],
                    )
                    free_text_input = gr.Textbox(
                        label="可选：一句话补充（你期望看到什么，或哪里不对）",
                        placeholder="例如：我要查的是 1.5mm 不锈钢折弯距孔，结果给的是公差表",
                        lines=2,
                    )
                    include_question_checkbox = gr.Checkbox(
                        value=False,
                        label="附带本次问题原文（默认关闭，仅保存 SHA-256 哈希）",
                    )
                    with gr.Row():
                        btn_submit_down = gr.Button("提交问题反馈", variant="primary", size="sm")
                        btn_cancel_down = gr.Button("收起", variant="secondary", size="sm")

        with gr.Column(scale=4, min_width=360, elem_id="status-column"):
            gr.HTML(SIDEBAR_HEADER_HTML)
            delivery_card_output.render()
            cost_card_output.render()
            source_card_output.render()
            trace_card_output.render()
            with gr.Accordion("原始执行追踪 JSON（调试用）", open=False):
                metrics_output.render()

    latest_turn_state.change(
        fn=_sync_feedback_bar,
        inputs=[latest_turn_state],
        outputs=[feedback_status, down_panel, blamed_evidence_input],
    )

    btn_up.click(
        fn=submit_positive_feedback,
        inputs=[latest_turn_state],
        outputs=[feedback_status, down_panel],
    )
    btn_down.click(
        fn=open_negative_panel,
        inputs=[latest_turn_state],
        outputs=[feedback_status, down_panel],
    )
    btn_cancel_down.click(
        fn=lambda: gr.update(visible=False),
        inputs=[],
        outputs=[down_panel],
    )
    btn_submit_down.click(
        fn=submit_negative_feedback,
        inputs=[
            latest_turn_state,
            reason_radio,
            blamed_evidence_input,
            free_text_input,
            include_question_checkbox,
        ],
        outputs=[
            feedback_status,
            down_panel,
            reason_radio,
            blamed_evidence_input,
            free_text_input,
        ],
    )

demo.queue(default_concurrency_limit=settings.max_concurrent_requests, max_size=32)


if __name__ == "__main__":
    demo.launch(
        server_name="127.0.0.1",
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7860")),
        share=False,
        theme=THEME,
        css=CSS,
        footer_links=[],
    )
