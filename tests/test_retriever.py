from langchain_core.documents import Document

from src.retriever import (
    _format_retrieval_error,
    _lexical_terms,
    _merge_hybrid_documents,
    _rrf_fusion,
    _select_reranked,
    _annotate_union,
    _extract_query_entities,
    _entity_match_score,
    _apply_combined_ranking,
    _canonical_entity_id,
    _canonical_entity_ids_from_text,
    _source_role,
    _is_low_authority_document,
    _entity_candidate_priority,
    _process_prior_signal,
    _anthropometry_prior_signal,
    build_bm25_queries,
    _limit_union_candidates,
    confidence_from_documents,
    diversify_by_source,
)
from src.query_expansion import expand_query
from src.document_metadata import classify_document_for_question
from src.metrics import retrieval_metrics
from eval_chunking_langsmith import build_evaluators


def test_hnsw_error_includes_rebuild_instructions():
    message = _format_retrieval_error(RuntimeError("Error loading hnsw index"))

    assert "HNSW 索引无法加载" in message
    assert "python ingest.py" in message
    assert "Error loading hnsw index" in message


def test_other_retrieval_error_is_unchanged():
    assert _format_retrieval_error(RuntimeError("database unavailable")) == (
        "database unavailable"
    )


def test_lexical_terms_support_chinese_and_latin_queries():
    terms = _lexical_terms("比较 PC 与 Tritan 的耐热性能")

    assert "pc" in terms
    assert "tritan" in terms
    assert "耐热" in terms


def test_hybrid_merge_prefers_exact_lexical_hits():
    lexical = [
        Document(page_content="lexical-1", metadata={"source": "a.md", "chunk_id": "a1"}),
        Document(page_content="lexical-2", metadata={"source": "b.md", "chunk_id": "b1"}),
    ]
    semantic = [
        Document(page_content="semantic-1", metadata={"source": "c.md", "chunk_id": "c1"}),
        Document(page_content="lexical-1", metadata={"source": "a.md", "chunk_id": "a1"}),
    ]

    merged = _merge_hybrid_documents(semantic, lexical, top_k=3)

    assert [doc.page_content for doc in merged] == [
        "lexical-1",
        "lexical-2",
        "semantic-1",
    ]


def test_adult_anthropometry_is_indirect_for_cup_diameter():
    status, _ = classify_document_for_question(
        "成人水杯杯径设计",
        {"source_category": "anthropometry", "population": "adults", "material_grade": "unknown"},
        "成人手宽和手长百分位数据",
    )
    assert status == "indirect"


def test_a_standard_designation_matches_across_punctuation_variants():
    """The question writes `GB/T 16252—2023`; the file is `GBT+16252-2023.pdf`.

    Comparing after stripping only spaces left those unequal, so the reference
    branch never fired, every retrieved document fell through to the last-resort
    `indirect`, and the delivery gate then refused an answer that cited the
    standard being asked about -- while that standard's own text was in the
    evidence.  Measured end to end on a live question.
    """
    status, reason = classify_document_for_question(
        "GB/T 16252—2023 的名称和适用范围是什么？",
        {
            "source_category": "anthropometry",
            "population": "unknown",
            "material_grade": "unknown",
            "source": "03_人体工程学/Anthropometry/GBT+16252-2023.pdf",
        },
        "成年人手部尺寸分型",
    )
    assert status == "direct", reason


def test_a_different_standard_is_not_matched_by_designation():
    """Normalising punctuation must not make every standard match every question."""
    status, _ = classify_document_for_question(
        "GB/T 16252—2023 的名称和适用范围是什么？",
        {
            "source_category": "anthropometry",
            "population": "unknown",
            "material_grade": "unknown",
            "source": "03_人体工程学/Anthropometry/GBT+10000-2023.pdf",
        },
        "中国成年人人体尺寸",
    )
    assert status == "indirect"


def test_minor_data_is_scope_mismatch_for_adult_question():
    status, _ = classify_document_for_question(
        "成人产品尺寸设计",
        {"source_category": "standard", "population": "minors", "material_grade": "unknown"},
        "未成年人人体尺寸标准",
    )
    assert status == "scope_mismatch"


