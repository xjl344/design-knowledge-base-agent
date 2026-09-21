"""Tests for the composite multi-hop snapshot and its per-hop scoring.

Two things are being protected here.

The first is the anti-fabrication guard.  Composite cases pool chunks that were
retrieved for a *different* question, so nothing guarantees they contain what
the new question needs.  A composite question whose evidence does not contain
the answer would measure retrieval coverage while claiming to measure
generation ability, so the builder has to refuse to emit one.

The second is that ``hop_recall`` scores each hop on its own terms.  If hops
shared a pool of terms, one hop's facts would satisfy another hop's check and
every answer would look complete -- the metric would report 1.0 no matter what
the system did.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from langchain_core.documents import Document

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.frozen_evidence import (  # noqa: E402
    FrozenDocument,
    FrozenRetrievalCase,
    build_evidence_pack,
    load_cases,
    soft_audit,
)

MULTIHOP_SNAPSHOT = ROOT / "data" / "frozen_multihop_cases.jsonl"
MULTIHOP_CONTRACT = ROOT / "data" / "generation_eval.multihop.v1.json"
MULTIHOP_SPECS = ROOT / "data" / "multihop_specs.json"


def _document(text: str, chunk_id: str, source: str = "标准/甲.pdf") -> FrozenDocument:
    return FrozenDocument.from_document(
        Document(
            page_content=text,
            metadata={
                "source": source,
                "title": "测试标准",
                "chunk_id": chunk_id,
                "content_hash": f"hash-{chunk_id}",
                "retrieval_evidence_status": "direct",
            },
        ),
        1,
    )


def _case(question_id: str, documents: tuple[FrozenDocument, ...]) -> FrozenRetrievalCase:
    return FrozenRetrievalCase(
        question_id=question_id,
        question="跨文档问题？",
        retrieval_snapshot_id="snapshot-test",
        retrieval_config={"top_k": 10},
        index_fingerprint={"count": len(documents)},
        documents=documents,
        retrieval_profile={},
        retrieval_timing={"retrieval_seconds": 1.0},
    )


# --- per-hop scoring ------------------------------------------------------


def _hop_pack():
    """Two hops whose evidence lives in two different documents."""
    first = _document("桌面高 680~760，座高 400~440。", "chunk-a", "标准/甲.pdf")
    second = _document("未成年人 4岁~17岁。", "chunk-b", "标准/乙.pdf")
    return build_evidence_pack(_case("mh-test", (first, second)))


def _hops():
    return [
        {
            "hop_id": "h1",
            "from_question": "q01_hit",
            "source_chunk_id": "chunk-a",
            "expected_span": "680~760",
            "required_terms": [["680~760"], ["400~440"]],
        },
        {
            "hop_id": "h2",
            "from_question": "q06_hit",
            "source_chunk_id": "chunk-b",
            "expected_span": "4岁~17岁",
            "required_terms": [["未成年人"], ["4岁~17岁"]],
        },
    ]


def test_hop_recall_is_one_when_every_hop_is_covered():
    audit = soft_audit(
        "桌面高 680mm~760mm，座高 400mm~440mm。[L1] 适用于 4岁~17岁 未成年人。[L2]",
        _hop_pack(),
        required_hops=_hops(),
    )
    assert audit["hop_recall"] == 1.0
    assert audit["hop_metric_applicable"] is True
    assert [item["matched"] for item in audit["hop_results"]] == [True, True]


def test_hop_recall_drops_when_one_hop_is_missing():
    """The second hop's document is never consulted."""
    audit = soft_audit(
        "桌面高 680mm~760mm，座高 400mm~440mm。[L1]",
        _hop_pack(),
        required_hops=_hops(),
    )
    assert audit["hop_recall"] == 0.5
    results = {item["hop_id"]: item for item in audit["hop_results"]}
    assert results["h1"]["matched"] is True
    assert results["h2"]["matched"] is False


def test_one_hop_cannot_satisfy_another_hops_terms():
    """Each hop is judged on its own terms, never on the pooled set.

    If the hops shared a term pool, the first hop's numbers would cover the
    second hop's check and a single-document answer would score 1.0 -- which is
    precisely the failure a multi-hop metric exists to catch.
    """
    audit = soft_audit(
        "桌面高 680mm~760mm，座高 400mm~440mm。[L1]",
        _hop_pack(),
        required_hops=_hops(),
    )
    second = next(item for item in audit["hop_results"] if item["hop_id"] == "h2")
    assert second["matched"] is False
    assert all(not group["matched"] for group in second["terms"])


def test_hop_recall_is_not_applicable_without_hops():
    audit = soft_audit("座高为400mm~440mm。[L1]", _hop_pack())
    assert audit["hop_recall"] is None
    assert audit["hop_metric_applicable"] is False
    assert audit["hop_results"] == []


