import json
from pathlib import Path

from langchain_core.documents import Document

from src.frozen_evidence import (
    FrozenDocument,
    FrozenRetrievalCase,
    build_evidence_pack,
    soft_audit,
)


def make_case():
    document = FrozenDocument.from_document(
        Document(
            page_content="座高为400mm~440mm。",
            metadata={
                "source": "设计规范与标准/3326-2016-gbt-cd-300.pdf",
                "title": "家具标准",
                "chunk_id": "chunk-1",
                "content_hash": "hash-1",
                "retrieval_evidence_status": "direct",
            },
        ),
        1,
    )
    return FrozenRetrievalCase(
        question_id="q01_hit",
        question="座高是多少？",
        retrieval_snapshot_id="snapshot-1",
        retrieval_config={"top_k": 10},
        index_fingerprint={"count": 1},
        documents=(document,),
        retrieval_profile={},
        retrieval_timing={"retrieval_seconds": 1.0},
    )


def test_evidence_pack_preserves_retrieval_order_and_citations():
    pack = build_evidence_pack(make_case())
    assert pack.allowed_citations == ("L1",)
    assert "[L1]" in pack.context_text()
    assert "座高为400mm~440mm" in pack.context_text()


def test_soft_audit_checks_citations_spans_and_sources():
    pack = build_evidence_pack(make_case())
    audit = soft_audit(
        "座高为400mm~440mm。[L1]",
        pack,
        [{"id": "s1", "text": "400mm~440mm"}],
        ["设计规范与标准/3326-2016-gbt-cd-300.pdf"],
    )
    assert audit["citation_validity"] is True
    assert audit["expected_answer_span_recall"] == 1.0
    assert audit["source_coverage"] == 1.0


def test_soft_audit_reports_invalid_citation_without_blocking():
    pack = build_evidence_pack(make_case())
    audit = soft_audit("座高为400mm~440mm。[L9]", pack)
    assert audit["citation_validity"] is False
    assert audit["invalid_citations"] == ["L9"]


def test_soft_audit_normalises_equivalent_dimensions():
    pack = build_evidence_pack(make_case())
    audit = soft_audit(
        "座高不小于400 mm，最大为440毫米。[L1]",
        pack,
        [{"id": "s1", "text": "座高为400mm~440mm"}],
    )
    assert audit["expected_answer_span_recall"] == 1.0


def test_soft_audit_marks_missing_citation_not_applicable_for_fallback():
    pack = build_evidence_pack(make_case())
    audit = soft_audit("当前资料无法确认。", pack)
    assert audit["citation_status"] == "not_applicable"
    assert audit["citation_validity"] is None


def test_soft_audit_does_not_flag_refusal_year_as_unsupported_number():
    pack = build_evidence_pack(make_case())
    audit = soft_audit("当前资料无法确认2025年的趋势。", pack)
    assert audit["unsupported_number_count"] == 0


def test_soft_audit_ignores_requested_year_in_later_refusal_sentence():
    pack = build_evidence_pack(make_case())
    audit = soft_audit(
        "当前资料无法确认产品趋势。资料未提供针对2025年的统计或判断。",
        pack,
    )
    assert audit["unsupported_number_count"] == 0


def test_soft_audit_matches_semantic_age_percentile_span():
    pack = build_evidence_pack(make_case())
    audit = soft_audit(
        "先明确目标年龄或身高范围，再取得对应群体的百分位数据并据此确定高度。[L1]",
        pack,
        [{"id": "s1", "text": "按目标年龄段百分位数推算"}],
    )
    assert audit["expected_answer_span_recall"] == 1.0


def test_soft_audit_matches_range_with_table_label_and_repeated_units():
    pack = build_evidence_pack(make_case())
    audit = soft_audit(
        "座深（T1）：340 mm～460 mm。[L1]",
        pack,
        [{"id": "s1", "text": "座深340mm~460mm"}],
    )
    assert audit["expected_answer_span_recall"] == 1.0


def test_soft_audit_matches_range_with_bare_table_label():
    # Models often drop the parentheses: "座深 T1：340～460 mm".  The bare
    # label used to survive normalisation as "座深t1340", corrupting both the
    # key phrase and the leading digit.
    pack = build_evidence_pack(make_case())
    audit = soft_audit(
        "**座深 T1：340～460 mm** [L1]",
        pack,
        [{"id": "s1", "text": "座深340mm~460mm"}],
    )
    assert audit["expected_answer_span_recall"] == 1.0