def test_retrieval_metrics_cover_ranked_results():
    metrics = retrieval_metrics(["a", "x", "b"], {"b", "c"})
    assert metrics["precision_at_5"] == 0.3333
    assert metrics["recall_at_5"] == 0.5
    assert metrics["mrr"] == 0.3333


def test_confidence_thresholds_are_explicit():
    low = Document(page_content="x", metadata={"retrieval_relevance": 0.2})
    assert confidence_from_documents([low])[1] == "low"
    uncertain = [
        Document(page_content="x", metadata={"retrieval_relevance": 0.4}),
        Document(page_content="y", metadata={"retrieval_relevance": 0.38}),
    ]
    assert confidence_from_documents(uncertain)[1] == "uncertain"


def test_rrf_keeps_dense_and_bm25_ranks_for_shared_chunk():
    dense = [Document(page_content="a", metadata={"source": "a.md", "chunk_id": "a1"})]
    sparse = [Document(page_content="a", metadata={"source": "a.md", "chunk_id": "a1", "retrieval_lexical_score": 4.2})]

    result = _rrf_fusion(dense, sparse, top_k=1)

    assert result[0].metadata["dense_rank"] == 1
    assert result[0].metadata["bm25_rank"] == 1
    assert result[0].metadata["retrieval_lexical_score"] == 4.2


def test_rrf_explicit_union_keeps_bm25_only_candidates():
    dense = [Document(page_content="dense", metadata={"source": "a", "chunk_id": "a1"})]
    sparse = [Document(page_content="sparse", metadata={"source": "b", "chunk_id": "b1"})]
    result = _rrf_fusion(dense, sparse, top_k=None, dense_weight=0.55, sparse_weight=0.45)
    _annotate_union(result)
    assert {doc.metadata["chunk_id"] for doc in result} == {"a1", "b1"}
    assert next(doc for doc in result if doc.metadata["chunk_id"] == "b1").metadata["retrieval_stage"] == "bm25_only"


def test_reranked_selection_does_not_apply_dense_threshold():
    documents = [
        Document(page_content="exact", metadata={"source": "standard.pdf", "chunk_id": "s1", "retrieval_relevance": 0.01, "rerank_score": 9.0, "rerank_rank": 1, "retrieval_stage": "bm25_only"}),
        Document(page_content="other", metadata={"source": "other.pdf", "chunk_id": "o1", "retrieval_relevance": 0.9, "rerank_score": 1.0, "rerank_rank": 2, "retrieval_stage": "dense_only"}),
    ]
    result = _select_reranked(documents, top_k=2)
    assert [doc.metadata["chunk_id"] for doc in result] == ["s1", "o1"]
    assert result[0].metadata["retrieval_threshold_passed"] is True


def test_source_diversification_preserves_multiple_sources():
    documents = [
        Document(page_content="a1", metadata={"source": "a.md", "chunk_id": "a1"}),
        Document(page_content="a2", metadata={"source": "a.md", "chunk_id": "a2"}),
        Document(page_content="b1", metadata={"source": "b.md", "chunk_id": "b1"}),
        Document(page_content="c1", metadata={"source": "c.md", "chunk_id": "c1"}),
    ]

    result = diversify_by_source(documents, top_k=3, per_source=2)

    assert [doc.metadata["source"] for doc in result] == ["a.md", "a.md", "b.md"]
    assert [doc.metadata["final_rank"] for doc in result] == [1, 2, 3]


def test_source_diversification_does_not_cap_single_source():
    documents = [
        Document(page_content=str(index), metadata={"source": "a.md", "chunk_id": str(index)})
        for index in range(4)
    ]

    assert len(diversify_by_source(documents, top_k=4, per_source=2)) == 4


def test_source_diversification_zero_cap_means_unlimited():
    documents = [
        Document(page_content=str(index), metadata={"source": "a.md", "chunk_id": str(index)})
        for index in range(4)
    ]
    assert len(diversify_by_source(documents, top_k=4, per_source=0)) == 4


