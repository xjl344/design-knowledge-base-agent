"""Tests for the real-question multi-hop set.

The synthetic set is built by pooling frozen chunks into questions written to
fit the evidence.  That makes the evidence trivially aligned with the question
and is the main external-validity gap: nothing proves the generator handles
questions a person actually asked.

This set takes the questions from `data/test_qa_35_complex.json` -- real
complex questions authored independently of the frozen snapshot -- and keeps
only those whose evidence is actually present.  What has to hold is that the
questions were not quietly edited to fit, and that nothing was dropped without
saying so.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SPEC_PATH = ROOT / "data" / "multihop_specs.real.v1.json"
SOURCE_QUESTIONS = ROOT / "data" / "test_qa_35_complex.json"
CONTRACT_PATH = ROOT / "data" / "generation_eval.multihop.real.v1.json"
SNAPSHOT_PATH = ROOT / "data" / "frozen_multihop_real_cases.jsonl"


@pytest.fixture(scope="module")
def spec():
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def source_questions():
    payload = json.loads(SOURCE_QUESTIONS.read_text(encoding="utf-8"))
    return {str(q["id"]): q for q in payload["questions"]}


def test_the_questions_are_taken_verbatim_from_the_source(spec, source_questions):
    """Rewording a question to fit the evidence would defeat the point.

    The value of this set is that the questions were written without knowledge
    of the frozen snapshot, so a hop the evidence cannot support is a real
    finding rather than something edited away.
    """
    for case in spec["cases"]:
        source_id = str(case["id"]).split("_", 1)[0].lstrip("r").rjust(2, "0")
        source = source_questions.get(f"c{source_id}")
        assert source is not None, f"{case['id']} 找不到对应的源题"
        assert case["question"] == source["question"], (
            f"{case['id']} 的问题与源题 {source['id']} 不一致；不得改写"
        )


def test_every_excluded_question_is_named_with_a_reason(spec, source_questions):
    """Silently dropping questions would make the set look more complete than it is."""
    kept = {str(case["id"]).split("_", 1)[0].lstrip("r").rjust(2, "0") for case in spec["cases"]}
    kept = {f"c{value}" for value in kept}
    excluded = set(spec["provenance"]["excluded"]["ids"])
    assert kept.isdisjoint(excluded)
    assert kept | excluded == set(source_questions), (
        "每道源题要么入选、要么被显式排除；不能有沉默的遗漏"
    )


def test_the_real_set_is_smaller_than_the_source_and_says_so(spec, source_questions):
    """The headline fact: the frozen snapshot supports only a slice of real questions."""
    assert len(spec["cases"]) < len(source_questions)
    assert len(spec["cases"]) == 5


def test_each_case_has_at_least_two_hops(spec):
    """A single-hop question would not exercise multi-hop coverage at all."""
    for case in spec["cases"]:
        assert len(case["hops"]) >= 2, f"{case['id']} 少于两跳"


def test_hops_come_from_the_declared_sources(spec):
    """Evidence must be traceable to a declared source, or provenance is fake."""
    for case in spec["cases"]:
        sources = {str(item) for item in case["sources"]}
        for hop in case["hops"]:
            assert str(hop["from_question"]) in sources, (
                f"{case['id']}/{hop['hop_id']} 的来源题不在 sources 里"
            )


def test_the_built_contract_carries_the_hops():
    """The spec is only useful if the built contract exposes it to the scorer."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["family"] == "multihop.v1"
    ids = {str(case["id"]) for case in contract["cases"]}
    spec_ids = {str(case["id"]) for case in json.loads(SPEC_PATH.read_text(encoding="utf-8"))["cases"]}
    assert ids == spec_ids
    for case in contract["cases"]:
        assert len(case["required_hops"]) >= 2


def test_at_least_one_hop_falls_past_the_default_truncation_point():
    """Otherwise raising the evidence cap could not change anything.

    The composite is assembled in hop order, so a hop whose evidence sits past
    the default cap is exactly what the two-arm experiment needs.
    """
    from src.frozen_evidence import build_evidence_pack, load_cases

    cases = load_cases(SNAPSHOT_PATH)
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    truncated_somewhere = False
    for case in contract["cases"]:
        hops = case["required_hops"]
        pooled = cases[str(case["id"])]
        order = [
            document.chunk_id or document.content_hash or document.document_id
            for document in pooled.documents
        ]
        kept = set(order[:5])
        if any(str(hop["source_chunk_id"]) not in kept for hop in hops):
            truncated_somewhere = True
    assert truncated_somewhere, "没有一跳落在默认截断点之后，两臂对照会是空的"


def test_the_real_snapshot_pools_only_frozen_chunks():
    """No retrieval happens: every chunk must already exist in the frozen snapshot."""
    from src.frozen_evidence import load_cases

    frozen = load_cases(ROOT / "data" / "frozen_retrieval_cases.jsonl")
    available = {
        document.chunk_id
        for case in frozen.values()
        for document in case.documents
    }
    real = load_cases(SNAPSHOT_PATH)
    for case in real.values():
        for document in case.documents:
            assert document.chunk_id in available, (
                f"{case.question_id} 用了不在冻结快照里的 chunk，等于绕过冻结"
            )
