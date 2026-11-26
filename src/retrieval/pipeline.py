"""Top-level retrieval orchestration: caching, rerank budgeting, and multi-query merge."""

from __future__ import annotations

import asyncio
import re
import threading
import time
from typing import Any

from langchain_chroma import Chroma
from langchain_core.documents import Document

from src.async_utils import wait_bounded
from src.embeddings import embedding_lock, get_embeddings
from src.query_expansion import expand_query
from src.reranker import rerank
from src.retrieval._compat import active_settings, clone_documents, document_key, facade_attr
from src.retrieval.domain_rules import _build_entity_plan, build_bm25_queries, domain_rules_enabled
from src.retrieval.fusion import (
    _annotate_union,
    _format_retrieval_error,
    _limit_union_candidates,
    _rrf_fusion,
    _vector_retrieve_documents,
)
from src.retrieval.lexical import _lexical_retrieve_documents_multi
from src.retrieval.ranking import (
    _apply_combined_ranking,
    _entity_diagnostics,
    _select_reranked,
    diversify_by_source,
)


class KnowledgeBaseUnavailable(RuntimeError):
    """Raised when a configured knowledge base cannot be queried."""


_RETRIEVAL_CACHE: dict[tuple[Any, ...], list[Document]] = {}
_RETRIEVAL_CACHE_LOCK = threading.RLock()
_RETRIEVAL_EXECUTION_LOCK = threading.Lock()


def _retrieval_cache_key(question: str, top_k: int | None = None) -> tuple[Any, ...]:
    settings = active_settings()
    normalized = re.sub(r"\s+", " ", str(question or "").strip().lower())
    try:
        client = __import__("chromadb").PersistentClient(path=str(settings.chroma_dir))
        collection_count = int(client.get_collection(settings.collection_name).count())
        client.close()
    except Exception:
        collection_count = -1
    return (
        normalized,
        str(settings.collection_name),
        collection_count,
        int(top_k or settings.retriever_top_k),
        str(settings.chunking_strategy),
        bool(getattr(settings, "retriever_domain_rules_enabled", True)),
        int(getattr(settings, "retriever_rerank_input_cap", 40)),
    )


def select_rerank_pool(
    union: list[Document],
    bm25_plan: list[dict[str, object]],
    rerank_input_cap: int | None,
) -> list[Document]:
    """Bound the Cross-Encoder input pool without mutating union-truncation flags.

    On CPU environments, scoring all 200 RRF union candidates with a cross-encoder
    causes 100-200 s cold latency. Bounding the rerank input (e.g. top 40 while
    preserving guardable entity representatives) keeps latency tractable without
    hiding the full union from diagnostics.
    """
    if not rerank_input_cap or rerank_input_cap <= 0 or len(union) <= rerank_input_cap:
        return list(union)
    prior_flags = {
        document_key(doc): (
            doc.metadata.get("union_truncated"),
            doc.metadata.get("union_limit"),
            doc.metadata.get("union_before_count"),
        )
        for doc in union
    }
    pool = _limit_union_candidates(union, bm25_plan, rerank_input_cap)
    for doc in union:
        prev_trunc, prev_limit, prev_before = prior_flags[document_key(doc)]
        if prev_trunc is None:
            doc.metadata.pop("union_truncated", None)
        else:
            doc.metadata["union_truncated"] = prev_trunc
        if prev_limit is None:
            doc.metadata.pop("union_limit", None)
        else:
            doc.metadata["union_limit"] = prev_limit
        if prev_before is None:
            doc.metadata.pop("union_before_count", None)
        else:
            doc.metadata["union_before_count"] = prev_before
    return pool


def retrieve_documents(question: str, top_k: int | None = None) -> list[Document]:
    settings = active_settings()
    if not settings.chroma_dir.is_dir():
        return []

    cache_key_fn = facade_attr("_retrieval_cache_key", _retrieval_cache_key)
    uncached_fn = facade_attr("_retrieve_documents_uncached", _retrieve_documents_uncached)
    cache = facade_attr("_RETRIEVAL_CACHE", _RETRIEVAL_CACHE)

    cache_key = cache_key_fn(question, top_k)
    with _RETRIEVAL_CACHE_LOCK:
        cached = cache.get(cache_key)
    if cached is not None:
        result = clone_documents(cached)
        for document in result:
            document.metadata.setdefault("retrieval_profile", {})["cache_hit"] = True
        return result

    with _RETRIEVAL_EXECUTION_LOCK:
        with _RETRIEVAL_CACHE_LOCK:
            cached = cache.get(cache_key)
        if cached is not None:
            result = clone_documents(cached)
            for document in result:
                document.metadata.setdefault("retrieval_profile", {})["cache_hit"] = True
            return result
        result = uncached_fn(question, top_k)
        with _RETRIEVAL_CACHE_LOCK:
            cache[cache_key] = clone_documents(result)
            if len(cache) > 128:
                cache.pop(next(iter(cache)))
        return result


