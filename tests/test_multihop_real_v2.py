"""Tests for the v2 real-question set: hop-level admission of partial questions.

v1 kept only the 5 real questions whose evidence is entirely frozen.  That is
honest but too small to measure anything: 5 questions, and provider timeouts
cost some of those.  v2 admits 7 more questions at *hop level* -- a question
joins with only the hops its frozen evidence can prove, labelled partial, with
the uncovered needs named.

The risk this file exists to contain: a partial question is scored on a subset
of its own needs, so it is easier than a complete one.  If the two were pooled
into one mean, the set's difficulty would drift with the admission ratio -- the
same trap as a metric whose denominator moves with the evidence count.  So the
two groups are built into separate contracts, and these tests hold that line.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SPEC_V1 = ROOT / "data" / "multihop_specs.real.v1.json"
SPEC_V2 = ROOT / "data" / "multihop_specs.real.v2.json"
SOURCE_QUESTIONS = ROOT / "data" / "test_qa_35_complex.json"
FROZEN_SNAPSHOT = ROOT / "data" / "frozen_retrieval_cases.jsonl"

GROUPS = {
    "complete": {
        "snapshot": ROOT / "data" / "frozen_multihop_real_complete.v2.jsonl",
        "contract": ROOT / "data" / "generation_eval.multihop.real.complete.v2.json",
        "slices": ROOT / "data" / "generation_eval_slices.multihop.real.complete.v2.json",
        "family": "multihop.real.complete.v2",
    },
    "partial": {
        "snapshot": ROOT / "data" / "frozen_multihop_real_partial.v2.jsonl",
        "contract": ROOT / "data" / "generation_eval.multihop.real.partial.v2.json",
        "slices": ROOT / "data" / "generation_eval_slices.multihop.real.partial.v2.json",
        "family": "multihop.real.partial.v2",
    },
}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def spec() -> dict:
    return load(SPEC_V2)


@pytest.fixture(scope="module")
def source_questions() -> dict[str, dict]:
    return {
        str(item["id"]): item
        for item in load(SOURCE_QUESTIONS)["questions"]
    }


def source_id_of(case_id: str) -> str:
    """``r03_office_chair_constraints`` -> ``c03``; ``p01_x`` keeps its own map."""
    head = case_id.split("_", 1)[0]
    if head.startswith("r"):
        return f"c{head.lstrip('r').rjust(2, '0')}"
    return head


def test_every_question_text_is_verbatim(spec, source_questions):
    """Rewording a question to fit the evidence is what this set guards against."""
    for case in spec["cases"]:
        source_id = (
            source_id_of(case["id"])
            if case["coverage"] == "complete"
            else next(
                key for key, value in source_questions.items()
                if value["question"] == case["question"]
            )
        )
        assert case["question"] == source_questions[source_id]["question"], (
            f"{case['id']} 的问题与源题 {source_id} 不一致；不得改写"
        )


def test_the_three_groups_partition_the_source(spec, source_questions):
    """Nothing may be dropped silently, and no question may appear twice."""
    complete = {source_id_of(case["id"]) for case in spec["cases"] if case["coverage"] == "complete"}
    partial = {
        next(key for key, value in source_questions.items() if value["question"] == case["question"])
        for case in spec["cases"] if case["coverage"] == "partial"
    }
    excluded = set(spec["provenance"]["excluded"]["ids"])
    assert complete.isdisjoint(partial)
    assert complete.isdisjoint(excluded)
    assert partial.isdisjoint(excluded)
    assert complete | partial | excluded == set(source_questions), (
        "每道源题必须落在 complete / partial / excluded 之一，且只落一处"
    )


def test_every_exclusion_states_its_own_reason(spec):
    """One blanket reason would hide that the exclusions differ in kind."""
    per_id = spec["provenance"]["excluded"]["per_id"]
    ids = spec["provenance"]["excluded"]["ids"]
    assert set(per_id) == set(ids)
    for question_id, reason in per_id.items():
        assert reason and reason.strip(), f"{question_id} 的排除理由为空"
    assert len(set(per_id.values())) > 1, (
        "所有排除理由都一样，说明没有区分「来源全缺」「来源在但内容为空」「来源不足两跳」"
    )


def test_partial_cases_name_what_they_cannot_cover(spec):
    """A partial label without the gap named is indistinguishable from a claim."""
    for case in spec["cases"]:
        if case["coverage"] != "partial":
            continue
        needs = case.get("unsupported_needs")
        assert needs, f"{case['id']} 标为 partial 却没有列出未覆盖的需求"
        assert all(str(item).strip() for item in needs)


def test_partial_cases_still_have_at_least_two_hops(spec):
    for case in spec["cases"]:
        assert len(case["hops"]) >= 2, f"{case['id']} 少于两跳"


def test_hops_trace_to_declared_sources(spec):
    for case in spec["cases"]:
        sources = {str(item) for item in case["sources"]}
        for hop in case["hops"]:
            assert str(hop["from_question"]) in sources, (
                f"{case['id']}/{hop['hop_id']} 的来源题不在 sources 里"
            )


def test_every_case_declares_its_coverage(spec):
    """The default is complete, so silence would quietly mean complete."""
    for case in spec["cases"]:
        assert case.get("coverage") in ("complete", "partial"), (
            f"{case['id']} 没有声明 coverage"
        )


def test_the_complete_group_is_the_same_five_questions_as_v1():
    """The headline comparison must stay about the same questions."""
    v1 = {str(case["id"]) for case in load(SPEC_V1)["cases"]}
    v2 = {
        str(case["id"])
        for case in load(SPEC_V2)["cases"]
        if case["coverage"] == "complete"
    }
    assert v1 == v2


def test_the_two_groups_are_separate_contracts():
    """Pooling them would make difficulty drift with the admission ratio."""
    complete = load(GROUPS["complete"]["contract"])
    partial = load(GROUPS["partial"]["contract"])
    assert complete["family"] != partial["family"]
    assert complete["family"] != "multihop.v1"
    assert partial["family"] != "multihop.v1"
    assert complete["version"] == 2 and partial["version"] == 2
    assert {case["coverage"] for case in complete["cases"]} == {"complete"}
    assert {case["coverage"] for case in partial["cases"]} == {"partial"}
    complete_ids = {case["id"] for case in complete["cases"]}
    partial_ids = {case["id"] for case in partial["cases"]}
    assert complete_ids.isdisjoint(partial_ids)


def test_partial_contract_records_the_uncovered_needs():
    """The contract is what a run is scored against, so the gap must be in it."""
    partial = load(GROUPS["partial"]["contract"])
    for case in partial["cases"]:
        assert case["unsupported_needs"], f"{case['id']} 的契约里没有记录未覆盖需求"
    complete = load(GROUPS["complete"]["contract"])
    for case in complete["cases"]:
        assert "unsupported_needs" not in case


def test_both_contracts_point_at_their_own_spec():
    """v1's real contract claimed to be generated from the synthetic spec."""
    for group in GROUPS.values():
        contract = load(group["contract"])
        assert contract["generated_from"] == "data/multihop_specs.real.v2.json", (
            f"{group['contract'].name} 的 generated_from 与自身输入不符"
        )