def test_soft_audit_matches_range_across_label_variants():
    pack = build_evidence_pack(make_case())
    for answer in (
        "座深（T1）：340 mm～460 mm [L1]",
        "座深T1：340～460 mm[L1]",
        "座深（T1）340 mm~460 mm",
    ):
        audit = soft_audit(
            answer,
            pack,
            [{"id": "s1", "text": "座深340mm~460mm"}],
        )
        assert audit["expected_answer_span_recall"] == 1.0, answer


def test_span_noise_stripping_keeps_real_identifiers():
    # Standard numbers, material names and decimal values must not be
    # mistaken for table labels.
    from src.frozen_evidence import _strip_span_noise

    assert _strip_span_noise("GB/T 3326—2016 中座深为 340～460 mm [L1]").endswith("340～460 mm ")
    assert "PP" in _strip_span_noise("PP 材料密度 0.9 g/cm3 [L2]")
    assert "Tritan" in _strip_span_noise("Tritan 在 100 ℃ 下稳定 [L1]")


def test_citation_usage_ratio_is_not_named_coverage():
    # ``citation_coverage`` used to mean "how many allowed ids were used",
    # which reads like claim-level coverage.  It must not be confused with
    # faithfulness, so the misleading name is gone.
    pack = build_evidence_pack(make_case())
    audit = soft_audit("座高为400mm~440mm。[L1]", pack, [{"id": "s1", "text": "400mm~440mm"}])
    assert "citation_coverage" not in audit
    assert audit["citation_id_usage_ratio"] is not None


def test_boundary_questions_are_excluded_from_citation_metrics():
    # A correct refusal cites nothing; averaging it into fact-question
    # citation rates would silently dilute "citation validity 100%".
    pack = build_evidence_pack(make_case())
    audit = soft_audit(
        "当前资料无法确认该数值，需要补充实验数据。",
        pack,
        [{"id": "s1", "text": "座高为400mm~440mm"}],
    )
    assert audit["refusal_correctness"] is True
    assert audit["citation_metric_applicable"] is False
    assert audit["span_metric_applicable"] is False


def test_fact_questions_remain_applicable_to_citation_metrics():
    pack = build_evidence_pack(make_case())
    audit = soft_audit(
        "座高为400mm~440mm。[L1]",
        pack,
        [{"id": "s1", "text": "400mm~440mm"}],
        ["设计规范与标准/3326-2016-gbt-cd-300.pdf"],
    )
    assert audit["refusal_correctness"] is None
    assert audit["citation_metric_applicable"] is True
    assert audit["span_metric_applicable"] is True


def test_full_width_bracket_citations_are_counted():
    # Models often emit 【L1】 or 〔L1〕 instead of [L1].  If those are not
    # recognised, a genuinely cited answer is misread as citation-free and
    # silently lands in ``not_applicable``.
    pack = build_evidence_pack(make_case())
    for answer in ("座高为400mm~440mm。【L1】", "座高为400mm~440mm。〔L1〕"):
        audit = soft_audit(answer, pack, [{"id": "s1", "text": "400mm~440mm"}])
        assert audit["used_citations"] == ["L1"], answer
        assert audit["citation_status"] == "pass", answer


def test_required_terms_allow_semantic_alternatives():
    pack = build_evidence_pack(make_case())
    audit = soft_audit(
        "产品包含顶层设计，并采用系统设计方法。[L1]",
        pack,
        required_terms=(("顶层设计",), ("系统化设计", "系统设计")),
    )
    assert audit["required_term_recall"] == 1.0


def test_refusal_requirement_accepts_equivalent_refusal():
    pack = build_evidence_pack(make_case())
    audit = soft_audit(
        "当前资料无法确认2025年的趋势。",
        pack,
        refusal_requirements=(("无法确认", "无法从本地资料中找到"),),
    )
    assert audit["refusal_requirements"][0]["matched"] is True


