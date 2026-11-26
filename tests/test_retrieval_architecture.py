"""Tests for the slimmed modular RAG architecture and its invariants."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langchain_core.documents import Document

import src.retriever as retriever
from src.calculation import parse_cylinder_capacity_question
from src.retrieval.bilingual_glossary import expand_bilingual_terms
from src.retrieval import (
    build_bm25_queries,
    domain_rules_enabled,
    retrieve_documents,
    retrieve_documents_multi,
    select_rerank_pool,
)
from src.retrieval.ranking import _apply_combined_ranking
from src.services import DefaultServices, EvaluationPolicy
from src.tools import ToolRegistry
from src.tools.builtin import register_builtin_tools


def test_retriever_facade_reexports_modular_retrieval_api():
    assert retriever.retrieve_documents is retrieve_documents
    assert retriever.retrieve_documents_multi is retrieve_documents_multi
    assert retriever.build_bm25_queries is build_bm25_queries
    assert retriever.select_rerank_pool is select_rerank_pool
    assert retriever.domain_rules_enabled is domain_rules_enabled


def test_domain_rules_can_be_disabled_for_generic_corpora(monkeypatch):
    monkeypatch.setattr(
        retriever,
        "settings",
        SimpleNamespace(
            retriever_domain_rules_enabled=False,
            retriever_bm25_mode="conditional",
            retriever_max_entity_bm25_queries=3,
            retriever_ranking_mode="reranker_entity_rrf",
            retriever_rerank_top_k=20,
            retriever_rerank_weight=0.82,
            retriever_low_authority_prior=True,
            retriever_low_authority_penalty=0.12,
        ),
    )

    lanes = build_bm25_queries("PP 和 Tritan 儿童水杯材料对比 GB 4806.7-2023")
    assert len(lanes) == 1
    assert lanes[0]["kind"] == "original"

    docs = [
        Document(
            page_content="CNC milling minimum wall thickness 0.51mm",
            metadata={"source": "cnc.md", "chunk_id": "c1", "rerank_score": 0.9, "rrf_score": 0.02},
        ),
        Document(
            page_content="PP 3486-01 TDS",
            metadata={"source": "PP_3486-01_TDS.pdf", "chunk_id": "p1", "rerank_score": 0.2, "rrf_score": 0.01},
        ),
    ]
    ranked = _apply_combined_ranking("PP 和 Tritan 儿童水杯", docs, True)
    assert ranked[0].metadata["chunk_id"] == "c1"
    assert ranked[0].metadata["query_entities"] == []
    assert ranked[0].metadata["process_prior_reason"] == "domain_rules_disabled"
    assert ranked[0].metadata["anthropometry_prior_reason"] == "domain_rules_disabled"


def test_select_rerank_pool_bounds_candidates_without_mutating_union_flags():
    docs = [
        Document(
            page_content=f"chunk {i}",
            metadata={
                "source": f"doc_{i}.md",
                "chunk_id": f"c{i}",
                "dense_rank": i + 1,
                "union_truncated": False,
            },
        )
        for i in range(60)
    ]
    pool = select_rerank_pool(docs, [{"query": "test", "kind": "original"}], rerank_input_cap=25)
    assert len(pool) == 25
    assert [d.metadata["chunk_id"] for d in pool] == [f"c{i}" for i in range(25)]
    assert all(d.metadata.get("union_truncated") is False for d in docs)

    unlimited = select_rerank_pool(docs, [{"query": "test", "kind": "original"}], rerank_input_cap=0)
    assert len(unlimited) == 60


def test_execute_subtasks_enforces_single_layer_decomposition(monkeypatch):
    observed: list[bool | None] = []

    async def fake_multi(question: str, total_timeout_seconds=None, query_decompose_enabled=None):
        observed.append(query_decompose_enabled)
        return [
            Document(
                page_content="evidence",
                metadata={"source": "guide.md", "chunk_id": "g1", "retrieval_evidence_status": "direct"},
            )
        ]

    monkeypatch.setattr("src.tools.builtin.retrieve_documents_multi", fake_multi)
    services = DefaultServices(
        policy=EvaluationPolicy(
            allow_web=False,
            query_decompose_enabled=True,
            max_retrieval_tasks=2,
            shared_retrieval_budget_seconds=10.0,
        )
    )

    async def fake_grade(question, docs):
        return docs

    monkeypatch.setattr(services, "grade", fake_grade)

    tasks = [
        {"id": "task-1", "operation": "retrieve", "query": "子任务一", "dependencies": []},
        {"id": "task-2", "operation": "retrieve", "query": "子任务二", "dependencies": []},
    ]
    results = asyncio.run(services.execute_subtasks(tasks))
    assert len(results) == 2
    assert all(item["status"] == "completed" for item in results)
    assert observed == [False, False]

    # Direct single-pass retrieval still respects the configured default.
    registry = ToolRegistry()
    register_builtin_tools(registry, query_decompose_enabled=True)
    res = asyncio.run(registry.call("local_retrieval", question="单路检索"))
    assert res.success is True
    assert observed[-1] is True


def test_parse_cylinder_capacity_question_extracts_dimensions():
    assert parse_cylinder_capacity_question("直筒杯内径65 mm、有效液高120 mm，估算理想容量") == (
        "65",
        "120",
    )
    assert parse_cylinder_capacity_question("钣金件最小折弯半径是多少？") is None


def test_bilingual_glossary_bridges_chinese_dfm_queries_to_english_terms():
    tokens = expand_bilingual_terms("PC注塑件壁厚和垂直面脱模斜度怎么选？")
    assert "polycarbonate" in tokens
    assert "wall" in tokens
    assert "thickness" in tokens
    assert "draft" in tokens

    sm_tokens = expand_bilingual_terms("1.5mm厚的不锈钢板，折弯处离孔最近能留多少距离？")
    assert "stainless" in sm_tokens
    assert "bend" in sm_tokens
    assert "hole" in sm_tokens