def test_hop_recall_is_not_scored_for_a_provider_substitute():
    """A fallback answer is an availability failure, not a multi-hop failure."""
    from src.frozen_evidence import FALLBACK_ANSWER_WITH_EVIDENCE

    audit = soft_audit(
        FALLBACK_ANSWER_WITH_EVIDENCE,
        _hop_pack(),
        required_hops=_hops(),
    )
    assert audit["hop_recall"] is None
    assert audit["hop_metric_applicable"] is False
    # The per-hop detail survives for diagnosis even when nothing is scored.
    assert len(audit["hop_results"]) == 2


def test_hop_metric_is_not_applicable_to_boundary_questions():
    """A refusal question exercises policy, not hop coverage."""
    audit = soft_audit(
        "当前资料无法确认。",
        _hop_pack(),
        refusal_requirements=[["无法确认"]],
        required_hops=_hops(),
    )
    assert audit["hop_metric_applicable"] is False
    assert audit["hop_recall"] is None


# --- per-item character budget -------------------------------------------
# Raising `max_items` adds evidence but multiplies context length, and measured
# calls with a ~13k-char context sat close enough to the ceiling that provider
# variance pushed one over it.  `max_chars_per_item` shrinks each chunk instead,
# keeping every chunk in play.


def _long_case() -> FrozenRetrievalCase:
    return _case(
        "mh-budget",
        (
            _document("甲" * 400 + " 9999", "chunk-long"),
            _document("乙" * 50, "chunk-short"),
        ),
    )


def test_no_budget_leaves_content_untouched():
    """The default must preserve historical runs' meaning exactly."""
    pack = build_evidence_pack(_long_case(), max_items=5)
    assert len(pack.items[0].page_content) == 405
    assert "context_truncated" not in pack.items[0].metadata


def test_budget_caps_each_item():
    pack = build_evidence_pack(_long_case(), max_items=5, max_chars_per_item=100)
    assert len(pack.items[0].page_content) == 100
    # A chunk already under the budget is left alone, not padded.
    assert len(pack.items[1].page_content) == 50


def test_budget_records_what_it_cut():
    """Truncation has to be visible, or a shortened chunk reads as the original."""
    pack = build_evidence_pack(_long_case(), max_items=5, max_chars_per_item=100)
    metadata = pack.items[0].metadata
    assert metadata.get("context_truncated") is True
    assert metadata.get("context_original_chars") == 405


def test_budget_shrinks_the_rendered_context():
    full = build_evidence_pack(_long_case(), max_items=5).context_text()
    cut = build_evidence_pack(_long_case(), max_items=5, max_chars_per_item=100).context_text()
    assert len(cut) < len(full)


def test_audit_sees_the_same_truncated_text_as_the_model():
    """The cut must happen on the evidence, not only on the rendered prompt.

    Truncating at render time would leave the auditor treating text the model
    never saw as supporting evidence, so a number lifted from the unseen tail
    would be scored as grounded.  Cutting at pack construction keeps the two
    views identical -- which is what this test pins.
    """
    answer = "数值是 9999。[L1]"
    untruncated = soft_audit(answer, build_evidence_pack(_long_case(), max_items=5))
    assert untruncated["unsupported_number_count"] == 0, "9999 在原 chunk 里，应视为有出处"

    truncated = soft_audit(
        answer, build_evidence_pack(_long_case(), max_items=5, max_chars_per_item=100)
    )
    assert truncated["unsupported_number_count"] == 1, (
        "9999 落在被截断的尾部，模型看不到它；审计若仍算它有出处，"
        "就等于把模型不可能知道的数字判成有依据"
    )


# --- the generated artefacts ---------------------------------------------


def test_generated_snapshot_and_contract_are_present():
    assert MULTIHOP_SNAPSHOT.exists(), "复合快照未生成；运行 scripts/build_multihop_snapshot.py"
    assert MULTIHOP_CONTRACT.exists(), "多跳契约未生成"
    assert MULTIHOP_SPECS.exists(), "spec 未提供"


def test_every_generated_hop_is_verifiable_from_its_declared_chunk():
    """The anti-fabrication property must hold for the shipped snapshot.

    The builder asserts this at generation time; this test asserts it of the
    file that is actually on disk, so a hand-edited snapshot cannot slip a
    hop past the guard.
    """
    cases = load_cases(MULTIHOP_SNAPSHOT)
    contract = json.loads(MULTIHOP_CONTRACT.read_text(encoding="utf-8"))

    def normalise(text: str) -> str:
        import re

        return re.sub(r"\s+", "", str(text or ""))

    for spec in contract["cases"]:
        case = cases[spec["id"]]
        by_chunk = {document.chunk_id: document for document in case.documents}
        for hop in spec["required_hops"]:
            document = by_chunk.get(hop["source_chunk_id"])
            assert document is not None, (
                f"{spec['id']}/{hop['hop_id']}: 声明的 chunk 不在快照里"
            )
            assert normalise(hop["expected_span"]) in normalise(document.page_content), (
                f"{spec['id']}/{hop['hop_id']}: 期望片段不是该 chunk 的原文子串"
            )
            for group in hop["required_terms"]:
                alternatives = [group] if isinstance(group, str) else list(group)
                assert any(
                    normalise(item) in normalise(document.page_content)
                    for item in alternatives
                ), f"{spec['id']}/{hop['hop_id']}: 必需词组 {group!r} 在该 chunk 中找不到"