def test_ambiguity_requirements_drive_boundary_metric():
    pack = build_evidence_pack(make_case())
    audit = soft_audit(
        "儿童座椅不能直接套用通用标准，应按年龄段百分位确定。[L1]",
        pack,
        ambiguity_requirements=(("儿童",), ("不能直接", "不能套用"), ("年龄段", "百分位")),
    )
    assert audit["ambiguity_safety"] is True


# --- behaviour predicates -------------------------------------------------
# A word list scores wording, not meaning.  Over two recorded runs the same
# model produced ``不能无条件作为儿童座椅高度`` and ``不能直接作为儿童座椅的
# 无条件推荐值`` -- equivalent statements -- and the list scored one False and
# the other True.  These tests pin the composite predicate that replaced it.


def test_composite_predicate_accepts_synonymous_prohibitions():
    """Both recorded rewordings must pass, or the metric flips on phrasing."""
    pack = build_evidence_pack(make_case())
    requirement = {
        "negation": ["不能", "不应", "不宜", "不得", "无法"],
        "object": ["直接", "简单", "无条件", "套用"],
    }
    for answer in (
        "这是一般桌椅尺寸，不能无条件作为儿童座椅高度。[L1]",
        "因此不能直接作为儿童座椅的无条件推荐值。[L1]",
        "该范围不宜简单套用到儿童座椅。[L1]",
    ):
        audit = soft_audit(answer, pack, ambiguity_requirements=(requirement,))
        assert audit["ambiguity_safety"] is True, answer


def test_composite_predicate_rejects_answers_that_would_match_loosely():
    """The reverse test: genuinely wrong answers must still fail.

    A permissive rule such as "answer contains 不能" would pass all of these,
    which is why the negation family alone is not enough.
    """
    pack = build_evidence_pack(make_case())
    requirement = {
        "negation": ["不能", "不应", "不宜", "不得", "无法"],
        "object": ["直接", "简单", "无条件", "套用"],
    }
    for answer in (
        "儿童座椅座面高度可直接采用400~440mm，这是标准值。[L1]",
        "儿童座椅高度建议取450mm，符合人体工学。[L1]",
        "儿童产品不能忽视，设计时应充分考虑。[L1]",
        "儿童座椅高度无法测量。[L1]",
    ):
        audit = soft_audit(answer, pack, ambiguity_requirements=(requirement,))
        assert audit["ambiguity_safety"] is False, answer


def test_composite_predicate_defaults_when_a_side_is_omitted():
    """A half-specified predicate falls back to the built-in families."""
    pack = build_evidence_pack(make_case())
    audit = soft_audit(
        "不能简单套用通用尺寸。[L1]",
        pack,
        ambiguity_requirements=({"object": ["套用"]},),
    )
    assert audit["ambiguity_safety"] is True


def test_composite_predicate_accepts_a_bare_dict():
    """A single predicate may be passed without the outer sequence."""
    pack = build_evidence_pack(make_case())
    audit = soft_audit(
        "不能直接作为儿童座椅定值。[L1]",
        pack,
        ambiguity_requirements={"negation": ["不能"], "object": ["直接"]},
    )
    assert audit["ambiguity_safety"] is True


def test_word_list_groups_still_work_alongside_predicates():
    """Legacy and composite groups can be mixed in one requirement list."""
    pack = build_evidence_pack(make_case())
    audit = soft_audit(
        "儿童座椅不能直接套用通用标准，应按目标人群实测数据推算。[L1]",
        pack,
        ambiguity_requirements=(
            ("儿童", "儿童座椅"),
            {"negation": ["不能"], "object": ["直接", "套用"]},
            ("年龄段", "百分位", "实测", "人体测量"),
        ),
    )
    assert audit["ambiguity_safety"] is True
    kinds = [item["kind"] for item in audit["ambiguity_requirements"]]
    assert kinds == ["alternatives", "composite", "alternatives"]


def test_audit_reports_predicate_hits_for_diagnosis():
    """A failing predicate must say which side missed, not just False."""
    pack = build_evidence_pack(make_case())
    audit = soft_audit(
        "儿童座椅高度取450mm。[L1]",
        pack,
        ambiguity_requirements=({"negation": ["不能"], "object": ["直接"]},),
    )
    result = audit["ambiguity_requirements"][0]
    assert result["matched"] is False
    assert result["negation_hits"] == []
    assert result["object_hits"] == []
    assert result["kind"] == "composite"


