"""Dense vector retrieval, RRF fusion, evidence annotation, and confidence."""

from __future__ import annotations

from langchain_chroma import Chroma
from langchain_core.documents import Document

from src.document_metadata import build_document_metadata, classify_document_for_question
from src.retrieval._compat import active_settings, document_key
from src.retrieval.domain_rules import (
    _candidate_entity_text,
    _canonical_entity_ids_from_text,
    _entity_candidate_priority,
    _entity_lane_rank,
    build_bm25_queries,
)


def _annotate_document_evidence(
    question: str,
    document: Document,
    relevance: float | None = None,
) -> Document:
    """Attach conservative, query-specific evidence metadata to a document."""
    settings = active_settings()
    metadata = document.metadata
    if metadata.get("source_category", "unknown") == "unknown":
        inferred = build_document_metadata(
            str(metadata.get("source", "")),
            str(metadata.get("source_title") or metadata.get("title", "unknown")),
            document.page_content,
            metadata.get("page") if isinstance(metadata.get("page"), int) else None,
        )
        for key, value in inferred.items():
            metadata.setdefault(key, value)
    status, reason = classify_document_for_question(
        question,
        metadata,
        document.page_content,
        relevance,
        getattr(settings, "retriever_min_relevance", 0.25),
    )
    metadata["retrieval_evidence_status"] = status
    metadata["retrieval_evidence_reason"] = reason
    if relevance is not None:
        metadata["retrieval_relevance"] = round(float(relevance), 6)
    return document


def _vector_retrieve_documents(
    vectorstore: Chroma, question: str, k: int
) -> list[Document]:
    """Retrieve with relevance scores when the installed Chroma supports them."""
    try:
        scored = vectorstore.similarity_search_with_relevance_scores(question, k=k)
        documents: list[Document] = []
        for document, score in scored:
            document.metadata["source_type"] = "local"
            documents.append(_annotate_document_evidence(question, document, float(score)))
        return documents
    except (AttributeError, TypeError, ValueError):
        retriever = vectorstore.as_retriever(
            search_type="similarity", search_kwargs={"k": k}
        )
        return [
            _annotate_document_evidence(question, document)
            for document in retriever.invoke(question)
        ]


def _merge_hybrid_documents(
    vector_documents: list[Document],
    lexical_documents: list[Document],
    top_k: int,
) -> list[Document]:
    """Blend exact lexical hits with semantic hits."""
    result: list[Document] = []
    seen: set[str] = set()
    lexical_budget = min(len(lexical_documents), top_k)
    ordered_documents = (
        lexical_documents[:lexical_budget]
        + vector_documents
        + lexical_documents[lexical_budget:]
    )
    for document in ordered_documents:
        key = document_key(document)
        if key in seen:
            continue
        seen.add(key)
        result.append(document)
        if len(result) >= top_k:
            break
    return result


def _rrf_fusion(
    vector_documents: list[Document],
    lexical_documents: list[Document],
    top_k: int | None,
    dense_weight: float = 0.7,
    sparse_weight: float = 0.3,
) -> list[Document]:
    rrf_k = getattr(active_settings(), "rrf_k", 60)
    scores: dict[str, float] = {}
    documents: dict[str, Document] = {}
    for rank, document in enumerate(vector_documents, 1):
        key = document_key(document)
        documents.setdefault(key, document)
        scores[key] = scores.get(key, 0.0) + dense_weight / (rrf_k + rank)
        documents[key].metadata["dense_rank"] = rank
    for rank, document in enumerate(lexical_documents, 1):
        key = document_key(document)
        if key not in documents:
            documents[key] = document
        else:
            target = documents[key]
            for field in ("source_title", "title", "section_path", "parent_document"):
                if not target.metadata.get(field) and document.metadata.get(field):
                    target.metadata[field] = document.metadata[field]
            target.metadata.setdefault("bm25_query_hits", []).extend(
                document.metadata.get("bm25_query_hits", [])
            )
            merged_entity_ranks = dict(target.metadata.get("entity_bm25_ranks", {}))
            for canonical_id, rank_value in document.metadata.get("entity_bm25_ranks", {}).items():
                merged_entity_ranks[canonical_id] = min(
                    int(merged_entity_ranks.get(canonical_id, 10**9)),
                    int(rank_value),
                )
            target.metadata["entity_bm25_ranks"] = merged_entity_ranks
            target.metadata["retrieval_exact_matches"] = list(dict.fromkeys(
                list(target.metadata.get("retrieval_exact_matches", []))
                + list(document.metadata.get("retrieval_exact_matches", []))
            ))
        scores[key] = scores.get(key, 0.0) + sparse_weight / (rrf_k + rank)
        documents[key].metadata["bm25_rank"] = rank
        documents[key].metadata["retrieval_lexical_score"] = document.metadata.get(
            "retrieval_lexical_score"
        )
        if document.metadata.get("retrieval_exact_match"):
            documents[key].metadata["retrieval_exact_match"] = True
            documents[key].metadata["retrieval_exact_matches"] = document.metadata.get(
                "retrieval_exact_matches", []
            )
    ranked = sorted(scores, key=scores.get, reverse=True)
    limit = len(ranked) if top_k is None else top_k
    for key in ranked[:limit]:
        documents[key].metadata["rrf_score"] = round(scores[key], 8)
    return [documents[key] for key in ranked[:limit]]