@pytest.mark.parametrize("coverage", ["complete", "partial"])
def test_the_slice_files_load(coverage):
    """v1's real slices carried empty slice_tags and could not be loaded at all.

    Anything that groups by slice -- the grouped gates included -- raised
    SliceError on the real set, so no grouped gate could ever run on it.
    """
    from src.generation_slices import load_slices

    loaded = load_slices(GROUPS[coverage]["slices"])
    assert loaded["version"] == GROUPS[coverage]["family"]
    for entry in loaded["slices"]:
        assert entry["slice_tags"], f"{entry['id']} 的 slice_tags 为空"


def test_partial_slices_are_tagged():
    from src.generation_slices import load_slices

    loaded = load_slices(GROUPS["partial"]["slices"])
    for entry in loaded["slices"]:
        assert "partial_coverage" in entry["slice_tags"]
        assert entry["coverage"] == "partial"


@pytest.mark.parametrize("coverage", ["complete", "partial"])
def test_the_v2_snapshots_pool_only_frozen_chunks(coverage):
    """No retrieval happens: every chunk must already exist in the frozen snapshot."""
    from src.frozen_evidence import load_cases

    frozen = load_cases(FROZEN_SNAPSHOT)
    available = {
        document.chunk_id
        for case in frozen.values()
        for document in case.documents
    }
    for case in load_cases(GROUPS[coverage]["snapshot"]).values():
        for document in case.documents:
            assert document.chunk_id in available, (
                f"{case.question_id} 用了不在冻结快照里的 chunk，等于绕过冻结"
            )


