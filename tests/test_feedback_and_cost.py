"""Offline unit tests for the PRD feedback loop, cost ledger, and triage flow."""

from __future__ import annotations

import json

import pytest

from src.cost_ledger import (
    QueryCostLedger,
    build_query_cost_record,
    estimate_tokens_from_text,
    format_cost_markdown,
    percentile,
    summarize_query_costs,
)
from src.feedback import (
    FeedbackLedger,
    build_feedback_record,
    compute_snapshot_id,
    short_hash,
    summarize_feedback,
)


def test_feedback_record_hashes_by_default_and_requires_reason_on_down():
    up = build_feedback_record(
        session_id="sess-1",
        question="PC注塑件壁厚一般多少毫米？",
        answer="推荐 1.016mm - 3.81mm [L1]。",
        verdict="up",
        deliverable=True,
        delivery_status="completed",
    )
    assert up.question_text is None
    assert len(up.question_hash) == 16
    assert up.question_hash == short_hash("PC注塑件壁厚一般多少毫米？")
    assert up.was_refusal is False

    with pytest.raises(ValueError, match="reason_code"):
        build_feedback_record(
            session_id="sess-1",
            question="q",
            answer="a",
            verdict="down",
        )

    down = build_feedback_record(
        session_id="sess-2",
        question="敏感设计问题",
        answer="当前资料无法确认。",
        verdict="down",
        reason_code="wrong_evidence",
        blamed_evidence_ids=["L1", "L2"],
        free_text="引用的是旧标准",
        include_question_text=True,
        deliverable=False,
        delivery_status="degraded_insufficient_evidence",
    )
    assert down.question_text == "敏感设计问题"
    assert down.was_refusal is True
    assert down.blamed_evidence_ids == ["L1", "L2"]


def test_feedback_ledger_roundtrip_triage_and_regression_export(tmp_path):
    ledger = FeedbackLedger(tmp_path / "feedback.jsonl")
    rec = build_feedback_record(
        session_id="s-1",
        question="钣金折弯距孔最小距离？",
        answer="资料不足。",
        verdict="down",
        reason_code="did_not_answer",
        blamed_evidence_ids=["L2"],
        free_text="指南里其实有 4 倍板厚",
        snapshot_id="snap-abc",
    )
    ledger.append(rec)

    # M1 must be attributed to metric_defect, never system_failure (PRD §8).
    with pytest.raises(ValueError, match="metric_defect"):
        ledger.triage(rec.feedback_id, failure_category="M1", attribution_kind="system_failure")

    triaged = ledger.triage(
        rec.feedback_id,
        failure_category="R2",
        attribution_kind="system_failure",
        disposition="提高表格切分优先级",
    )
    assert triaged.triage_status == "triaged"
    assert triaged.failure_category == "R2"
    assert triaged.triaged_at is not None

    reg_path = tmp_path / "regression.jsonl"
    exported = ledger.export_regression_case(rec.feedback_id, reg_path)
    assert exported["observation_tier"] == "single_observation"
    assert exported["failure_category"] == "R2"
    assert json.loads(reg_path.read_text(encoding="utf-8").strip())["source_feedback_id"] == rec.feedback_id

    reloaded = ledger.load_all()
    assert len(reloaded) == 1
    assert reloaded[0].promoted_to_regression is True


def test_feedback_summary_enforces_small_sample_and_low_submission_guardrails():
    rec = build_feedback_record(
        session_id="s-1",
        question="q",
        answer="a",
        verdict="down",
        reason_code="hallucinated",
    )
    summary = summarize_feedback([rec], total_sessions=50)
    assert summary["descriptive_only"] is True
    assert summary["submission_rate"] == 0.02
    assert any("描述性统计" in w for w in summary["warnings"])
    assert any("先怀疑入口设计" in w for w in summary["warnings"])


def test_snapshot_id_is_deterministic_without_loading_models(tmp_path):
    chroma = tmp_path / "chroma_db"
    chroma.mkdir()
    (chroma / "ingest_manifest.json").write_text('{"files": {"a.md": "123"}}', encoding="utf-8")
    first = compute_snapshot_id(chroma, "design_knowledge", "R")
    second = compute_snapshot_id(chroma, "design_knowledge", "R")
    assert first == second
    assert len(first) == 16


def test_cost_ledger_computes_p50_p95_cold_start_and_cost(tmp_path):
    ledger = QueryCostLedger(tmp_path / "costs.jsonl")
    state_cold = {
        "route": "local",
        "question_type": "simple",
        "delivery_status": "completed",
        "deliverable": True,
        "sources": [{"id": "L1"}],
        "retrieval_profile": {"cache_hit": False, "reranker_seconds": 28.4},
        "execution_trace": [
            {"node": "retrieve", "duration_seconds": 32.0},
            {"node": "generate", "duration_seconds": 12.0},
        ],
    }
    r1 = build_query_cost_record(
        session_id="s-1",
        question_hash="h1",
        question="PC注塑件壁厚一般多少？",
        answer="推荐 1.016mm - 3.81mm [L1]。",
        state=state_cold,
        generator_backend="v2",
        input_price_per_1m_cny=2.0,
        output_price_per_1m_cny=8.0,
        wall_seconds=44.5,
    )
    state_warm = {**state_cold, "retrieval_profile": {"cache_hit": True, "reranker_seconds": 0.0}}
    r2 = build_query_cost_record(
        session_id="s-2",
        question_hash="h2",
        question="PC注塑件壁厚一般多少？",
        answer="推荐 1.016mm - 3.81mm [L1]。",
        state=state_warm,
        generator_backend="v2",
        input_price_per_1m_cny=2.0,
        output_price_per_1m_cny=8.0,
        wall_seconds=12.5,
    )
    ledger.append(r1)
    ledger.append(r2)

    loaded = ledger.load_all()
    summary = summarize_query_costs(loaded)
    assert summary["total_queries"] == 2
    assert summary["cold_start_count"] == 1
    assert summary["cold_start_rate"] == 0.5
    assert summary["latency_p50_seconds"] == percentile([44.5, 12.5], 50)
    assert summary["total_estimated_cost_cny"] > 0
    assert estimate_tokens_from_text("测试中文abc") > 0

    md = format_cost_markdown(r1)
    assert "冷检索" in md
    assert "44.5 s" in md
    assert "¥" in md