def _annotate_union(documents: list[Document]) -> list[Document]:
    """Attach stable channel/rank metadata to every explicit-union candidate."""
    for union_rank, document in enumerate(documents, 1):
        document.metadata["union_rank"] = union_rank
        if document.metadata.get("rerank_score") is not None:
            document.metadata["model_rerank_rank"] = document.metadata.get("rerank_rank")
        dense_rank = document.metadata.get("dense_rank")
        bm25_rank = document.metadata.get("bm25_rank")
        if dense_rank is not None and bm25_rank is not None:
            stage = "dense_and_bm25"
        elif dense_rank is not None:
            stage = "dense_only"
        elif bm25_rank is not None:
            stage = "bm25_only"
        else:
            stage = "not_retrieved"
        document.metadata["retrieval_stage"] = stage
    return documents


def _limit_union_candidates(
    documents: list[Document], query_plan: list[dict[str, object]] | str, limit: int
) -> list[Document]:
    """Bound reranker work while preserving one exact guardable candidate."""
    if len(documents) <= limit:
        for document in documents:
            document.metadata["union_truncated"] = False
        return documents
    plan = build_bm25_queries(query_plan) if isinstance(query_plan, str) else query_plan
    guardable = {
        str(item.get("canonical_id")): item
        for item in plan
        if item.get("guardable") and item.get("canonical_id")
    }
    protected_by_entity: dict[str, Document] = {}
    for document in documents:
        if document.metadata.get("dense_rank") is None and document.metadata.get("bm25_rank") is None:
            continue
        matched_ids = set(guardable) & _canonical_entity_ids_from_text(_candidate_entity_text(document))
        for entity_id in matched_ids:
            if _entity_lane_rank(document, entity_id) is None:
                continue
            authority_priority = _entity_candidate_priority(document, entity_id)
            if authority_priority < 0:
                continue
            current = protected_by_entity.get(entity_id)
            candidate_key = (
                -authority_priority,
                int(_entity_lane_rank(document, entity_id) or 10**9),
                int(document.metadata.get("bm25_rank") or 10**9),
                int(document.metadata.get("dense_rank") or 10**9),
                document_key(document),
            )
            current_key = (
                -_entity_candidate_priority(current, entity_id),
                int(_entity_lane_rank(current, entity_id) or 10**9),
                int(current.metadata.get("bm25_rank") or 10**9),
                int(current.metadata.get("dense_rank") or 10**9),
                document_key(current),
            ) if current is not None else (10**9, 10**9, 10**9, 10**9, "")
            if current is None or candidate_key < current_key:
                protected_by_entity[entity_id] = document
    protected_order = list(protected_by_entity.values())
    selected: list[Document] = []
    seen: set[str] = set()
    for document in protected_order[: max(0, limit)] + documents:
        key = document_key(document)
        if key in seen:
            continue
        seen.add(key)
        selected.append(document)
        if len(selected) >= limit:
            break
    for document in selected:
        document.metadata["union_truncated"] = True
        document.metadata["union_limit"] = limit
        document.metadata["union_before_count"] = len(documents)
    return selected


def confidence_from_documents(documents: list[Document]) -> tuple[float | None, str, str | None]:
    """Return conservative confidence using dense relevance when available."""
    scores = [
        float(doc.metadata["retrieval_relevance"])
        for doc in documents
        if doc.metadata.get("retrieval_relevance") is not None
    ]
    if not scores:
        return None, "unknown", None
    scores.sort(reverse=True)
    top = scores[0]
    gap = top - scores[1] if len(scores) > 1 else top
    if top < 0.3:
        return round(top, 3), "low", f"最高相似度仅 {top:.2f}"
    if top < 0.5 and gap < 0.05:
        return round(top, 3), "uncertain", f"前两位相似度接近（{top:.2f} / {scores[1]:.2f}）"
    return round(top, 3), "high" if top >= 0.7 else "medium", None


def _format_retrieval_error(exc: Exception) -> str:
    message = str(exc)
    if "hnsw" in message.lower():
        return (
            "本地向量库 HNSW 索引无法加载（可能与当前 Chroma/hnswlib 环境不兼容或索引损坏），"
            f"请重新运行 `python ingest.py` 重建知识库。原始错误：{message}"
        )
    return message