def test_audit_version_is_bumped_for_the_new_rule():
    """Scoring rule changed, so old runs must not be averaged with new ones.

    v4: an unanswered question no longer yields a failed behaviour verdict, and
    citation applicability is decided from the contract rather than from the
    verdict that happened to be reached.
    """
    from src.frozen_evidence import AUDIT_VERSION

    assert AUDIT_VERSION == "soft-audit-behaviour-v4"


def test_no_question_specific_branch_remains_in_the_audit():
    """The audit must encode rules, not dataset quirks.

    Comments may still mention the removed branch by name; what must not exist
    is executable code keyed on a question id.  Comments are stripped before
    checking so a future explanatory note cannot fail the test.
    """
    source = (Path(__file__).resolve().parents[1] / "src" / "frozen_evidence.py").read_text(
        encoding="utf-8"
    )
    without_comments = "\n".join(
        line.split("#", 1)[0] for line in source.splitlines()
    )
    assert "q17_ambiguous" not in without_comments
    assert "question_id ==" not in without_comments


def test_summarize_rows_reports_p95_and_required_term_recall():
    from eval_generation_replay import summarize_rows

    rows = [
        {
            "question_id": f"q{index}",
            "status": "completed",
            "generation_latency_seconds": float(index),
            "audit": {
                "citation_metric_applicable": True,
                "citation_status": "pass",
                "required_term_recall": 1.0,
            },
        }
        for index in range(1, 11)
    ]
    summary = summarize_rows(rows)
    assert summary["generation_latency_seconds"]["p95"] == 10.0
    assert summary["required_term_recall_mean"] == 1.0
    assert summary["required_term_metric_sample_size"] == 10


def test_sanitize_answer_strips_trailing_control_token():
    from src.generator_v2 import sanitize_answer

    cleaned, rules = sanitize_answer("座高为400mm~440mm。[L1]\n.calc")
    assert cleaned == "座高为400mm~440mm。[L1]"
    assert rules == ["remove_trailing_control_token"]


def test_sanitize_answer_handles_repeated_and_punctuated_control_tokens():
    from src.generator_v2 import sanitize_answer

    for raw in (
        "座高为400mm~440mm。[L1].calc.calc",
        "座高为400mm~440mm。[L1]。.final",
        "座高为400mm~440mm。[L1] .answer",
        "座高为400mm~440mm。[L1]\n.summary\n",
    ):
        cleaned, rules = sanitize_answer(raw)
        assert cleaned == "座高为400mm~440mm。[L1]", raw
        assert "remove_trailing_control_token" in rules, raw


def test_sanitize_answer_does_not_touch_legitimate_body_text():
    from src.generator_v2 import sanitize_answer

    # Real sentences, decimals, English abbreviations and file names must
    # survive untouched — only trailing control artefacts are removed.
    for body in (
        "座深为340mm~460mm，约为座高的0.7倍。[L1]",
        "该结论来自 GB/T 3326—2016 的 5.2 节。[L1]",
        "可用 PP、ABS 等材料，密度约0.9 g/cm3。[L2]",
        "参考 data/final_report.md 中的结论。[L3]",
    ):
        cleaned, rules = sanitize_answer(body)
        assert cleaned == body, body
        assert rules == [], body


def test_sanitize_answer_keeps_inner_control_word_when_not_suffix():
    from src.generator_v2 import sanitize_answer

    # ".calc" appearing mid-sentence is data, not a control token.
    body = "配置项 config.calc 用于开启计算。[L1]"
    cleaned, rules = sanitize_answer(body)
    assert cleaned == body
    assert rules == []


def test_sanitize_answer_strips_trailing_xml_like_tag():
    from src.generator_v2 import sanitize_answer

    cleaned, rules = sanitize_answer("座高为400mm~440mm。[L1]\n</answer>")
    assert cleaned == "座高为400mm~440mm。[L1]"
    assert rules == ["remove_trailing_tag"]


