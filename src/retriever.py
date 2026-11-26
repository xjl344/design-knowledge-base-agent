"""Backwards-compatible facade for the modular ``src.retrieval`` package.

All retrieval implementation now lives in focused submodules under
``src/retrieval/``:
- ``lexical.py``       : BM25 lexical indexing and multi-lane lexical search
- ``fusion.py``        : Chroma dense search, RRF fusion, evidence annotation
- ``domain_rules.py``  : Pluggable domain entities, BM25 lanes, and priors
- ``ranking.py``       : Combined ranking, source diversification, diagnostics
- ``pipeline.py``      : Caching, rerank input budget, and multi-query merge

Existing callers and tests that import from ``src.retriever`` or monkeypatch
``src.retriever.settings`` continue to work unchanged.
"""

from __future__ import annotations

from config import settings
from src.retrieval._compat import clone_documents as _clone_documents
from src.retrieval._compat import document_key as _document_key
from src.retrieval.domain_rules import (
    _BM25_ENTITY_ALIASES,
    _ENTITY_PATTERNS,
    _FINAL_ENTITY_NAMES,
    _LOW_AUTHORITY_SOURCE_MARKERS,
    _MATERIAL_TDS_MAPPINGS,
    _PROCESS_PRIOR_MATERIALS,
    _PROCESS_TERM_ALIASES,
    _STRICT_ENTITY_NAMES,
    _anthropometry_prior_signal,
    _build_entity_plan,
    _candidate_entity_text,
    _canonical_entity_id,
    _canonical_entity_ids_from_text,
    _compact_entity_text,
    _compact_query_text,
    _entity_candidate_priority,
    _entity_lane_rank,
    _entity_match_locations,
    _entity_match_score,
    _entity_role_compatible,
    _extract_query_entities,
    _is_low_authority_document,
    _make_bm25_lane,
    _normalise_bm25_lane,
    _process_prior_signal,
    _query_roles,
    _source_role,
    build_bm25_queries,
    domain_rules_enabled,
)
from src.retrieval.fusion import (
    _annotate_document_evidence,
    _annotate_union,
    _format_retrieval_error,
    _limit_union_candidates,
    _merge_hybrid_documents,
    _rrf_fusion,
    _vector_retrieve_documents,
    confidence_from_documents,
)
from src.retrieval.lexical import (
    _LEXICAL_INDEX_CACHE,
    _lexical_retrieve_documents,
    _lexical_retrieve_documents_multi,
    _lexical_terms,
)
from src.retrieval.pipeline import (
    KnowledgeBaseUnavailable,
    _RETRIEVAL_CACHE,
    _RETRIEVAL_CACHE_LOCK,
    _RETRIEVAL_EXECUTION_LOCK,
    _dedupe_chunks,
    _retrieval_cache_key,
    _retrieve_documents_uncached,
    cap_per_source,
    hybrid_merge,
    retrieve_documents,
    retrieve_documents_multi,
    select_rerank_pool,
)
from src.retrieval.ranking import (
    _apply_combined_ranking,
    _entity_diagnostics,
    _normalise_scores,
    _select_reranked,
    diversify_by_source,
)

__all__ = [
    "KnowledgeBaseUnavailable",
    "build_bm25_queries",
    "cap_per_source",
    "confidence_from_documents",
    "diversify_by_source",
    "domain_rules_enabled",
    "hybrid_merge",
    "retrieve_documents",
    "retrieve_documents_multi",
    "select_rerank_pool",
    "settings",
]
