"""Modular retrieval package for the DesignKB Agent.

Layers (strictly acyclic):
- ``_compat``       : shared document key/clone helpers & settings bridge
- ``domain_rules``  : pluggable domain entities, BM25 lanes, and ranking priors
- ``fusion``        : Chroma dense retrieval, RRF fusion, evidence annotation
- ``lexical``       : BM25 lexical index & multi-lane lexical search
- ``ranking``       : combined ranking, reranked selection, diversification
- ``pipeline``      : cached single-query & bounded multi-query orchestration
"""

from src.retrieval.domain_rules import build_bm25_queries, domain_rules_enabled
from src.retrieval.fusion import confidence_from_documents
from src.retrieval.pipeline import (
    KnowledgeBaseUnavailable,
    cap_per_source,
    hybrid_merge,
    retrieve_documents,
    retrieve_documents_multi,
    select_rerank_pool,
)
from src.retrieval.ranking import diversify_by_source

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
]