def test_app_feedback_handlers_and_progress_helpers(tmp_path):
    import app as app_module

    ledger = FeedbackLedger(tmp_path / "ui_feedback.jsonl")
    turn = {
        "session_id": "ui-sess-1",
        "question": "铝件阳极氧化膜厚一般多少微米？",
        "answer": "1 mil = 25 微米 [L1]。",
        "generator_backend": "v2",
        "latency_seconds": 31.2,
        "evidence_count": 2,
        "snapshot_id": "snap-1",
        "delivery_status": "completed",
        "deliverable": True,
    }
    msg_up, _ = app_module.submit_positive_feedback(turn, ledger=ledger)
    assert "感谢反馈" in msg_up
    assert "哈希" not in msg_up

    msg_missing, _panel, *_ = app_module.submit_negative_feedback(
        turn, None, [], "", False, ledger=ledger
    )
    assert "先在四项原因中勾选一项" in msg_missing

    msg_down, *_ = app_module.submit_negative_feedback(
        turn,
        "哪条证据不对",
        ["[L1] AAC_Specifying_Anodized_Aluminum.md"],
        "单位换算没说清",
        False,
        ledger=ledger,
    )
    assert "已记录问题" in msg_down
    assert "哈希" not in msg_down
    assert "坏案例台账" not in msg_down

    saved = ledger.load_all()
    assert len(saved) == 2
    assert saved[0].verdict == "up"
    assert saved[1].verdict == "down"
    assert saved[1].reason_code == "wrong_evidence"
    assert saved[1].blamed_evidence_ids == ["L1"]
    assert saved[1].question_text is None

    progress = app_module._stage_progress_text(
        {"sources": [{"id": "L1", "retrieval_evidence_status": "direct"}]},
        "retrieve",
        8.4,
    )
    assert "8.4 s" in progress
    assert "1" in progress


def test_triage_cli_summary_and_triage(tmp_path, capsys):
    from scripts import triage_feedback

    fb_path = tmp_path / "fb.jsonl"
    cost_path = tmp_path / "cost.jsonl"
    ledger = FeedbackLedger(fb_path)
    rec = ledger.append(
        build_feedback_record(
            session_id="s-cli",
            question="测试问题",
            answer="测试回答",
            verdict="down",
            reason_code="did_not_answer",
        )
    )

    rc = triage_feedback.main(
        [
            "--feedback-ledger",
            str(fb_path),
            "--cost-ledger",
            str(cost_path),
            "--triage",
            rec.feedback_id,
            "--category",
            "G1",
            "--attribution",
            "system_failure",
            "--disposition",
            "补充数值枚举约束",
        ]
    )
    assert rc == 0

    rc2 = triage_feedback.main(
        [
            "--feedback-ledger",
            str(fb_path),
            "--cost-ledger",
            str(cost_path),
            "--summary",
            "--json",
        ]
    )
    assert rc2 == 0
    out = capsys.readouterr().out
    payload = json.loads(out[out.index("{") :])
    assert payload["feedback"]["triaged_bad_cases"] == 1
    assert payload["feedback"]["by_failure_category"] == {"G1": 1}


def test_ui_theme_cards_render_badges_and_escape_html():
    from src.ui_theme import (
        render_cost_card,
        render_delivery_card,
        render_sources_card,
        render_trace_card,
    )

    ok_card = render_delivery_card(
        {"deliverable": True, "delivery_status": "completed", "errors": []},
        route_label="本地知识库",
    )
    assert "可交付" in ok_card
    assert "本地知识库" in ok_card

    xss_card = render_sources_card(
        [
            {
                "id": "L1",
                "title": "<script>alert(1)</script>",
                "location": "05_制造工艺/test.md",
                "type": "local",
                "retrieval_evidence_status": "direct",
            }
        ]
    )
    assert "<script>" not in xss_card
    assert "&lt;script&gt;" in xss_card
    assert "直接依据" in xss_card
    assert 'data-status="direct"' in xss_card

    cost_html = render_cost_card(
        {
            "cache_hit": False,
            "total_latency_seconds": 33.9,
            "retrieval_seconds": 28.0,
            "reranker_seconds": 25.0,
            "generation_seconds": 5.9,
            "total_tokens_est": 1240,
            "prompt_tokens_est": 980,
            "completion_tokens_est": 260,
            "estimated_cost_cny": 0.004,
        }
    )
    assert "33.9 s" in cost_html
    assert "冷检索" in cost_html

    trace_html = render_trace_card(
        {
            "question_type": "simple",
            "claim_support_rate": 1.0,
            "rewrite_attempts": 0,
            "execution_trace": [{"node": "retrieve", "duration_seconds": 3.1}],
        }
    )
    assert "retrieve 3.1s" in trace_html