def _retrieve_documents_uncached(question: str, top_k: int | None = None) -> list[Document]:
    settings = active_settings()
    started = time.perf_counter()
    profile: dict[str, Any] = {"cache_hit": False, "timeout_stage": None}
    k = max(1, top_k or settings.retriever_top_k)
    dense_k = max(k, settings.retriever_dense_top_k)
    bm25_k = max(k, settings.retriever_bm25_top_k)
    expanded_queries = expand_query(question) if domain_rules_enabled() else [question]
    mode = str(getattr(settings, "retriever_bm25_mode", "conditional")).lower()
    if bool(getattr(settings, "retriever_independent_bm25_queries", False)):
        mode = "independent_all"
    bm25_plan = build_bm25_queries(question)
    entity_plan = _build_entity_plan(question, bm25_plan)
    lexical_queries = [str(item["query"]) for item in bm25_plan]
    lexical_question = " || ".join(lexical_queries)

    lexical_started = time.perf_counter()
    try:
        lexical_documents = _lexical_retrieve_documents_multi(
            bm25_plan,
            original_top_k=bm25_k,
            expansion_top_k=getattr(
                settings,
                "retriever_entity_bm25_top_k",
                getattr(settings, "retriever_expansion_bm25_top_k", 20),
            ),
            max_candidates=getattr(settings, "retriever_union_max_candidates", 200),
        )
        for document in lexical_documents:
            document.metadata["expanded_queries"] = expanded_queries
            document.metadata["lexical_query"] = lexical_question
            document.metadata["bm25_query_mode"] = mode
            document.metadata["bm25_query_plan"] = bm25_plan
    except Exception:
        lexical_documents = []
    profile["bm25_seconds"] = round(time.perf_counter() - lexical_started, 3)

    try:
        with embedding_lock:
            vectorstore = Chroma(
                collection_name=settings.collection_name,
                persist_directory=str(settings.chroma_dir),
                embedding_function=get_embeddings(device=settings.retrieval_device),
            )
            count = vectorstore._collection.count()
            if count == 0:
                return []

            final_k = min(k, count)
            dense_k = min(dense_k, count)
            dense_started = time.perf_counter()
            documents = _vector_retrieve_documents(vectorstore, question, dense_k)
            profile["dense_seconds"] = round(time.perf_counter() - dense_started, 3)
            profile["embedding_seconds"] = profile["dense_seconds"]

            fusion_started = time.perf_counter()
            union = _rrf_fusion(
                vector_documents=documents,
                lexical_documents=lexical_documents,
                top_k=None,
                dense_weight=settings.dense_weight,
                sparse_weight=settings.bm25_weight,
            )
            _annotate_union(union)
            for union_rank, candidate in enumerate(union, 1):
                candidate.metadata["union_rank"] = union_rank
            union_before_count = len(union)
            union = _limit_union_candidates(
                union,
                bm25_plan,
                getattr(settings, "retriever_union_max_candidates", 200),
            )
            profile["fusion_seconds"] = round(time.perf_counter() - fusion_started, 3)

            fallback_ranked = _rrf_fusion(
                documents[:30],
                lexical_documents[:30],
                top_k=None,
                dense_weight=0.55,
                sparse_weight=0.45,
            )
            fallback_ranks = {
                document_key(candidate): rank
                for rank, candidate in enumerate(fallback_ranked, 1)
            }
            for candidate in union:
                candidate.metadata["fallback_rrf_rank"] = fallback_ranks.get(document_key(candidate))

            rerank_input_cap = int(getattr(settings, "retriever_rerank_input_cap", 40) or 0)
            rerank_pool = select_rerank_pool(union, bm25_plan, rerank_input_cap)
            profile["rerank_input_count"] = len(rerank_pool)
            profile["union_count"] = len(union)

            rerank_started = time.perf_counter()
            outcome = rerank(question, rerank_pool)
            profile["reranker_seconds"] = round(time.perf_counter() - rerank_started, 3)
            rerank_status = {
                "status": outcome.status,
                "device": outcome.device,
                "model_path": outcome.model_path,
                "candidate_count": outcome.candidate_count,
                "latency_seconds": outcome.latency_seconds,
                "fallback_reason": outcome.fallback_reason,
                "rerank_input_cap": rerank_input_cap or None,
            }
            if outcome.succeeded:
                ranked_documents = _apply_combined_ranking(question, outcome.documents, True, bm25_plan)
                selected = _select_reranked(ranked_documents, final_k)
            else:
                fallback = _rrf_fusion(
                    documents[:30],
                    lexical_documents[:30],
                    top_k=None,
                    dense_weight=0.55,
                    sparse_weight=0.45,
                )
                fallback = _limit_union_candidates(
                    fallback,
                    bm25_plan,
                    getattr(settings, "retriever_union_max_candidates", 200),
                )
                for rank, document in enumerate(fallback, 1):
                    document.metadata["fallback_rrf_rank"] = rank
                    document.metadata["rerank_rank"] = rank
                _annotate_union(fallback)
                ranked_fallback = _apply_combined_ranking(question, fallback, False, bm25_plan)
                selected = _select_reranked(ranked_fallback, final_k)

            candidate_entity_ids, candidate_chunk_ids, candidate_entity_diagnostics = _entity_diagnostics(
                union, selected, entity_plan
            )
            for index, document in enumerate(selected):
                profile["total_seconds"] = round(time.perf_counter() - started, 3)
                document.metadata["retrieval_profile"] = dict(profile)
                document.metadata["reranker_status"] = rerank_status
                document.metadata["retrieval_candidate_entity_ids"] = candidate_entity_ids
                document.metadata["retrieval_candidate_chunk_ids"] = candidate_chunk_ids
                document.metadata["retrieval_candidate_entities"] = [
                    str(item["entity"]) for item in entity_plan
                ]
                document.metadata["candidate_entity_diagnostics"] = candidate_entity_diagnostics
                document.metadata["bm25_query_plan"] = bm25_plan
                if index == 0:
                    document.metadata["candidate_entity_ids"] = candidate_entity_ids
                document.metadata["retrieval_union_ids"] = [document_key(c) for c in union]
                document.metadata["retrieval_dense_top_ids"] = [document_key(c) for c in documents]
                document.metadata["retrieval_bm25_top_ids"] = [document_key(c) for c in lexical_documents]
                document.metadata["retrieval_bm25_query_hits"] = [
                    {"chunk_id": document_key(c), "hits": c.metadata.get("bm25_query_hits", [])}
                    for c in lexical_documents
                ]
                document.metadata["retrieval_union_before_count"] = union_before_count
                document.metadata["retrieval_union_after_count"] = len(union)
                document.metadata["retrieval_union_truncated"] = any(
                    c.metadata.get("union_truncated", False) for c in union
                )
                document.metadata["retrieval_reranked_top20_ids"] = (
                    [document_key(c) for c in ranked_documents[: settings.retriever_rerank_top_k]]
                    if outcome.succeeded
                    else []
                )
                document.metadata["retrieval_fallback_rrf_top20_ids"] = (
                    [document_key(c) for c in fallback[: settings.retriever_rerank_top_k]]
                    if not outcome.succeeded
                    else []
                )
                document.metadata["retrieval_final_top10_ids"] = [document_key(c) for c in selected]
            return selected
    except Exception as exc:
        original_error = _format_retrieval_error(exc)
        if lexical_documents:
            _annotate_union(lexical_documents)
            rerank_input_cap = int(getattr(settings, "retriever_rerank_input_cap", 40) or 0)
            rerank_pool = select_rerank_pool(lexical_documents, bm25_plan, rerank_input_cap)
            outcome = rerank(question, rerank_pool)
            if outcome.succeeded:
                ranked_documents = _apply_combined_ranking(question, outcome.documents, True, bm25_plan)
                selected = _select_reranked(ranked_documents, k)
                status = {
                    "status": outcome.status,
                    "device": outcome.device,
                    "model_path": outcome.model_path,
                    "candidate_count": outcome.candidate_count,
                    "latency_seconds": outcome.latency_seconds,
                    "fallback_reason": f"vector retrieval failed: {original_error}; lexical rerank used",
                }
                for document in selected:
                    profile["total_seconds"] = round(time.perf_counter() - started, 3)
                    document.metadata["retrieval_profile"] = dict(profile)
                    document.metadata["reranker_status"] = status
                    document.metadata["bm25_query_plan"] = bm25_plan
                    document.metadata["retrieval_candidate_entities"] = [
                        str(item.get("entity")) for item in entity_plan if item.get("entity")
                    ]
                return selected
            selected = diversify_by_source(lexical_documents, k)
            status = {
                "status": "failed",
                "device": None,
                "model_path": str(settings.reranker_model_path) if settings.reranker_model_path else None,
                "candidate_count": len(lexical_documents),
                "latency_seconds": 0.0,
                "fallback_reason": f"vector retrieval failed: {original_error}",
            }
            for document in selected:
                profile["total_seconds"] = round(time.perf_counter() - started, 3)
                document.metadata["retrieval_profile"] = dict(profile)
                document.metadata["reranker_status"] = status
                document.metadata["bm25_query_plan"] = bm25_plan
                document.metadata["retrieval_candidate_entities"] = [
                    str(item.get("entity")) for item in entity_plan if item.get("entity")
                ]
            return selected
        raise KnowledgeBaseUnavailable(
            f"向量检索失败：{original_error}；关键词兜底没有命中资料"
        ) from exc