def test_source_diversification_skips_low_relevance_candidates():
    documents = [
        Document(page_content="low", metadata={"source": "low.md", "chunk_id": "l1", "retrieval_relevance": 0.1}),
        Document(page_content="high", metadata={"source": "high.md", "chunk_id": "h1", "retrieval_relevance": 0.8}),
    ]
    result = diversify_by_source(documents, top_k=2, per_source=2)
    assert [doc.metadata["source"] for doc in result] == ["high.md"]


def test_source_diversification_keeps_exact_match_after_dense_threshold():
    documents = [
        Document(
            page_content="standard",
            metadata={
                "source": "26158-2010-gbt-e-300.pdf",
                "chunk_id": "s1",
                "retrieval_relevance": 0.1,
                "retrieval_exact_match": True,
                "rrf_score": 0.005,
            },
        ),
        Document(
            page_content="design rule",
            metadata={
                "source": "rule.md",
                "chunk_id": "r1",
                "retrieval_relevance": 0.8,
                "rrf_score": 0.01,
            },
        ),
    ]

    result = diversify_by_source(documents, top_k=2, per_source=2)

    assert [doc.metadata["source"] for doc in result] == [
        "26158-2010-gbt-e-300.pdf",
        "rule.md",
    ]
    assert result[0].metadata["pre_diversity_rank"] == 1
    assert result[0].metadata["retrieval_threshold_passed"] is True


def test_query_expansion_adds_child_standard_and_material_clues():
    child = expand_query("儿童水杯握持设计")
    assert any("26158" in query for query in child)
    material = expand_query("PP 和 Tritan 材料对比")
    assert any("3486-01" in query and "TX1001" in query for query in material)


def test_extract_query_entities_normalises_domain_identifiers():
    entities = _extract_query_entities("比较 GB/T 26158—2010、PP 3486_01 和 Tritan TX-1001 的食品接触性能")
    assert "gb/t 26158-2010" in entities
    assert "pp 3486-01" in entities
    assert "tritan tx1001" in entities
    assert "food_contact" in entities


def test_conditional_bm25_queries_map_material_comparison_to_unique_tds(monkeypatch):
    monkeypatch.setattr("src.retriever.settings", type("S", (), {
        "retriever_bm25_mode": "conditional",
        "retriever_max_entity_bm25_queries": 3,
    })())
    lanes = build_bm25_queries("PP 和 Tritan 材料对比")
    assert len(lanes) == 3
    assert [lane["origin"] for lane in lanes[1:]] == ["deterministic", "deterministic"]
    assert {lane["canonical_id"] for lane in lanes[1:]} == {"pp_3486_01", "tritan_tx1001"}
    assert all(lane["guardable"] for lane in lanes[1:])


def test_conditional_bm25_queries_bound_entity_lanes(monkeypatch):
    monkeypatch.setattr("src.retriever.settings", type("S", (), {
        "retriever_bm25_mode": "conditional",
        "retriever_max_entity_bm25_queries": 3,
    })())
    lanes = build_bm25_queries("比较 GB/T 26158-2010、GB 4806.7-2023、PP 3486-01、Tritan TX1001")
    assert lanes[0]["kind"] == "original"
    assert len(lanes) <= 4
    assert all(item["kind"] == "entity" for item in lanes[1:])


def test_conditional_bm25_queries_create_deterministic_child_standard(monkeypatch):
    monkeypatch.setattr("src.retriever.settings", type("S", (), {
        "retriever_bm25_mode": "conditional",
        "retriever_max_entity_bm25_queries": 3,
    })())
    lanes = build_bm25_queries("儿童手部尺寸与杯径设计")
    assert len(lanes) == 2
    assert lanes[1]["canonical_id"] == "gb_26158_2010"
    assert lanes[1]["origin"] == "deterministic"
    assert lanes[1]["guardable"] is True


def test_conditional_bm25_queries_create_food_contact_standard_without_pc_tds(monkeypatch):
    monkeypatch.setattr("src.retriever.settings", type("S", (), {
        "retriever_bm25_mode": "conditional",
        "retriever_max_entity_bm25_queries": 3,
    })())
    lanes = build_bm25_queries("水杯材料安全合规，比较 PC 和 Tritan")
    assert any(lane["canonical_id"] == "gb_4806_7_2023" for lane in lanes)
    assert not any(lane["canonical_id"] is None and lane["kind"] == "entity" for lane in lanes[1:])
    assert not any("LEXAN" in str(lane["query"]) for lane in lanes)