@pytest.mark.parametrize("coverage", ["complete", "partial"])
def test_the_committed_artifacts_match_the_builder(coverage):
    """A stale snapshot or contract would be scored as if it were current."""
    import scripts.build_multihop_snapshot as builder
    from src.frozen_evidence import load_cases

    specs_payload = builder.load_specs(SPEC_V2)
    specs = builder.select_cases(specs_payload["cases"], coverage)
    cases = load_cases(FROZEN_SNAPSHOT)

    composites = []
    hop_rows_by_id = {}
    for case_spec in specs:
        composite, hop_rows = builder.build_composite(
            case_spec, cases, slim=True
        )
        composites.append(composite)
        hop_rows_by_id[str(case_spec["id"])] = hop_rows

    committed = load_cases(GROUPS[coverage]["snapshot"])
    assert [case.as_dict() for case in composites] == [
        committed[case.question_id].as_dict() for case in composites
    ]

    contract = builder.build_contract(
        specs,
        hop_rows_by_id,
        family=GROUPS[coverage]["family"],
        version=2,
        generated_from="data/multihop_specs.real.v2.json",
    )
    assert contract == load(GROUPS[coverage]["contract"])
    assert builder.build_slices(specs, version=GROUPS[coverage]["family"]) == load(
        GROUPS[coverage]["slices"]
    )


@pytest.mark.parametrize("coverage", ["complete", "partial"])
def test_the_v2_snapshots_carry_no_source_retrieval_diagnostics(coverage):
    """Each pooled chunk used to carry ~178,000 characters of the *source*
    question's retrieval audit -- about 250x its own text, and untrue of a case
    that was never retrieved.  Dropping it shrank the snapshot 42x, and was
    verified not to change the evidence pack or the audit.
    """
    import scripts.build_multihop_snapshot as builder
    from src.frozen_evidence import load_cases

    for case in load_cases(GROUPS[coverage]["snapshot"]).values():
        for document in case.documents:
            present = [
                key for key in builder.HEAVY_RETRIEVAL_DIAGNOSTICS
                if key in document.metadata
            ]
            assert not present, f"{case.question_id} 仍带着源题检索诊断：{present}"
            assert document.metadata.get("retrieval_diagnostics_dropped") is None
        assert case.retrieval_profile.get("retrieval_diagnostics_dropped"), (
            "瘦身没有记录在案，读者无从知道诊断是被去掉还是本来就没有"
        )


def test_the_spec_generator_is_reproducible():
    """The admitted hops are the subjective part, so their derivation is code."""
    import scripts.build_real_specs_v2 as generator

    assert generator.build_spec() == load(SPEC_V2)


def test_every_partial_case_has_a_hop_beyond_the_default_cap():
    """Otherwise the two arms could not differ on that question at all."""
    from src.frozen_evidence import load_cases

    cases = load_cases(GROUPS["partial"]["snapshot"])
    contract = load(GROUPS["partial"]["contract"])
    for case in contract["cases"]:
        pooled = cases[str(case["id"])]
        order = [
            document.chunk_id or document.content_hash or document.document_id
            for document in pooled.documents
        ]
        kept = set(order[:5])
        assert any(str(hop["source_chunk_id"]) not in kept for hop in case["required_hops"]), (
            f"{case['id']} 的所有跳都在默认上限内，两臂在该题上必然相同"
        )


# ---------------------------------------------------------------------------
# Spans that only constrained wording were demoted to terms.
# ---------------------------------------------------------------------------