def _dedupe_chunks(documents: list[Document]) -> list[Document]:
    """Drop duplicate chunks by chunk_id (or object identity as fallback)."""
    seen: set[str] = set()
    result: list[Document] = []
    for doc in documents:
        chunk_id = doc.metadata.get("chunk_id") or str(id(doc))
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        result.append(doc)
    return result


def hybrid_merge(
    original_docs: list[Document],
    subquery_docs: list[Document],
    new_source_cap: int | None = None,
) -> list[Document]:
    """Merge original + sub-query results without diluting the original top-k."""
    settings = active_settings()
    new_source_cap = settings.retriever_new_source_cap if new_source_cap is None else new_source_cap
    original = _dedupe_chunks(original_docs)
    covered_sources = {doc.metadata.get("source", "") for doc in original}
    result = list(original)
    seen_chunks = {doc.metadata.get("chunk_id") or str(id(doc)) for doc in original}
    per_source_count: dict[str, int] = {}

    for doc in subquery_docs:
        chunk_id = doc.metadata.get("chunk_id") or str(id(doc))
        source = doc.metadata.get("source", "")
        if chunk_id in seen_chunks or source in covered_sources:
            continue
        if per_source_count.get(source, 0) >= new_source_cap:
            continue
        seen_chunks.add(chunk_id)
        per_source_count[source] = per_source_count.get(source, 0) + 1
        result.append(doc)
    return result


