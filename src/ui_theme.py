"""Studio-grade visual theme, CSS, and HTML audit card renderers for app.py."""

from __future__ import annotations

import html
from typing import Any
from urllib.parse import urlparse

import gradio as gr


def build_theme() -> gr.themes.Base:
    """Precision industrial-design studio theme (warm neutral + engineering teal)."""
    return gr.themes.Soft(
        primary_hue=gr.themes.colors.teal,
        secondary_hue=gr.themes.colors.slate,
        neutral_hue=gr.themes.colors.stone,
        radius_size=gr.themes.sizes.radius_md,
        spacing_size=gr.themes.sizes.spacing_md,
        font=(
            gr.themes.Font("PingFang SC"),
            gr.themes.Font("Microsoft YaHei UI"),
            gr.themes.Font("Inter"),
            "ui-sans-serif",
            "system-ui",
            "sans-serif",
        ),
        font_mono=(
            gr.themes.Font("JetBrains Mono"),
            gr.themes.Font("Cascadia Code"),
            "ui-monospace",
            "monospace",
        ),
    ).set(
        body_background_fill="#F6F6F3",
        body_background_fill_dark="#111315",
        block_background_fill="#FFFFFF",
        block_background_fill_dark="#181B1F",
        block_border_width="1px",
        block_border_color="#E5E5E0",
        block_border_color_dark="#2A2F36",
        block_shadow="0 1px 2px rgba(15, 23, 42, 0.04)",
        button_primary_background_fill="#0F766E",
        button_primary_background_fill_hover="#115E59",
        button_primary_text_color="#FFFFFF",
        button_secondary_background_fill="#F3F4F2",
        button_secondary_background_fill_hover="#E7E8E4",
        button_secondary_border_color="#D9DCD6",
        button_secondary_background_fill_dark="#22262C",
        button_secondary_border_color_dark="#343A43",
    )