def test_generated_cases_are_marked_as_composites():
    """A composite case must never be mistakable for a retrieved one."""
    cases = load_cases(MULTIHOP_SNAPSHOT)
    assert cases, "复合快照为空"
    for question_id, case in cases.items():
        profile = case.retrieval_profile
        assert profile.get("composite") is True, f"{question_id} 未标记为 composite"
        assert profile.get("composite_source_cases"), f"{question_id} 缺少来源题溯源"
        assert profile.get("composite_hops"), f"{question_id} 缺少逐跳溯源"
        # Nothing was retrieved, so no timing may be claimed.
        assert case.retrieval_timing.get("composite") is True


def test_every_composite_question_loses_a_hop_to_the_default_truncation():
    """The two-arm experiment only means something if truncation bites.

    ``build_evidence_pack`` keeps 5 items by default.  If every hop's evidence
    sat within the first 5, raising the cap could not change any answer and the
    comparison would be vacuous.  Each question must therefore have at least
    one hop whose evidence falls past the truncation point.
    """
    cases = load_cases(MULTIHOP_SNAPSHOT)
    contract = json.loads(MULTIHOP_CONTRACT.read_text(encoding="utf-8"))
    for spec in contract["cases"]:
        case = cases[spec["id"]]
        positions = {document.chunk_id: index + 1 for index, document in enumerate(case.documents)}
        beyond = [
            hop for hop in spec["required_hops"]
            if positions[hop["source_chunk_id"]] > 5
        ]
        assert beyond, (
            f"{spec['id']}: 所有跳的证据都在默认截断点内，放宽证据上限不会产生差异，"
            "该题无法用于对照实验"
        )


def test_multihop_slices_declare_the_new_case_type():
    from src.generation_slices import CASE_TYPE_VALUES, load_slices

    assert "multi_hop" in CASE_TYPE_VALUES
    payload = load_slices(ROOT / "data" / "generation_eval_slices.multihop.v1.json")
    assert "multi_hop" in payload["case_type_values"]
    assert "multi_hop" in payload["metric_groups"]
    assert payload["metric_groups"]["multi_hop"]["metrics"] == ["hop_recall_mean"]


def test_existing_slice_file_was_synced_with_the_new_case_type():
    """``load_slices`` requires the declared vocabulary to match exactly.

    Adding ``multi_hop`` in code without updating the existing slice file would
    make every run of the original 12 questions fail to load.
    """
    from src.generation_slices import load_slices

    payload = load_slices(ROOT / "data" / "generation_eval_slices.v1.json")
    assert "multi_hop" in payload["case_type_values"]
    assert len(payload["slices"]) == 12


# --- the multi_hop gate must be able to fail ------------------------------


def _multi_hop_groups():
    payload = json.loads(
        (ROOT / "data" / "generation_eval_slices.multihop.v1.json").read_text(encoding="utf-8")
    )
    return payload["metric_groups"]


def test_multi_hop_gate_fires_when_hop_recall_moves_without_declaration():
    """A gate that cannot fail is indistinguishable from one that passed.

    ``hop_recall_mean`` has to be wired into the gate's metric list, or the
    multi_hop group would report "no change" for every run and nobody would
    notice.  An undeclared group may not move at all -- an improvement counts
    as a breach too, because it means the change was not scoped.
    """
    from src.generation_gates import evaluate_group_gates

    result = evaluate_group_gates(
        _multi_hop_groups(),
        {"hop_recall_mean": {"baseline": 0.417, "current": 0.75, "delta": 0.333}},
        declared_changes=[],
    )
    assert result["all_groups_passed"] is False
    assert result["groups"]["multi_hop"]["passed"] is False
    breach = result["groups"]["multi_hop"]["metrics"][0]
    assert breach["breach_reason"] == "out_of_scope"


def test_multi_hop_gate_passes_inside_tolerance_when_declared():
    from src.generation_gates import evaluate_group_gates

    result = evaluate_group_gates(
        _multi_hop_groups(),
        {"hop_recall_mean": {"baseline": 0.417, "current": 0.467, "delta": 0.05}},
        declared_changes=["multi_hop"],
    )
    assert result["all_groups_passed"] is True