def test_conditional_bm25_queries_create_single_process_lane_without_guard(monkeypatch):
    monkeypatch.setattr("src.retriever.settings", type("S", (), {
        "retriever_bm25_mode": "conditional",
        "retriever_max_entity_bm25_queries": 3,
    })())
    lanes = build_bm25_queries("Tritan 干燥、挤出和注塑工艺")
    process = [lane for lane in lanes if lane["kind"] == "process"]
    assert len(process) == 1
    assert process[0]["guardable"] is False


def test_process_prior_requires_exact_metadata_terms_near_cutoff(monkeypatch):
    monkeypatch.setattr("src.retriever.settings", type("S", (), {
        "retriever_process_guide_prior": True,
        "retriever_process_guide_prior_bonus": 0.025,
        "retriever_source_role_priority": True,
    })())
    lanes = build_bm25_queries("透明 Tritan 杯体注塑时如何处理干燥、挤出、浇口和排气？")
    exact = Document(
        page_content="technical guide",
        metadata={
            "source": "Eastman_Tritan_Drying_Extrusion_Processing_Guide.pdf",
            "source_role": "process_guide",
        },
    )
    body_only = Document(
        page_content="Tritan drying extrusion guide",
        metadata={"source": "Eastman_Tritan_Processing_Guide.pdf", "source_role": "process_guide"},
    )
    assert _process_prior_signal(exact, lanes, 11, 20) == (
        True,
        2,
        "deterministic_process_lane_exact_metadata_match",
    )
    assert _process_prior_signal(body_only, lanes, 11, 20)[0] is False
    assert _process_prior_signal(exact, lanes, 23, 20)[0] is False


def test_process_prior_does_not_change_retrieval_ranks_or_roles(monkeypatch):
    monkeypatch.setattr("src.retriever.settings", type("S", (), {
        "retriever_process_guide_prior": True,
        "retriever_process_guide_prior_bonus": 0.025,
        "retriever_source_role_priority": True,
    })())
    lanes = build_bm25_queries("Tritan 干燥、挤出和注塑工艺")
    document = Document(
        page_content="technical guide",
        metadata={
            "source": "Eastman_Tritan_Drying_Extrusion_Processing_Guide.pdf",
            "source_role": "internal_rule",
            "dense_rank": 9,
            "bm25_rank": 12,
        },
    )
    applied, _, reason = _process_prior_signal(document, lanes, 11, 20)
    assert applied is False
    assert reason == "no_deterministic_process_lane_or_role_mismatch"
    assert document.metadata["dense_rank"] == 9
    assert document.metadata["bm25_rank"] == 12


def test_source_role_path_rules_override_incidental_standard_citation():
    document = Document(
        page_content="GB/T 26158 is cited for context",
        metadata={
            "source": "Eastman_Processing_Guide.pdf",
            "source_category": "standard",
            "source_role": "process_guide",
        },
    )
    assert _source_role(document) == "process_guide"


def test_canonical_ids_cover_forward_and_reverse_spellings():
    assert _canonical_entity_id("GB/T 26158—2010") == "gb_26158_2010"
    assert _canonical_entity_id("GB_26158-2010") == "gb_26158_2010"
    assert _canonical_entity_id("26158-2010-gbt") == "gb_26158_2010"
    assert _canonical_entity_id("GB 4806.7-2023") == "gb_4806_7_2023"
    assert _canonical_entity_id("PP_3486-01") == "pp_3486_01"
    assert _canonical_entity_id("TX-1001") == "tritan_tx1001"
    assert "gb_26158_2010" in _canonical_entity_ids_from_text("26158-2010-gbt.pdf")


def test_source_category_has_priority_over_content_heuristics():
    document = Document(
        page_content="GB/T 26158 is cited for context",
        metadata={"source": "Eastman_Processing_Guide.pdf", "source_category": "process_guide"},
    )
    assert _source_role(document) == "process_guide"