APP_CSS = """
.gradio-container {
    max-width: 1380px !important;
    margin: 0 auto !important;
    padding: 12px 18px 20px 18px !important;
}

/* Main 2-column workspace */
#dkb-main-row {
    gap: 18px !important;
    align-items: flex-start !important;
}

/* Header */
.dkb-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    flex-wrap: wrap;
    padding: 14px 18px;
    background: linear-gradient(135deg, #FFFFFF 0%, #F2F9F8 100%);
    border: 1px solid #DFECEA;
    border-radius: 12px;
    margin-bottom: 10px;
}
.dark .dkb-header {
    background: linear-gradient(135deg, #181B1F 0%, #142222 100%);
    border-color: #233836;
}
.dkb-brand {
    display: flex;
    align-items: center;
    gap: 12px;
}
.dkb-logo {
    width: 36px;
    height: 36px;
    border-radius: 9px;
    background: #0F766E;
    color: #FFFFFF;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 700;
    font-size: 15px;
    letter-spacing: 0.04em;
    flex-shrink: 0;
    box-shadow: 0 2px 6px rgba(15, 118, 110, 0.25);
}
.dkb-title {
    margin: 0;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: -0.01em;
    color: #111827;
    line-height: 1.25;
}
.dark .dkb-title { color: #F3F4F6; }
.dkb-subtitle {
    margin: 2px 0 0 0;
    font-size: 12.5px;
    color: #57606A;
}
.dark .dkb-subtitle { color: #9CA3AF; }
.dkb-pills {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
}
.dkb-pill {
    font-size: 11.5px;
    font-weight: 500;
    padding: 3px 9px;
    border-radius: 999px;
    background: #FFFFFF;
    border: 1px solid #D5E4E2;
    color: #0F5952;
}
.dark .dkb-pill {
    background: #1C2626;
    border-color: #2B4240;
    color: #5EEAD4;
}

/* Sidebar */
#status-column {
    border: 1px solid #E5E5E0 !important;
    border-radius: 12px !important;
    background: #FAFBF9 !important;
    padding: 12px !important;
}
.dark #status-column {
    border-color: #262B32 !important;
    background: #14171A !important;
}
.dkb-sidebar-title {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 2px 2px 10px 2px;
    margin-bottom: 10px;
    border-bottom: 1px solid #E6E7E2;
    font-size: 13.5px;
    font-weight: 700;
    color: #1F2937;
}
.dark .dkb-sidebar-title {
    border-bottom-color: #2A3038;
    color: #F3F4F6;
}
.dkb-sidebar-sub {
    font-size: 11px;
    font-weight: 500;
    color: #6B7280;
    font-family: "JetBrains Mono", "Cascadia Code", monospace;
}
.dkb-card {
    background: #FFFFFF;
    border: 1px solid #E6E7E2;
    border-radius: 10px;
    padding: 12px 14px;
    margin-bottom: 10px;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
}
.dark .dkb-card {
    background: #1A1E23;
    border-color: #2A3038;
}
.dkb-card-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
}
.dkb-card-label {
    font-size: 11.5px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #6B7280;
}
.dark .dkb-card-label { color: #9CA3AF; }

/* Badges */
.dkb-badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 11.5px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 999px;
    line-height: 1.4;
}
.dkb-badge-ok { background: #ECFDF5; color: #047857; border: 1px solid #A7F3D0; }
.dkb-badge-warn { background: #FFFBEB; color: #B45309; border: 1px solid #FDE68A; }
.dkb-badge-err { background: #FEF2F2; color: #B91C1C; border: 1px solid #FECACA; }
.dkb-badge-info { background: #F0F9FF; color: #0369A1; border: 1px solid #BAE6FD; }
.dkb-badge-neutral { background: #F3F4F6; color: #4B5563; border: 1px solid #E5E7EB; }
.dark .dkb-badge-ok { background: #064E3B; color: #6EE7B7; border-color: #065F46; }
.dark .dkb-badge-warn { background: #451A03; color: #FCD34D; border-color: #78350F; }
.dark .dkb-badge-err { background: #450A0A; color: #FCA5A5; border-color: #7F1D1D; }
.dark .dkb-badge-info { background: #0C4A6E; color: #7DD3FC; border-color: #075985; }
.dark .dkb-badge-neutral { background: #262B32; color: #D1D5DB; border-color: #374151; }

/* KPI grid */
.dkb-kpi-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
    margin-top: 4px;
}
.dkb-kpi {
    background: #F8F9F7;
    border: 1px solid #ECEEE9;
    border-radius: 8px;
    padding: 8px 9px;
}
.dark .dkb-kpi {
    background: #14171B;
    border-color: #262B32;
}
.dkb-kpi-val {
    font-size: 14.5px;
    font-weight: 700;
    color: #111827;
    font-variant-numeric: tabular-nums;
}
.dark .dkb-kpi-val { color: #F3F4F6; }
.dkb-kpi-sub {
    font-size: 11px;
    color: #6B7280;
    margin-top: 2px;
}
.dkb-meta-line {
    font-size: 12px;
    color: #4B5563;
    margin-top: 8px;
    line-height: 1.5;
}
.dark .dkb-meta-line { color: #9CA3AF; }

/* Sources list */
.dkb-source-item {
    padding: 8px 10px;
    border: 1px solid #ECEEE9;
    border-radius: 8px;
    background: #FCFCFB;
    margin-bottom: 6px;
}
.dkb-source-item:last-child { margin-bottom: 0; }
.dark .dkb-source-item {
    background: #14171B;
    border-color: #262B32;
}
.dkb-source-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    margin-bottom: 3px;
}
.dkb-source-id {
    font-family: "JetBrains Mono", "Cascadia Code", monospace;
    font-size: 11.5px;
    font-weight: 700;
    color: #0F766E;
}
.dark .dkb-source-id { color: #5EEAD4; }
.dkb-source-title {
    font-size: 12.5px;
    font-weight: 600;
    color: #1F2937;
    word-break: break-word;
}
.dark .dkb-source-title { color: #E5E7EB; }
.dkb-source-path {
    font-size: 11px;
    color: #6B7280;
    font-family: "JetBrains Mono", "Cascadia Code", monospace;
    word-break: break-all;
    margin-top: 2px;
}

/* Trace timeline pills */
.dkb-timeline {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
    margin-top: 6px;
}
.dkb-step {
    font-size: 11px;
    padding: 2px 7px;
    border-radius: 6px;
    background: #F3F4F2;
    border: 1px solid #E2E5DF;
    color: #374151;
    font-family: "JetBrains Mono", "Cascadia Code", monospace;
}
.dark .dkb-step {
    background: #20252B;
    border-color: #303740;
    color: #D1D5DB;
}

/* Issue list */
.dkb-issue-list {
    margin: 6px 0 0 0;
    padding-left: 16px;
    font-size: 12px;
    color: #92400E;
    line-height: 1.45;
}
.dark .dkb-issue-list { color: #FCD34D; }

/* Feedback bar */
#feedback-bar {
    margin-top: 10px !important;
    padding: 10px 14px !important;
    border: 1px solid #E2E6E2 !important;
    border-radius: 12px !important;
    background: #FFFFFF !important;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03) !important;
}
.dark #feedback-bar {
    background: #181B1F !important;
    border-color: #2A3038 !important;
}
#feedback-bar .dkb-fb-row,
#feedback-bar .styler,
#feedback-bar .form {
    align-items: center !important;
    gap: 8px !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
}
#feedback-bar .dkb-fb-prompt {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    padding: 2px 0 !important;
}
#feedback-bar .prose p {
    margin: 0 !important;
    font-size: 13px !important;
    color: #374151 !important;
}
.dark #feedback-bar .prose p { color: #D1D5DB !important; }
#down-panel {
    margin-top: 10px !important;
    padding: 12px !important;
    border-radius: 10px !important;
    background: #F8FAF9 !important;
    border: 1px solid #DCE7E4 !important;
}
.dark #down-panel {
    background: #14191B !important;
    border-color: #243432 !important;
}

/* Empty state inside chatbot */
.dkb-empty {
    max-width: 560px;
    margin: 24px auto;
    text-align: left;
    padding: 18px 20px;
    border-radius: 12px;
    border: 1px dashed #D3DCDA;
    background: #FAFDFC;
}
.dark .dkb-empty {
    background: #151C1D;
    border-color: #2A3B39;
}
.dkb-empty h3 {
    margin: 0 0 8px 0;
    font-size: 15px;
    font-weight: 700;
    color: #0F5952;
}
.dark .dkb-empty h3 { color: #5EEAD4; }
.dkb-empty ul {
    margin: 0;
    padding-left: 18px;
    font-size: 13px;
    color: #4B5563;
    line-height: 1.7;
}
.dark .dkb-empty ul { color: #9CA3AF; }
"""