def test_generation_result_keeps_raw_answer_and_sanitization_flag():
    from src.generator_v2 import GenerationResult

    result = GenerationResult(
        question_id="q01",
        question="座高是多少？",
        answer="座高为400mm~440mm。[L1]",
        model="test-model",
        generation_latency_seconds=0.1,
        error=None,
        error_type=None,
        attempt_count=1,
        generation_status="completed",
        answer_status="generated",
        audit={},
        raw_answer="座高为400mm~440mm。[L1].calc",
        sanitization_applied=True,
        sanitization_rules=("remove_trailing_control_token",),
    )
    payload = result.as_dict()
    # The raw output stays in the record so pollution remains auditable; the
    # sanitised answer is what gets scored.
    assert payload["raw_answer"] == "座高为400mm~440mm。[L1].calc"
    assert payload["answer"] == "座高为400mm~440mm。[L1]"
    assert payload["sanitization_applied"] is True
    assert payload["sanitization_rules"] == ["remove_trailing_control_token"]


def test_summarize_rows_counts_malformed_outputs():
    from eval_generation_replay import summarize_rows

    rows = [
        {
            "question_id": "q01",
            "status": "completed",
            "generation_latency_seconds": 1.0,
            "sanitization_applied": False,
            "audit": {"citation_metric_applicable": True, "citation_status": "pass"},
        },
        {
            "question_id": "q17",
            "status": "completed",
            "generation_latency_seconds": 2.0,
            "sanitization_applied": True,
            "sanitization_rules": ["remove_trailing_control_token"],
            "audit": {"citation_metric_applicable": True, "citation_status": "pass"},
        },
    ]
    summary = summarize_rows(rows)
    assert summary["malformed_output_count"] == 1
    assert summary["malformed_output_question_ids"] == ["q17"]
    assert summary["sanitization_rule_counts"] == {"remove_trailing_control_token": 1}


def test_summarize_rows_ignores_unfinished_rows_when_counting_malformed():
    from eval_generation_replay import summarize_rows

    rows = [
        {"question_id": "q09", "status": "provider_timeout", "sanitization_applied": True, "audit": {}},
        {"question_id": "q10", "status": "completed", "generation_latency_seconds": 1.0, "audit": {}},
    ]
    summary = summarize_rows(rows)
    # A provider failure is not a malformed model answer.
    assert summary["malformed_output_count"] == 0
    assert summary["generation_success_rate"] == 0.5


# ---------------------------------------------------------------------------
# Retry bookkeeping
#
# The point of these tests is not "retry works" but "retry stays visible".
# A retry that silently replaces a failure turns an unstable run into a clean
# one, which is the exact reporting failure this harness exists to prevent.
# ---------------------------------------------------------------------------


def test_retried_success_is_counted_separately_from_first_try():
    from eval_generation_replay import summarize_rows

    rows = [
        {
            "question_id": "q01",
            "status": "completed",
            "generation_latency_seconds": 5.0,
            "audit": {"failed_attempt_count": 0, "citation_metric_applicable": True},
        },
        {
            "question_id": "q04",
            "status": "completed",
            "generation_latency_seconds": 65.0,
            "audit": {"failed_attempt_count": 1, "citation_metric_applicable": True},
        },
    ]
    summary = summarize_rows(rows)
    # Both rows completed, so the pass rate alone would look clean.
    assert summary["generation_success_rate"] == 1.0
    # ...but the retry dependence is visible.
    assert summary["first_try_success_count"] == 1
    assert summary["retried_success_count"] == 1
    assert summary["retried_success_question_ids"] == ["q04"]
    assert summary["failed_attempt_count_total"] == 1


def test_rows_without_retry_fields_are_treated_as_first_try():
    # Runs recorded before retry bookkeeping existed must not be mislabelled.
    from eval_generation_replay import summarize_rows

    rows = [
        {
            "question_id": "q01",
            "status": "completed",
            "generation_latency_seconds": 1.0,
            "audit": {},
        }
    ]
    summary = summarize_rows(rows)
    assert summary["first_try_success_count"] == 1
    assert summary["retried_success_count"] == 0
    assert summary["failed_attempt_count_total"] == 0


def test_provider_failures_are_not_counted_as_retried_successes():
    from eval_generation_replay import summarize_rows

    rows = [
        {
            "question_id": "q05",
            "status": "provider_timeout",
            "audit": {"failed_attempt_count": 3},
        }
    ]
    summary = summarize_rows(rows)
    # A row that never succeeded is a failure, not a retried success.
    assert summary["retried_success_count"] == 0
    assert summary["failed_attempt_count_total"] == 0