def test_hop_recall_mean_is_in_the_comparability_keys():
    """Repeated runs must be checked for hop variance like any other metric."""
    from src.gates_runner import MULTIHOP_METRICS

    assert "hop_recall_mean" in MULTIHOP_METRICS


# --- a metric that moves with its denominator must not be gated -----------


def test_citation_usage_ratio_is_not_gated():
    """It is "used / allowed", and "allowed" is the evidence count.

    So the ratio falls whenever more evidence is supplied, for reasons that have
    nothing to do with citation quality: measured across arms of one experiment
    it went 0.767 -> 0.307 purely because the cap rose from 5 to 9-20 items.
    Gating it reports a breach on every comparison that changes the evidence
    volume, which is exactly the false alarm that trains people to ignore gates.
    """
    from src.generation_gates import evaluate_group_gates

    for path in (
        ROOT / "data" / "generation_eval_slices.v1.json",
        ROOT / "data" / "generation_eval_slices.multihop.v1.json",
    ):
        groups = json.loads(path.read_text(encoding="utf-8"))["metric_groups"]
        citation = groups["citation"]
        assert "citation_id_usage_ratio_mean" not in citation["metrics"], (
            f"{path.name}: 该指标跨证据条数不可比，不能进闸门"
        )

        # And prove the gate really ignores it: a large move must not breach.
        result = evaluate_group_gates(
            groups,
            {"citation_id_usage_ratio_mean": {"baseline": 0.767, "current": 0.307, "delta": -0.46}},
            declared_changes=[],
        )
        assert result["groups"]["citation"]["passed"] is True, (
            f"{path.name}: 分母效应仍被判成违规"
        )


def test_gated_diagnostics_are_explained():
    """A metric kept out of the gate must say why, or it looks like an oversight."""
    for path in (
        ROOT / "data" / "generation_eval_slices.v1.json",
        ROOT / "data" / "generation_eval_slices.multihop.v1.json",
    ):
        groups = json.loads(path.read_text(encoding="utf-8"))["metric_groups"]
        for name, definition in groups.items():
            for item in definition.get("diagnostics") or []:
                assert item.get("metric"), f"{path.name}/{name}: diagnostics 缺少 metric"
                assert len(str(item.get("reason") or "")) > 20, (
                    f"{path.name}/{name}/{item.get('metric')}: diagnostics 缺少理由说明"
                )


def test_the_usage_ratio_is_still_measured():
    """Not gated does not mean not measured -- it stays a reported diagnostic."""
    from src.gates_runner import CITATION_METRICS

    # It is still aggregated; it just is not compared against a tolerance.
    assert "citation_id_usage_ratio_mean" in CITATION_METRICS


# --- the builder's guard --------------------------------------------------


def test_builder_rejects_a_hop_whose_span_is_not_in_its_chunk():
    """A hop must prove itself against the chunk it names."""
    import scripts.build_multihop_snapshot as builder

    documents = (_document("这里只有别的内容。", "chunk-x"),)
    cases = {"q01_hit": _case("q01_hit", documents)}
    hop = {
        "hop_id": "h1",
        "from_question": "q01_hit",
        "chunk_id": "chunk-x",
        "expected_span": "这段文字不在原文里",
        "required_terms": [["也不在"]],
    }
    with pytest.raises(SystemExit, match="原文子串"):
        builder._resolve_hop("mh-x", hop, cases)


def test_builder_rejects_a_hop_whose_terms_are_absent():
    """A hop whose terms cannot be found is not answerable from that chunk."""
    import scripts.build_multihop_snapshot as builder

    documents = (_document("桌面高 680~760。", "chunk-y"),)
    cases = {"q01_hit": _case("q01_hit", documents)}
    hop = {
        "hop_id": "h1",
        "from_question": "q01_hit",
        "chunk_id": "chunk-y",
        "expected_span": "680~760",
        "required_terms": [["完全不存在的词组"]],
    }
    with pytest.raises(SystemExit, match="不可答"):
        builder._resolve_hop("mh-y", hop, cases)


def test_builder_accepts_a_span_split_by_table_layout():
    """Extracted tables separate cells with newlines.

    A literal substring test would reject spans a human reads as present, so
    the guard normalises whitespace before comparing.
    """
    import scripts.build_multihop_snapshot as builder

    documents = (_document("坐高\n758\n780\n791\n", "chunk-z"),)
    cases = {"q01_hit": _case("q01_hit", documents)}
    hop = {
        "hop_id": "h1",
        "from_question": "q01_hit",
        "chunk_id": "chunk-z",
        "expected_span": "坐高 758",
        "required_terms": [["坐高"], ["758"]],
    }
    document, _ = builder._resolve_hop("mh-z", hop, cases)
    assert document.chunk_id == "chunk-z"
