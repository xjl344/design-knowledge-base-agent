"""Render an aggregation payload as a human-readable evaluation report.

Why a five-section report rather than one table
-----------------------------------------------
Metrics in this project are not interchangeable, and a flat table invites the
reader to average across them.  They differ in two ways that decide what may be
concluded:

* **What they are about.**  A timeout rate is a statement about the service; a
  span recall is a statement about answer text.  Reading them side by side
  hides the fact that a 60% success rate turns every quality number into a
  claim about the surviving 60%.

* **What may be done with them.**  Some are frozen (retrieval must not move at
  all), some are diagnostic (latency headroom, retry dependence), some are
  gated.  A report that does not say which is which gets read as if all rows
  carried equal weight.

So the sections are ordered by dependency: credibility first, then whether the
system ran, then what it produced.  A quality number is only meaningful after
the first two sections pass, and the report says so in prose rather than
leaving it to the reader.

Every metric carries its ``sample_size``.  At 12 questions a single flipped
verdict moves a percentage by 8 points, so a figure without its denominator is
not interpretable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Behaviour metric -> the summary key holding the number of scored
# observations.  Mapped explicitly rather than derived by string surgery on the
# metric name: the two names differ (``refusal_correctness_rate`` vs
# ``refusal_metric_sample_size``), and a derivation that silently returns None
# would drop the denominator without any error.
BEHAVIOUR_SAMPLE_SIZE_KEYS = {
    "refusal_correctness_rate": "refusal_metric_sample_size",
    "ambiguity_safety_rate": "ambiguity_metric_sample_size",
}

# Metrics that are means over a subset; a reader needs to know the subset.
# Maps summary key -> the sample-size key recorded next to it, if any.
SUMMARY_SAMPLE_SIZE_KEYS = {
    "answer_span_recall_mean": "span_metric_sample_size",
    "required_term_recall_mean": "required_term_metric_sample_size",
    "citation_validity_rate": "citation_metric_sample_size",
    "citation_id_usage_ratio_mean": "citation_metric_sample_size",
    "source_coverage_mean": "citation_metric_sample_size",
    "unsupported_number_count_mean": "unsupported_number_metric_sample_size",
    "answer_length_mean": "answer_length_metric_sample_size",
}

# Groups of the contract, in the order they are reported, with the human
# heading and the reason the section exists.
SECTIONS = (
    (
        "credibility",
        "一、实验可信度",
        "在解释任何指标之前，先确认这次运行本身是可以被解释的。",
    ),
    (
        "usability",
        "二、系统可用性",
        "系统是否真的跑起来了。这一节不达标时，后面的质量数字只是对幸存样本的描述。",
    ),
    (
        "quality",
        "三、回答质量",
        "答案是否覆盖了应当覆盖的事实，且没有编造区间外的数字。",
    ),
    (
        "safety",
        "四、安全行为",
        "高风险行为：该拒答时是否拒答，该限定范围时是否限定。",
    ),
    (
        "citation",
        "五、引用质量",
        "引用的编号是否合法、是否用到了它应引用的证据。",
    ),
)


def _fmt(value: Any, digits: int = 3) -> str:
    """Format a metric for a table cell, keeping None distinguishable from 0."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _pct(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.1f}%"


def _metric_row(
    label: str, value: Any, *, sample_size: Any = None, note: str = ""
) -> str:
    """One table row. ``sample_size`` is rendered as ``n=`` or an em dash."""
    n = f"n={sample_size}" if sample_size is not None else "—"
    return f"| {label} | {_fmt(value)} | {n} | {note} |"


def _sample_size_of(payload: dict[str, Any], metric: str) -> Any:
    """Find the denominator recorded for a mean-style metric.

    Two producers exist: single-run summaries carry a per-metric key, while the
    aggregator over several runs does not, in which case the number of scored
    observations is the denominator.
    """
    key = SUMMARY_SAMPLE_SIZE_KEYS.get(metric)
    for source in (payload.get("summary") or {}, payload):
        if key and source.get(key) is not None:
            return source[key]
    return None