def test_retry_backoff_gives_rate_limits_the_longest_wait():
    from src.generator_v2 import _retry_after_seconds

    # Immediate retry is exactly what a rate limit asks us not to do.
    assert _retry_after_seconds("provider_rate_limit", 0) > _retry_after_seconds(
        "provider_timeout", 0
    )
    # Backoff must grow, not shrink.
    assert _retry_after_seconds("provider_timeout", 1) >= _retry_after_seconds(
        "provider_timeout", 0
    )


def test_retryable_classes_exclude_non_provider_failures():
    from src.generator_v2 import RETRYABLE_FAILURE_CLASSES

    assert "provider_timeout" in RETRYABLE_FAILURE_CLASSES
    assert "provider_error" in RETRYABLE_FAILURE_CLASSES
    assert "provider_rate_limit" in RETRYABLE_FAILURE_CLASSES
    # Re-rolling a malformed answer does not fix a parser.
    assert "invalid_response" not in RETRYABLE_FAILURE_CLASSES
    assert "evaluation_error" not in RETRYABLE_FAILURE_CLASSES


def test_generate_from_pack_retries_then_succeeds_and_records_both_attempts(monkeypatch):
    import asyncio

    from src import generator_v2

    attempts = {"count": 0}

    class FlakyChain:
        async def ainvoke(self, _payload):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise TimeoutError("Request timed out.")
            return "座高为400mm~440mm。[L1]"

    monkeypatch.setattr(generator_v2, "require_role_model", lambda _role: None)
    monkeypatch.setattr(generator_v2, "_build_chain", lambda: _StubChain(FlakyChain()))
    monkeypatch.setattr(generator_v2, "_retry_after_seconds", lambda *_: 0.0)

    pack = build_evidence_pack(make_case())
    result = asyncio.run(generator_v2.generate_from_pack(pack, max_retries=2))

    assert result.generation_status == "completed"
    assert result.attempt_count == 2
    # The original timeout stays on the record.
    assert result.audit["failed_attempt_count"] == 1
    statuses = [item["status"] for item in result.audit["attempts"]]
    assert statuses == ["provider_timeout", "completed"]


def test_generate_from_pack_gives_up_after_retries_are_exhausted(monkeypatch):
    import asyncio

    from src import generator_v2

    class AlwaysFailingChain:
        async def ainvoke(self, _payload):
            raise TimeoutError("Request timed out.")

    monkeypatch.setattr(generator_v2, "require_role_model", lambda _role: None)
    monkeypatch.setattr(generator_v2, "_build_chain", lambda: _StubChain(AlwaysFailingChain()))
    monkeypatch.setattr(generator_v2, "_retry_after_seconds", lambda *_: 0.0)

    pack = build_evidence_pack(make_case())
    result = asyncio.run(generator_v2.generate_from_pack(pack, max_retries=2))

    assert result.generation_status == "provider_timeout"
    assert result.attempt_count == 3  # initial call plus two retries
    assert result.audit["failed_attempt_count"] == 3
    assert result.answer_status == "fallback"


def test_generate_from_pack_does_not_retry_on_success(monkeypatch):
    import asyncio

    from src import generator_v2

    attempts = {"count": 0}

    class GoodChain:
        async def ainvoke(self, _payload):
            attempts["count"] += 1
            return "座高为400mm~440mm。[L1]"

    monkeypatch.setattr(generator_v2, "require_role_model", lambda _role: None)
    monkeypatch.setattr(generator_v2, "_build_chain", lambda: _StubChain(GoodChain()))

    pack = build_evidence_pack(make_case())
    result = asyncio.run(generator_v2.generate_from_pack(pack, max_retries=2))

    assert attempts["count"] == 1
    assert result.attempt_count == 1
    assert result.audit["failed_attempt_count"] == 0


class _StubChain:
    """Chain stub that ignores the prompt template and calls the model mock.

    ``generate_from_pack`` builds ``prompt | model | parser``.  Substituting the
    whole expression is simpler and less brittle than faking each link, so the
    tests patch ``_build_chain`` instead.
    """

    def __init__(self, model):
        self._model = model

    async def ainvoke(self, payload):
        return await self._model.ainvoke(payload)