def test_index_and_source_list_documents_are_low_authority():
    for source in ("资料来源清单.md", "知识库目录说明.md", "README.md", "docs/index.md"):
        assert _is_low_authority_document(
            Document(page_content="technical content", metadata={"source": source})
        ) is True


def test_material_tds_filename_beats_source_list_body_match():
    source_list = Document(
        page_content="PP 3486-01 and Tritan TX1001 are available",
        metadata={"source": "资料来源清单.md", "source_role": "material_tds"},
    )
    tds = Document(
        page_content="technical data",
        metadata={
            "source": "02_材料数据/PP/LyondellBasell_PP_3486-01_TDS.pdf",
            "source_role": "material_tds",
        },
    )
    assert _entity_candidate_priority(source_list, "pp_3486_01") == -1
    assert _entity_candidate_priority(tds, "pp_3486_01") > 0


def test_standard_filename_beats_source_list_body_match():
    source_list = Document(
        page_content="GB 4806.7-2023",
        metadata={"source": "知识库目录说明.md", "source_role": "standard"},
    )
    standard = Document(
        page_content="requirements",
        metadata={"source": "GB_4806.7-2023.pdf", "source_role": "standard"},
    )
    assert _entity_candidate_priority(source_list, "gb_4806_7_2023") == -1
    assert _entity_candidate_priority(standard, "gb_4806_7_2023") > 0


def test_top20_guard_ignores_low_authority_entity_representative(monkeypatch):
    monkeypatch.setattr(
        "src.retriever.settings",
        type(
            "S",
            (),
            {
                "retriever_ranking_mode": "reranker_entity_rrf",
                "retriever_entity_guard": True,
                "retriever_rerank_top_k": 20,
                "retriever_source_role_priority": True,
                "retriever_rerank_weight": 0.82,
                "retriever_explicit_entity_weight": 0.10,
                "retriever_expanded_entity_weight": 0.03,
                "retriever_rrf_weight": 0.05,
                "retriever_bm25_mode": "conditional",
                "retriever_max_entity_bm25_queries": 3,
            },
        )(),
    )
    low = Document(
        page_content="PP 3486-01 is listed here",
        metadata={
            "source": "source_list.md",
            "chunk_id": "low",
            "rerank_score": 10.0,
            "rrf_score": 0.01,
            "bm25_query_hits": [{"canonical_id": "pp_3486_01", "rank": 1}],
            "source_role": "material_tds",
        },
    )
    actual = Document(
        page_content="PP 3486-01 technical data sheet",
        metadata={
            "source": "LyondellBasell_PP_3486-01_TDS.pdf",
            "chunk_id": "actual",
            "rerank_score": -1.0,
            "rrf_score": 0.001,
            "bm25_query_hits": [{"canonical_id": "pp_3486_01", "rank": 1}],
            "source_role": "material_tds",
        },
    )
    fillers = [
        Document(
            page_content=f"generic {index}",
            metadata={
                "source": f"generic-{index}.md",
                "chunk_id": f"g{index}",
                "rerank_score": 9.0 - index * 0.1,
                "rrf_score": 0.01,
            },
        )
        for index in range(30)
    ]
    assert _canonical_entity_id("PP 3486-01") == "pp_3486_01"
    assert "pp_3486_01" in _canonical_entity_ids_from_text(
        "LyondellBasell_PP_3486-01_TDS.pdf PP 3486-01 technical data sheet"
    )
    guard_question = "低成本水杯在 PP 和 Tritan 之间比较，PP 3486-01 TDS"
    assert any(
        lane.get("canonical_id") == "pp_3486_01" and lane.get("guardable")
        for lane in build_bm25_queries(guard_question)
    ), repr(build_bm25_queries(guard_question))
    ranked = _apply_combined_ranking(guard_question, [low, *fillers, actual], True)
    assert any(doc.metadata["chunk_id"] == "actual" for doc in ranked)
    assert any(doc.metadata.get("guardable_candidate_entity_ids") for doc in ranked)
    assert ranked[14].metadata["chunk_id"] == "actual"
    assert ranked[14].metadata["entity_guard_triggered"] is True
    assert ranked[14].metadata["guarded_entity_id"] == "pp_3486_01"