# ---------------------------------------------------------------------------
# Section 1 -- credibility
# ---------------------------------------------------------------------------
def render_credibility(payload: dict[str, Any]) -> list[str]:
    """State what must hold before the rest of the report may be read.

    This section is first because every later number is conditional on it.  It
    reports the experiment identity (so two reports can be told apart), whether
    the repeated runs agree, and whether flaky questions were found.
    """
    lines = [
        "| 判据 | 结果 | 说明 |",
        "| --- | --- | --- |",
    ]

    comparability = payload.get("run_comparability")
    if comparability:
        ok = bool(comparability.get("comparable"))
        lines.append(
            f"| 多次运行可比 | {_fmt(ok)} | 容差 "
            f"{_pct(comparability.get('tolerance'))}；"
            f"{comparability.get('verdict', '')} |"
        )
        for item in comparability.get("unstable_metrics") or []:
            lines.append(
                f"| ↳ 不稳定：{item['metric']} | 跨度 {_fmt(item['spread'])} | "
                f"取值 {item['values']} |"
            )
    else:
        lines.append("| 多次运行可比 | — | 本次聚合只有一个运行，无从判断运行间稳定性 |")

    runs = payload.get("run_count")
    lines.append(f"| 运行次数 | {_fmt(runs)} | 重复序号 {payload.get('repetition_indices')} |")

    slice_report = payload.get("slice_report") or {}
    excluded = slice_report.get("excluded_question_ids")
    if slice_report:
        lines.append(
            f"| flaky 题（移出回归分母） | {_fmt(len(excluded or []))} | "
            f"{excluded if excluded else '无'} |"
        )
    else:
        lines.append("| flaky 题（移出回归分母） | — | 未提供切片文件，未做 flaky 检测 |")

    lines += [
        "",
        "实验身份（两次运行只有这些字段相同才可比较）：",
        "",
        "| 字段 | 值 |",
        "| --- | --- |",
        f"| 模型 | `{payload.get('model')}` |",
        f"| 提示词版本 | `{payload.get('prompt_version')}` |",
        f"| 判定口径版本 | `{payload.get('audit_version')}` |",
        f"| 快照 sha256 | `{str(payload.get('snapshot_sha256'))[:16]}…` |",
        f"| 契约 sha256 | `{str(payload.get('evaluation_sha256'))[:16]}…` |",
        f"| 重试上限 | {_fmt(payload.get('generation_max_retries'))} |",
        f"| 检索调用次数 | {_fmt(payload.get('retrieval_calls'))}（冻结组，必须为 0） |",
    ]

    if not comparability:
        lines += [
            "",
            "> 只有一个运行时，本节无法给出运行间稳定性结论。",
            "> 单次运行的指标差异无法与噪声区分，不足以支撑趋势判断。",
        ]
    elif not comparability.get("comparable"):
        lines += [
            "",
            "> **本次运行的重复之间已超出容差，后文任何「变化」都不应被读作趋势。**",
            "> 这些差异来自同配置重跑，属测量噪声；先扩大重复次数或修稳定服务，",
            "> 再谈提示词或链路改动的效果。",
        ]

    return lines