SIDEBAR_HEADER_HTML = """
<div class="dkb-sidebar-title">
  <span>证据与执行审计</span>
  <span class="dkb-sidebar-sub">AUDIT PANEL</span>
</div>
"""


HEADER_HTML = """
<div class="dkb-header">
  <div class="dkb-brand">
    <div class="dkb-logo">DK</div>
    <div>
      <div class="dkb-title">设计知识库助手 · DesignKB Agent</div>
      <div class="dkb-subtitle">面向材料选型、标准合规、制造工艺与尺寸推导的可审计问答系统</div>
    </div>
  </div>
  <div class="dkb-pills">
    <span class="dkb-pill">证据分级 direct / indirect</span>
    <span class="dkb-pill">证据不足即拒答</span>
    <span class="dkb-pill">隐私哈希反馈</span>
  </div>
</div>
"""


CHATBOT_PLACEHOLDER_HTML = """
<div class="dkb-empty">
  <h3>欢迎使用设计知识库助手</h3>
  <ul>
    <li><strong>结论可追溯：</strong>每条实质参数与规则都绑定本地来源编号 <code>[L1]</code>，并在右侧实时区分 <code>direct</code> / <code>indirect</code> 证据等级。</li>
    <li><strong>支持跨语种工艺查证：</strong>直接用中文提问注塑、钣金、CNC、3D 打印、压铸与阳极氧化等厂商 DFM 准则。</li>
    <li><strong>不硬编数字：</strong>资料缺口或超范围时会明确降级提示，并支持底部一键反馈沉淀坏案例。</li>
  </ul>
</div>
"""


def _evidence_badge(status: str, source_type: str) -> str:
    norm = str(status or "unknown").lower()
    if source_type == "web":
        return '<span class="dkb-badge dkb-badge-info">web · 需核验</span>'
    if norm == "direct":
        return '<span class="dkb-badge dkb-badge-ok">direct</span>'
    if norm == "indirect":
        return '<span class="dkb-badge dkb-badge-warn">indirect</span>'
    if norm == "scope_mismatch":
        return '<span class="dkb-badge dkb-badge-err">scope_mismatch</span>'
    return f'<span class="dkb-badge dkb-badge-neutral">{html.escape(norm)}</span>'