def test_wording_only_spans_are_demoted_to_terms():
    """A hop whose fact is about phrasing must not be judged on a sentence.

    The audit read the answers behind every failed hop and found five where the
    fact was delivered and the declared span was not (`检查高度是否导致重心过高`
    against the answer's `应检查重心是否过高`).  v7 fixed the mechanical damage
    to those spans and the scores did not move: the gap was wording.  Demoting
    them keeps one mechanism -- every hop is judged on terms.
    """
    from scripts.build_multihop_snapshot import SPAN_DEMOTIONS

    contract = load(GROUPS["complete"]["contract"])
    # A list, not a dict keyed by hop_id: hop ids repeat across cases, and
    # keying by them silently collapses ten hops into two.
    all_hops = [
        hop for case in contract["cases"] for hop in case["required_hops"]
    ]
    demoted = [hop for hop in all_hops if not hop.get("expected_span")]
    # Expected count derived from the spec, so adding a demotion to
    # SPAN_DEMOTIONS without the span being present is not silently ignored.
    spec = load(SPEC_V2)
    expected = sum(
        1
        for case in spec["cases"]
        if case.get("coverage") == "complete"
        for hop in (case.get("hops") or [])
        if hop.get("expected_span") in SPAN_DEMOTIONS
    )
    assert len(demoted) == expected, (
        f"降级的跳数应为 {expected}，实际 {len(demoted)}；"
        "多一个或少一个都意味着有跳被悄悄改了计分方式"
    )
    for hop in demoted:
        # Terms must be strengthened past the single topic word, because the
        # span is no longer carrying the substance check.
        assert len(hop["required_terms"]) >= 2, hop
        assert hop.get("span_demoted_because"), hop


def test_a_demoted_hop_is_judged_on_terms_not_on_an_empty_span():
    """No span at all, rather than an empty one.

    An empty `expected_span` would be scored as a failed span and the hop would
    never be credited -- the opposite of the intent.
    """
    from src.frozen_evidence import AUDIT_VERSION, build_evidence_pack, load_cases, soft_audit

    assert AUDIT_VERSION == "soft-audit-behaviour-v8"
    cases = load_cases(GROUPS["complete"]["snapshot"])
    contract = load(GROUPS["complete"]["contract"])
    case = contract["cases"][3]  # r18: both hops demoted
    pooled = cases[str(case["id"])]
    pack = build_evidence_pack(pooled, max_items=99, max_chars_per_item=None)
    answer = "应在杯架允许的外径范围内确定内径，再反推 h；并检查重心是否过高。"
    audit = soft_audit(
        answer,
        pack,
        expected_answer_spans=case["expected_answer_spans"],
        required_hops=case["required_hops"],
    )
    for hop in audit["hop_results"]:
        assert hop["expected_span"] == ""
        assert hop["span_matched"] is None, "无跨段时不应判为跨度失败"
        assert hop["matched"] is True, hop


def test_the_partial_set_is_demoted_by_the_same_rule():
    """The partial set's 0.127 was the same measurement artifact as the complete set's.

    Its audit found 37 of 55 failures were "terms matched, span rejected", and
    every span was a whole prose sentence.  All 21 hops are demoted now; this
    pins that, because a partial question's low score is easy to accept as
    "these are the harder questions" -- which is what happened for a long time.
    """
    from scripts.build_multihop_snapshot import SPAN_DEMOTIONS

    contract = load(GROUPS["partial"]["contract"])
    all_hops = [hop for case in contract["cases"] for hop in case["required_hops"]]
    assert all_hops, "partial 契约里没有跳"
    demoted = [hop for hop in all_hops if not hop.get("expected_span")]
    assert len(demoted) == len(all_hops), (
        f"partial 的 {len(all_hops)} 个跳应全部降级，实际 {len(demoted)}"
    )
    for hop in demoted:
        assert hop.get("span_demoted_because"), hop
        # Every demoted term set must come from the shared table, so a hop
        # cannot quietly get a bespoke (weaker) rule of its own.
        assert hop["required_terms"] in SPAN_DEMOTIONS.values(), hop