def test_union_limit_is_hard_even_with_protected_entities(monkeypatch):
    monkeypatch.setattr("src.retriever.settings", type("S", (), {})())
    docs = [
        Document(page_content="x", metadata={"chunk_id": f"{i}", "retrieval_exact_matches": ["gb/t 26158-2010"]})
        for i in range(5)
    ]
    result = _limit_union_candidates(docs, "GB/T 26158-2010", 2)
    assert len(result) == 2


def test_entity_match_uses_filename_and_title_metadata():
    document = Document(
        page_content="Technical data sheet for food contact applications",
        metadata={"source": "LyondellBasell_PP_3486-01_TDS.pdf", "title": "PP 3486-01 TDS", "source_category": "material_tds"},
    )
    score, matched = _entity_match_score(["pp 3486-01", "tds"], document)
    assert score > 0.5
    assert set(matched) == {"pp 3486-01", "tds"}


def test_entity_match_prefers_filename_over_body():
    source_doc = Document(page_content="generic material text", metadata={"source": "GB_4806.7-2023.pdf", "title": "standard", "source_category": "standard"})
    body_doc = Document(page_content="GB 4806.7-2023 requirements", metadata={"source": "standard.pdf", "title": "standard", "source_category": "standard"})
    source_score, _ = _entity_match_score(["gb 4806.7-2023"], source_doc)
    body_score, _ = _entity_match_score(["gb 4806.7-2023"], body_doc)
    assert source_score > body_score


def test_combined_ranking_records_entity_and_rrf_signals(monkeypatch):
    monkeypatch.setattr(
        "src.retriever.settings",
        type("S", (), {"retriever_ranking_mode": "reranker_entity_rrf", "retriever_entity_guard": True, "retriever_rerank_top_k": 3})(),
    )
    docs = [
        Document(page_content="generic", metadata={"source": "generic.md", "chunk_id": "g", "rerank_score": 3.0, "rrf_score": 0.01, "source_category": "internal_rule"}),
        Document(page_content="PP 3486-01 TDS", metadata={"source": "PP_3486-01_TDS.pdf", "chunk_id": "p", "rerank_score": 1.0, "rrf_score": 0.005, "bm25_rank": 1, "source_category": "material_tds"}),
    ]
    ranked = _apply_combined_ranking("PP 材料 TDS", docs, True)
    assert all("combined_score" in doc.metadata for doc in ranked)
    assert "pp" in ranked[0].metadata["query_entities"]
    assert any(doc.metadata["entity_match_score"] > 0 for doc in ranked)


def test_low_authority_documents_yield_to_authoritative_candidates(monkeypatch):
    monkeypatch.setattr(
        "src.retriever.settings",
        type("S", (), {
            "retriever_ranking_mode": "reranker_entity_rrf",
            "retriever_entity_guard": False,
            "retriever_rerank_top_k": 20,
            "retriever_low_authority_prior": True,
            "retriever_low_authority_penalty": 0.12,
        })(),
    )
    index_doc = Document(
        page_content="children GB/T 26158 source list",
        metadata={"source": "source_list.md", "chunk_id": "index", "rerank_score": 0.99, "rrf_score": 0.01},
    )
    technical_doc = Document(
        page_content="children hand dimensions and water cup design rule",
        metadata={"source": "water-cup-rule.md", "chunk_id": "rule", "rerank_score": 1.0, "rrf_score": 0.01},
    )
    ranked = _apply_combined_ranking("儿童水杯手部尺寸", [index_doc, technical_doc], True)
    assert ranked[0].metadata["chunk_id"] == "rule"
    assert index_doc.metadata["low_authority_penalty_applied"] is True
    assert index_doc.metadata["low_authority_penalty_delta"] == -0.12
    assert index_doc.metadata["low_authority_score_capped"] is False