# ---------------------------------------------------------------------------
# Section 2 -- usability
# ---------------------------------------------------------------------------
def render_usability(payload: dict[str, Any]) -> list[str]:
    """Whether the system ran: failure rates split by remedy, then latency."""
    lines = [
        "| 指标 | 值 | n | 说明 |",
        "| --- | --- | --- | --- |",
    ]
    lines.append(_metric_row(
        "生成成功率", payload.get("generation_success_rate"),
        sample_size=payload.get("total_question_runs"),
        note="完成的题次 / 全部题次",
    ))
    lines.append(_metric_row(
        "provider 超时率", payload.get("provider_timeout_rate"),
        sample_size=payload.get("total_question_runs"),
        note="补救方式：调高单次调用上限",
    ))
    lines.append(_metric_row(
        "provider 硬错误率", payload.get("provider_hard_error_rate"),
        sample_size=payload.get("total_question_runs"),
        note="补救方式：重试或换 provider",
    ))
    lines.append(_metric_row(
        "限流率", payload.get("provider_rate_limit_rate"),
        sample_size=payload.get("total_question_runs"),
        note="计入硬错误率",
    ))
    lines.append(_metric_row(
        "首答成功题次", payload.get("first_try_success_count"),
        note="未依赖重试",
    ))
    lines.append(_metric_row(
        "依赖重试才成功", payload.get("retried_success_count"),
        note=f"{payload.get('retried_success_question_ids') or '无'}",
    ))

    gate = bool(payload.get("provider_stability_gate_passed"))
    lines += [
        "",
        f"**可用性闸门（阈值 {_pct(payload.get('provider_stability_gate_threshold'))}）："
        f"{'通过' if gate else '未通过'}**",
    ]

    latency = payload.get("generation_latency_seconds") or {}
    attempt = payload.get("per_attempt_latency_seconds") or {}
    lines += [
        "",
        "延迟（区分「单次调用」与「整行含重试」两类，上限作用在单次调用上）：",
        "",
        "| 口径 | p50 | p95 | max | n |",
        "| --- | --- | --- | --- | --- |",
        f"| 单次调用 | {_fmt(attempt.get('p50'))} | {_fmt(attempt.get('p95'))} | "
        f"{_fmt(attempt.get('max'))} | {_fmt(attempt.get('sample_size'))} |",
        f"| 整行（含重试） | {_fmt(latency.get('p50'))} | {_fmt(latency.get('p95'))} | "
        f"{_fmt(latency.get('max'))} | {_fmt(payload.get('completed_question_runs'))} |",
        "",
        f"单次调用上限 {_fmt(payload.get('latency_ceiling_seconds'))} s，"
        f"距最慢成功调用余量 {_fmt(payload.get('latency_headroom_seconds'))} s。",
    ]
    headroom = payload.get("latency_headroom_seconds")
    if headroom is not None and float(headroom) < 0:
        lines.append(
            "> 余量为负：有调用触及上限。这是**卡死**的特征，不是「慢但会完成」。"
        )
    elif headroom is not None and float(headroom) < 5:
        lines.append(
            "> 余量不足 5 s：最慢的成功调用已接近上限，超时率对服务抖动会很敏感。"
        )

    if not gate:
        lines += [
            "",
            "> **可用性闸门未通过，后两节的质量与引用数字只描述幸存样本，"
            "不能读作系统水平。**",
        ]
    return lines


# ---------------------------------------------------------------------------
# Section 3 -- quality
# ---------------------------------------------------------------------------
def render_quality(payload: dict[str, Any]) -> list[str]:
    """Answer coverage: did the answer contain what the contract required."""
    lines = [
        "| 指标 | 值 | n | 说明 |",
        "| --- | --- | --- | --- |",
    ]
    for key, label, note in (
        ("answer_span_recall_mean", "答案 span 召回",
         "契约指定的答案片段被覆盖的比例；最贴近「答对了没有」"),
        ("required_term_recall_mean", "必需词召回",
         "契约指定的关键词组被覆盖的比例"),
        ("unsupported_number_count_mean", "无出处数字数",
         "答案中出现但证据包不支持的数值个数，越低越好"),
        ("answer_length_mean", "答案长度",
         "字符数，仅作诊断；过长会触发告警"),
    ):
        lines.append(_metric_row(
            label, payload.get(key),
            sample_size=_sample_size_of(payload, key),
            note=note,
        ))

    span_excluded = (payload.get("total_question_runs") or 0) - (
        payload.get("completed_question_runs") or 0
    )
    lines += [
        "",
        f"未产生答案的题次 {span_excluded} 个，不进入本节任何分母——"
        "把失败题按 0 分计入会同时低估质量和掩盖失败原因。",
    ]
    lines.append(
        "注意：span 与词召回只检查「该说的说了没有」，不检查「多说了没有」；"
        "后者由无出处数字数与引用质量承担。"
    )
    return lines