def render_delivery_card(
    state: dict[str, Any] | None,
    *,
    route_label: str = "等待提问",
    stage_note: str | None = None,
) -> str:
    """Render top sidebar card showing delivery badge, route, and blocking issues."""
    if not state:
        return (
            '<div class="dkb-card">'
            '<div class="dkb-card-head">'
            '<span class="dkb-card-label">交付与运行状态</span>'
            '<span class="dkb-badge dkb-badge-neutral">待机</span>'
            "</div>"
            f'<div class="dkb-meta-line">检索路径：{html.escape(route_label)}</div>'
            "</div>"
        )

    if stage_note:
        return (
            '<div class="dkb-card">'
            '<div class="dkb-card-head">'
            '<span class="dkb-card-label">交付与运行状态</span>'
            '<span class="dkb-badge dkb-badge-info">执行中</span>'
            "</div>"
            f'<div class="dkb-meta-line"><strong>{html.escape(stage_note)}</strong><br/>'
            f"检索路径：{html.escape(route_label)}</div>"
            "</div>"
        )

    deliverable = state.get("deliverable") is True
    status = str(state.get("delivery_status") or state.get("status") or "unknown")
    if deliverable and status == "completed":
        badge = '<span class="dkb-badge dkb-badge-ok">✓ 可交付</span>'
    else:
        badge = '<span class="dkb-badge dkb-badge-warn">! 证据不足 · 已降级</span>'

    issues_html = ""
    issues = state.get("blocking_issues", []) or []
    errors = state.get("errors", []) or []
    items: list[str] = []
    seen: set[str] = set()
    for issue in issues[:5]:
        text = f"{issue.get('status', 'unknown')}：{issue.get('reason', '需要补充证据')}"
        if text not in seen:
            seen.add(text)
            items.append(f"<li>{html.escape(text)}</li>")
    for err in errors[:3]:
        text = str(err)
        if text not in seen:
            seen.add(text)
            items.append(f"<li>{html.escape(text)}</li>")
    if items:
        issues_html = f'<ul class="dkb-issue-list">{"".join(items)}</ul>'

    return (
        '<div class="dkb-card">'
        '<div class="dkb-card-head">'
        '<span class="dkb-card-label">交付与运行状态</span>'
        f"{badge}"
        "</div>"
        f'<div class="dkb-meta-line">检索路径：{html.escape(route_label)}</div>'
        f"{issues_html}"
        "</div>"
    )


def render_cost_card(cost_record: dict[str, Any] | None = None, *, pending: bool = False) -> str:
    """Render compact 3-tile KPI card for latency, tokens, and estimated cost."""
    if pending:
        return (
            '<div class="dkb-card">'
            '<div class="dkb-card-head">'
            '<span class="dkb-card-label">耗时与成本账单</span>'
            '<span class="dkb-badge dkb-badge-info">统计中</span>'
            "</div>"
            '<div class="dkb-meta-line">本轮完成后自动记录至 <code>logs/query_cost_ledger.jsonl</code></div>'
            "</div>"
        )
    if not cost_record:
        return (
            '<div class="dkb-card">'
            '<div class="dkb-card-head">'
            '<span class="dkb-card-label">耗时与成本账单</span>'
            '<span class="dkb-badge dkb-badge-neutral">等待提问</span>'
            "</div>"
            '<div class="dkb-kpi-grid">'
            '<div class="dkb-kpi"><div class="dkb-kpi-val">—</div><div class="dkb-kpi-sub">总耗时</div></div>'
            '<div class="dkb-kpi"><div class="dkb-kpi-val">—</div><div class="dkb-kpi-sub">估算 Token</div></div>'
            '<div class="dkb-kpi"><div class="dkb-kpi-val">—</div><div class="dkb-kpi-sub">估算费用</div></div>'
            "</div>"
            "</div>"
        )

    cache_hit = bool(cost_record.get("cache_hit", False))
    cache_badge = (
        '<span class="dkb-badge dkb-badge-ok">缓存命中</span>'
        if cache_hit
        else '<span class="dkb-badge dkb-badge-neutral">冷检索</span>'
    )
    total_s = float(cost_record.get("total_latency_seconds", 0.0) or 0.0)
    ret_s = float(cost_record.get("retrieval_seconds", 0.0) or 0.0)
    rerank_s = float(cost_record.get("reranker_seconds", 0.0) or 0.0)
    gen_s = float(cost_record.get("generation_seconds", 0.0) or 0.0)
    tok = int(cost_record.get("total_tokens_est", 0) or 0)
    p_tok = int(cost_record.get("prompt_tokens_est", 0) or 0)
    c_tok = int(cost_record.get("completion_tokens_est", 0) or 0)
    cost_cny = float(cost_record.get("estimated_cost_cny", 0.0) or 0.0)

    return (
        '<div class="dkb-card">'
        '<div class="dkb-card-head">'
        '<span class="dkb-card-label">耗时与成本账单（估算）</span>'
        f"{cache_badge}"
        "</div>"
        '<div class="dkb-kpi-grid">'
        f'<div class="dkb-kpi"><div class="dkb-kpi-val">{total_s:.1f} s</div><div class="dkb-kpi-sub">总耗时</div></div>'
        f'<div class="dkb-kpi"><div class="dkb-kpi-val">{tok:,}</div><div class="dkb-kpi-sub">Token</div></div>'
        f'<div class="dkb-kpi"><div class="dkb-kpi-val">¥{cost_cny:.4f}</div><div class="dkb-kpi-sub">估算费用</div></div>'
        "</div>"
        f'<div class="dkb-meta-line">检索 {ret_s:.1f}s · 重排 {rerank_s:.1f}s · 生成 {gen_s:.1f}s ｜ 输入 ~{p_tok:,} / 输出 ~{c_tok:,}</div>'
        "</div>"
    )