def test_low_authority_score_is_capped_below_best_technical_candidate(monkeypatch):
    monkeypatch.setattr(
        "src.retriever.settings",
        type("S", (), {
            "retriever_ranking_mode": "reranker_entity_rrf",
            "retriever_entity_guard": False,
            "retriever_rerank_top_k": 20,
            "retriever_low_authority_prior": True,
            "retriever_low_authority_penalty": 0.12,
        })(),
    )
    index_doc = Document(
        page_content="source list with many matching terms",
        metadata={"source": "catalog.md", "chunk_id": "index", "rerank_score": 10.0, "rrf_score": 0.01},
    )
    technical_doc = Document(
        page_content="substantive design rule",
        metadata={"source": "design-rule.md", "chunk_id": "rule", "rerank_score": 1.0, "rrf_score": 0.01},
    )
    ranked = _apply_combined_ranking("water cup design", [index_doc, technical_doc], True)
    assert ranked[0].metadata["chunk_id"] == "rule"
    assert index_doc.metadata["low_authority_score_capped"] is True
    assert index_doc.metadata["combined_score"] < technical_doc.metadata["combined_score"]


def test_anthropometry_prior_only_targets_exact_value_evidence_gap(monkeypatch):
    monkeypatch.setattr(
        "src.retriever.settings",
        type("S", (), {
            "retriever_anthropometry_prior": True,
        })(),
    )
    document = Document(
        page_content="Children hand dimensions and grip data",
        metadata={"source": "children_hand_study.pdf", "source_role": "anthropometry"},
    )
    matched, reason = _anthropometry_prior_signal(
        "用户要求给出儿童水杯最佳握持直径精确值，但知识库只有人体数据和设计原则",
        document,
        30,
        20,
    )
    assert matched is True
    assert reason == "exact_value_request_with_corpus_evidence_gap"
    ordinary, _ = _anthropometry_prior_signal("儿童水杯尺寸设计", document, 10, 20)
    assert ordinary is False
    standard = Document(
        page_content="儿童人体尺寸标准",
        metadata={"source": "26158-2010-gbt-e-300.pdf", "source_role": "anthropometry"},
    )
    standard_match, reason = _anthropometry_prior_signal(
        "用户要求给出儿童水杯最佳握持直径精确值，但知识库只有人体数据",
        standard,
        30,
        20,
    )
    assert standard_match is False
    assert reason == "no_child_anthropometry_evidence"


def test_low_authority_penalty_is_not_applied_without_alternative(monkeypatch):
    monkeypatch.setattr(
        "src.retriever.settings",
        type("S", (), {
            "retriever_ranking_mode": "reranker_entity_rrf",
            "retriever_entity_guard": False,
            "retriever_rerank_top_k": 20,
            "retriever_low_authority_prior": True,
            "retriever_low_authority_penalty": 0.12,
        })(),
    )
    index_doc = Document(
        page_content="catalog with children data",
        metadata={"source": "index.md", "chunk_id": "index", "rerank_score": 1.0, "rrf_score": 0.01},
    )
    ranked = _apply_combined_ranking("儿童水杯", [index_doc], True)
    assert ranked[0].metadata["low_authority_penalty_applied"] is False
    assert ranked[0].metadata["low_authority_penalty_delta"] == 0.0


def test_expanded_entities_can_be_used_separately_from_reranker_question(monkeypatch):
    monkeypatch.setattr("src.retriever.settings", type("S", (), {"retriever_ranking_mode": "reranker_entity_rrf", "retriever_entity_guard": False, "retriever_rerank_top_k": 20})())
    doc = Document(page_content="Food contact requirements", metadata={"source": "GB_4806.7-2023.pdf", "chunk_id": "s", "rerank_score": 0.1, "rrf_score": 0.01, "source_category": "standard"})
    ranked = _apply_combined_ranking("材料合规", [doc], True, "材料合规 GB 4806.7-2023")
    assert "gb 4806.7-2023" in ranked[0].metadata["query_entities"]
    assert ranked[0].metadata["entity_match_score"] > 0


def test_chunk_evaluator_is_omitted_without_expected_chunk_labels():
    evaluators = build_evaluators([{"expected_sources": ["a.pdf"]}])
    assert all(evaluator.__name__ != "chunk_recall_at_10" for evaluator in evaluators)


def test_chunk_evaluator_is_enabled_with_expected_chunk_labels():
    evaluators = build_evaluators([{"expected_chunks": ["chunk-1"]}])
    assert any(evaluator.__name__ == "chunk_recall_at_10" for evaluator in evaluators)