# ---------------------------------------------------------------------------
# Section 4 -- safety
# ---------------------------------------------------------------------------
def render_safety(payload: dict[str, Any], slice_report: dict[str, Any]) -> list[str]:
    """Behaviour metrics, scored only on the questions they apply to.

    Both metrics are ``None`` on almost every question by construction, so the
    denominator here is not the run size.  A refusal question contributes to the
    refusal mean and nothing else; treating its ``None`` ambiguity as a failure
    would report a fabricated regression.

    Two denominators are shown, because at this size they differ and confusing
    them changes the reading: ``题数`` is how many questions the metric applies
    to, ``观测`` is how many scored answers went into the mean (题数 × 成功的运行数).
    """
    lines = [
        "| 指标 | 值 | 题数 | 观测 | 适用题目 | 说明 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    applicable = _behaviour_question_ids(slice_report)
    for key, label, note in (
        ("refusal_correctness_rate", "拒答正确率",
         "证据不足时明确拒答；错答即为幻觉"),
        ("ambiguity_safety_rate", "歧义处理正确率",
         "证据部分支持时拒绝直接套用，并给出限定或推算路径"),
    ):
        ids = applicable.get(key) or []
        lines.append(
            f"| {label} | {_fmt(payload.get(key))} | {len(ids)} | "
            f"{_fmt(_behaviour_observation_count(payload, key))} | "
            f"{', '.join(ids) if ids else '—'} | {note} |"
        )

    lines += [
        "",
        "两项的分母都不是全部题次。非该类别的题在此指标上记为**不适用**而非失败，"
        "否则分母被稀释，一个真实缺陷会看起来像轻微波动。",
        "题数与观测数不同：一题在多轮运行中各判一次，所以观测数通常大于题数。",
    ]

    flaky = _flaky_ids(slice_report)
    if flaky:
        lines += [
            "",
            f"> 已排除 flaky 题 {flaky}：同一系统多次运行中判定翻转，"
            "进入分母会把不稳定读成改进或退步。",
        ]

    if payload.get("refusal_correctness_rate") is None:
        lines.append("\n> 本次运行没有可判定的拒答题次，该指标不可用。")
    if payload.get("ambiguity_safety_rate") is None:
        lines.append("\n> 本次运行没有可判定的歧义题次，该指标不可用。")
    return lines


def _behaviour_observation_count(payload: dict[str, Any], metric: str) -> Any:
    """Scored observations behind a behaviour mean, i.e. 题数 × 成功运行数."""
    key = BEHAVIOUR_SAMPLE_SIZE_KEYS.get(metric)
    for source in (payload.get("summary") or {}, payload):
        if key and source.get(key) is not None:
            return source[key]
    return None


def _behaviour_question_ids(slice_report: dict[str, Any]) -> dict[str, list[str]]:
    """Recover which questions each behaviour metric applied to.

    Read back from the slice breakdown rather than recomputed, so the report
    shows the same denominator the gate used.
    """
    out: dict[str, list[str]] = {}
    breakdown = slice_report.get("slice_breakdown") or {}
    for dimension in ("case_type", "risk_level"):
        for metric in ("refusal_correctness", "ambiguity_safety"):
            grouped = breakdown.get(f"{dimension}:{metric}") or {}
            for entry in grouped.values():
                ids = entry.get("question_ids") or []
                if not ids:
                    continue
                bucket = out.setdefault(f"{metric}_rate", [])
                for qid in ids:
                    if qid not in bucket:
                        bucket.append(qid)
    return {key: sorted(value) for key, value in out.items()}


def _flaky_ids(slice_report: dict[str, Any]) -> list[str]:
    return list(slice_report.get("excluded_question_ids") or [])


# ---------------------------------------------------------------------------
# Section 5 -- citation
# ---------------------------------------------------------------------------
def render_citation(payload: dict[str, Any], slice_report: dict[str, Any]) -> list[str]:
    """Citation legality and how much of the allowed evidence was used."""
    lines = [
        "| 指标 | 值 | n | 说明 |",
        "| --- | --- | --- | --- |",
    ]
    for key, label, note in (
        ("citation_validity_rate", "引用合法率",
         "答案里出现的引用编号都在允许集合内"),
        ("citation_id_usage_ratio_mean", "引用编号使用率",
         "用到的编号 / 允许的编号；过低说明证据未被充分利用"),
        ("source_coverage_mean", "来源覆盖率",
         "契约指定的来源被引用到的比例"),
    ):
        n = _sample_size_of(payload, key)
        if n is None:
            n = _citation_sample_size(slice_report)
        lines.append(_metric_row(label, payload.get(key), sample_size=n, note=note))

    lines += [
        "",
        "引用类指标只对事实题统计。拒答题与歧义题的正确答案可能不引用任何来源，"
        "把它们计入分母会把「正确不引用」算成「引用失败」。",
    ]
    return lines


def _citation_sample_size(slice_report: dict[str, Any]) -> Any:
    breakdown = slice_report.get("slice_breakdown") or {}
    grouped = breakdown.get("case_type:citation_validity") or {}
    total = sum(entry.get("sample_size") or 0 for entry in grouped.values())
    return total or None


# ---------------------------------------------------------------------------
# Gate verdict
# ---------------------------------------------------------------------------
def render_gate(payload: dict[str, Any]) -> list[str]:
    """The group-gate verdict, or an explicit statement that there is none."""
    gate = (payload.get("slice_report") or {}).get("group_gate")
    if not gate:
        return [
            "本次未指定基线，未做分组闸门比较。",
            "",
            "分组闸门的用途：把「这次改动会动哪些指标」写成声明，"
            "未声明的指标组必须完全不动。这样一次提示词改动若顺带动了"
            "检索或可用性指标，会直接失败，而不是被平均进一个总分会。",
        ]

    declared = gate.get("declared_changes") or []
    verdict = gate.get("all_groups_passed")
    lines = [
        f"**分组闸门：{'全部通过' if verdict else '存在违规'}**"
        f"（声明的改动范围：{', '.join(declared) if declared else '无，即要求所有组不动'}）",
        "",
        "| 指标组 | 是否声明 | 结论 | 违规指标 |",
        "| --- | --- | --- | --- |",
    ]
    for group_name, result in (gate.get("groups") or {}).items():
        breaches = [
            f"{item['metric']}（{item.get('breach_reason')}，"
            f"delta={_fmt(item.get('delta'))}）"
            for item in (result.get("metrics") or [])
            if item.get("verdict") == "breach"
        ]
        lines.append(
            f"| {group_name} | {'是' if result.get('declared') else '否'} | "
            f"{'通过' if result.get('passed') else '**违规**'} | "
            f"{'；'.join(breaches) if breaches else '—'} |"
        )

    lines += [
        "",
        "两个独立判据，必须同时成立：**是否允许动**（未声明的组一步都不许动，"
        "改进也不行——那说明改动没被限定在声明的范围内）和**动了多少**"
        "（已声明的组须落在容差内）。",
        "",
        "> 基线必须覆盖同一批运行。若基线的运行条数与本次不同，"
        "任何依赖数据量的指标（如引用编号使用率）都会因分母变化而移动，"
        "报出的违规可能是子集效应而非系统退化。",
    ]
    return lines


# ---------------------------------------------------------------------------
# Appendices
# ---------------------------------------------------------------------------
def render_slice_appendix(slice_report: dict[str, Any]) -> list[str]:
    """Per-slice breakdown, so a regression can be named rather than averaged.

    「质量下降了」不可执行；「只有歧义切片下降了」才可以定位。
    """
    breakdown = slice_report.get("slice_breakdown") or {}
    if not breakdown:
        return ["未提供切片报告。", ""]

    lines: list[str] = []
    metric_labels = {
        "answer_span_recall": "答案 span 召回",
        "required_term_recall": "必需词召回",
        "hop_recall": "多跳覆盖率",
        "citation_validity": "引用合法率",
        "refusal_correctness": "拒答正确率",
        "ambiguity_safety": "歧义处理正确率",
    }
    dimension_labels = {"case_type": "按题型", "risk_level": "按风险等级"}

    for dimension, heading in dimension_labels.items():
        lines += [f"### {heading}", ""]
        for metric, label in metric_labels.items():
            grouped = breakdown.get(f"{dimension}:{metric}") or {}
            if not grouped:
                continue
            lines += [
                f"**{label}**",
                "",
                "| 切片 | 均值 | 样本数 | 题数 | 题目 |",
                "| --- | --- | --- | --- | --- |",
            ]
            for key, entry in grouped.items():
                lines.append(
                    f"| {key} | {_fmt(entry.get('mean'))} | "
                    f"{_fmt(entry.get('sample_size'))} | "
                    f"{_fmt(entry.get('question_count'))} | "
                    f"{', '.join(entry.get('question_ids') or []) or '—'} |"
                )
            lines.append("")

    # The "one question moves it by N points" caution depends on the sample, so
    # it is computed rather than written down: the sentence used to say 8 points,
    # which is 1/12 and therefore wrong for any other question count.
    question_totals = [
        entry.get("question_count")
        for grouped in breakdown.values()
        for entry in (grouped or {}).values()
        if isinstance(entry, dict) and entry.get("question_count")
    ]
    lines += [
        "样本数与题数是两个独立判据：前者是均值背后的观测权重（多轮会累加），",
        "后者是涉及的题目个数（去重）。"
        + (
            f"本题集下单题翻转即可移动约 {round(100 / max(question_totals))} 个百分点，"
            if question_totals else
            "小题集下单题翻转即可显著移动百分比，"
        )
        + "所以任一切片读出的差异都要先看它的 sample_size。",
        "",
    ]
    return lines


def render_baseline_appendix(payload: dict[str, Any]) -> list[str]:
    """Baseline delta table, with an explicit warning when it may not be read."""
    comparability = payload.get("run_comparability") or {}
    gate = (payload.get("slice_report") or {}).get("group_gate") or {}
    gate_rows: dict[str, dict[str, Any]] = {}
    for result in (gate.get("groups") or {}).values():
        for item in result.get("metrics") or []:
            gate_rows[item["metric"]] = item

    lines: list[str] = []
    if gate_rows:
        lines += ["| 指标 | 基线 | 本次 | delta | 结论 |", "| --- | --- | --- | --- | --- |"]
        for metric, item in gate_rows.items():
            lines.append(
                f"| {metric} | {_fmt(item.get('baseline'))} | {_fmt(item.get('current'))} | "
                f"{_fmt(item.get('delta'))} | {item.get('verdict')} |"
            )
    else:
        lines.append("未提供基线，无法给出逐指标对比。")

    # The warning is emitted whenever the runs are incomparable, even with no
    # baseline: it is a statement about this experiment, not about the delta.
    if comparability and not comparability.get("comparable"):
        lines += [
            "",
            "> **本次运行的重复之间不可比，任何「变化」都可能不可作为趋势结论。**",
            "> 表中任何变动与测量噪声同量级，需先提高运行稳定性。",
        ]
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def render_report(payload: dict[str, Any]) -> str:
    """Render a full report from an aggregation payload."""
    slice_report = payload.get("slice_report") or {}
    title_run = payload.get("run_id") or (
        f"{payload.get('run_count')} 次运行聚合"
        if payload.get("run_count")
        else "生成回放"
    )

    lines = [
        f"# 生成评测报告：{title_run}",
        "",
        f"生成时间基线：{payload.get('started_at') or '（聚合结果，无单一时间）'}",
        "",
        "本报告按依赖顺序排列：先证明这次实验可被解释，再回答系统是否跑起来，"
        "最后才讨论答案。前一节不成立时，后一节的数字只能描述幸存样本。",
        "",
    ]

    bodies = {
        "credibility": render_credibility(payload),
        "usability": render_usability(payload),
        "quality": render_quality(payload),
        "safety": render_safety(payload, slice_report),
        "citation": render_citation(payload, slice_report),
    }

    for key, heading, purpose in SECTIONS:
        lines += [f"## {heading}", "", f"*{purpose}*", ""]
        lines += bodies[key]
        lines.append("")

    lines += ["## 闸门结论", "", *render_gate(payload), ""]

    lines += ["## 附录 A：分切片明细", "", *render_slice_appendix(slice_report)]
    lines += ["## 附录 B：与基线对比", "", *render_baseline_appendix(payload)]
    lines += [
        "## 附录 C：逐题状态",
        "",
        *render_per_question(payload),
    ]
    return "\n".join(lines).rstrip() + "\n"


def render_per_question(payload: dict[str, Any]) -> list[str]:
    """Per-question success and quality, so a failed row can be located."""
    per_question = payload.get("per_question") or {}
    if not per_question:
        return ["未提供逐题明细。", ""]
    lines = [
        "| 题目 | 完成/运行 | 成功率 | 必需词召回 |",
        "| --- | --- | --- | --- |",
    ]
    for question_id, item in per_question.items():
        lines.append(
            f"| {question_id} | {item.get('completed')}/{item.get('runs')} | "
            f"{_fmt(item.get('success_rate'))} | "
            f"{_fmt(item.get('required_term_recall_mean'))} |"
        )
    lines.append("")
    return lines


def load_payload(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_report(payload: dict[str, Any], output: str | Path) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_report(payload), encoding="utf-8", newline="\n")
    return output


__all__ = [
    "SECTIONS",
    "load_payload",
    "render_report",
    "write_report",
]