def render_sources_card(sources: list[dict[str, Any]], *, loading: bool = False) -> str:
    """Render structured evidence cards with direct/indirect/web pills."""
    count_badge = (
        '<span class="dkb-badge dkb-badge-info">检索中…</span>'
        if loading and not sources
        else f'<span class="dkb-badge dkb-badge-neutral">{len(sources)} 条</span>'
    )
    if not sources:
        empty_msg = "正在检索本地知识库，命中后会先在此处展示…" if loading else "暂无可用参考来源。"
        return (
            '<div class="dkb-card">'
            '<div class="dkb-card-head">'
            '<span class="dkb-card-label">参考来源与证据分级</span>'
            f"{count_badge}"
            "</div>"
            f'<div class="dkb-meta-line">{empty_msg}</div>'
            "</div>"
        )

    rows: list[str] = []
    for source in sources[:10]:
        sid = html.escape(str(source.get("id", "?")))
        title = html.escape(str(source.get("title") or "未知来源"))
        location = str(source.get("location") or "未知来源")
        page = source.get("page")
        page_text = f" · 第 {html.escape(str(page))} 页" if page else ""
        badge = _evidence_badge(
            str(source.get("retrieval_evidence_status", "unknown")),
            str(source.get("type", "local")),
        )
        if source.get("type") == "web" and urlparse(location).scheme in {"http", "https"}:
            safe_url = html.escape(location, quote=True)
            title_html = f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">{title}</a>'
            path_html = html.escape(location[:80])
        else:
            title_html = f"{title}{page_text}"
            path_html = html.escape(location)
        rows.append(
            '<div class="dkb-source-item">'
            f'<div class="dkb-source-top"><span class="dkb-source-id">[{sid}]</span>{badge}</div>'
            f'<div class="dkb-source-title">{title_html}</div>'
            f'<div class="dkb-source-path">{path_html}</div>'
            "</div>"
        )

    return (
        '<div class="dkb-card">'
        '<div class="dkb-card-head">'
        '<span class="dkb-card-label">参考来源与证据分级</span>'
        f"{count_badge}"
        "</div>"
        f'{"".join(rows)}'
        "</div>"
    )


def render_trace_card(state: dict[str, Any] | None) -> str:
    """Render compact execution-trace summary with node timeline pills."""
    if not state or not state.get("execution_trace"):
        return (
            '<div class="dkb-card">'
            '<div class="dkb-card-head">'
            '<span class="dkb-card-label">执行轨迹</span>'
            '<span class="dkb-badge dkb-badge-neutral">尚未运行</span>'
            "</div>"
            '<div class="dkb-meta-line">提问后展示问题类型、节点耗时与主张支持率。</div>'
            "</div>"
        )

    trace = state.get("execution_trace", []) or []
    qtype = html.escape(str(state.get("question_type", "simple")))
    support = state.get("claim_support_rate", 0.0)
    rewrites = int(state.get("rewrite_attempts", 0) or 0)
    steps = [
        f'<span class="dkb-step">{html.escape(str(item.get("node", "")))} {round(float(item.get("duration_seconds", 0) or 0), 1)}s</span>'
        for item in trace[:10]
    ]
    return (
        '<div class="dkb-card">'
        '<div class="dkb-card-head">'
        '<span class="dkb-card-label">执行轨迹</span>'
        f'<span class="dkb-badge dkb-badge-neutral">{qtype}</span>'
        "</div>"
        f'<div class="dkb-meta-line">主张支持率 <strong>{support}</strong> · 重写次数 <strong>{rewrites}</strong></div>'
        f'<div class="dkb-timeline">{"".join(steps)}</div>'
        "</div>"
    )