def cap_per_source(
    documents: list[Document], per_source: int | None = None
) -> list[Document]:
    """Keep at most ``per_source`` chunks per source for multi-source candidates."""
    settings = active_settings()
    distinct_sources = {doc.metadata.get("source", "") for doc in documents}
    if len(distinct_sources) <= 1:
        return documents

    per_source = settings.retriever_grader_cap if per_source is None else per_source
    seen_sources: dict[str, int] = {}
    result: list[Document] = []
    for doc in documents:
        source = doc.metadata.get("source", "")
        if seen_sources.get(source, 0) >= per_source:
            continue
        seen_sources[source] = seen_sources.get(source, 0) + 1
        result.append(doc)
    return result


async def retrieve_documents_multi(
    question: str,
    total_timeout_seconds: float | None = None,
    query_decompose_enabled: bool | None = None,
) -> list[Document]:
    """Hybrid retrieval: original query + optional bounded LLM-decomposed sub-queries."""
    settings = active_settings()
    started = time.perf_counter()
    deadline = (
        time.perf_counter() + float(total_timeout_seconds)
        if total_timeout_seconds and total_timeout_seconds > 0
        else None
    )
    retrieve_fn = facade_attr("retrieve_documents", retrieve_documents)

    async def bounded(operation, *args):
        if deadline is None:
            return await asyncio.to_thread(operation, *args)
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise asyncio.TimeoutError("local retrieval total deadline exceeded")
        return await asyncio.wait_for(asyncio.to_thread(operation, *args), timeout=remaining)

    async def bounded_coroutine(awaitable):
        if deadline is None:
            return await awaitable
        return await wait_bounded(
            awaitable,
            deadline - time.perf_counter(),
            "local retrieval total deadline exceeded",
        )

    original = await bounded(retrieve_fn, question)
    base_profile = dict(original[0].metadata.get("retrieval_profile", {})) if original else {"cache_hit": False}

    def finalize(documents: list[Document]) -> list[Document]:
        result = cap_per_source(documents)
        for document in result:
            profile = document.metadata.setdefault("retrieval_profile", {})
            profile.update(base_profile)
            profile["total_seconds"] = round(time.perf_counter() - started, 3)
        return result

    if query_decompose_enabled is False or (
        query_decompose_enabled is None and not settings.query_decompose_enabled
    ):
        return finalize(original)

    from src.query_decompose import decompose_question

    if deadline is not None and time.perf_counter() >= deadline:
        return finalize(original)
    try:
        sub_queries = await bounded_coroutine(decompose_question(question))
    except Exception:
        return finalize(original)
    if not sub_queries:
        return finalize(original)

    extra: list[Document] = []
    for sub_query in sub_queries:
        if deadline is not None and time.perf_counter() >= deadline:
            break
        try:
            sub_docs = await bounded(
                retrieve_fn, sub_query, settings.retriever_subquery_top_k
            )
            extra.extend(sub_docs)
        except Exception:
            continue
    merged = hybrid_merge(original, extra)
    return finalize(merged)
