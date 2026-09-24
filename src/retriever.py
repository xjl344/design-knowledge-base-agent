"""Lazy local Chroma retriever."""

from __future__ import annotations

import asyncio
import re
import math
import threading
import time
from typing import Any

from langchain_chroma import Chroma
from langchain_core.documents import Document

from config import settings
from src.async_utils import swallow_abandoned_result, wait_bounded
from src.embeddings import embedding_lock, get_embeddings
from src.document_metadata import build_document_metadata, classify_document_for_question
from src.reranker import rerank
from src.query_expansion import expand_query


class KnowledgeBaseUnavailable(RuntimeError):
    """Raised when a configured knowledge base cannot be queried."""


_LEXICAL_INDEX_CACHE: dict[tuple[str, str, int], tuple[list[tuple[str, dict[str, Any], list[str], list[str], dict[str, list[str]]]], float]] = {}
_RETRIEVAL_CACHE: dict[tuple[Any, ...], list[Document]] = {}
_RETRIEVAL_CACHE_LOCK = threading.RLock()
_RETRIEVAL_EXECUTION_LOCK = threading.Lock()


def _retrieval_cache_key(question: str, top_k: int | None = None) -> tuple[Any, ...]:
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
    )


def _clone_documents(documents: list[Document]) -> list[Document]:
    return [Document(page_content=doc.page_content, metadata=dict(doc.metadata)) for doc in documents]


def _lexical_terms(question: str) -> list[str]:
    """Extract small Chinese/Latin terms for the last-resort local fallback."""
    terms: list[str] = []
    for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9._/-]*", question.lower()):
        if len(word) > 1:
            terms.append(word)
    for phrase in re.findall(r"[\u4e00-\u9fff]+", question):
        if len(phrase) <= 8:
            terms.append(phrase)
        terms.extend(phrase[index : index + 2] for index in range(len(phrase) - 1))
    return list(dict.fromkeys(terms))


def _lexical_retrieve_documents(
    question: str, top_k: int | None = None
) -> list[Document]:
    """Query persisted Chroma text and metadata without loading embeddings.

    Including source/title metadata is important for exact identifiers such
    as standard numbers and material grades, whose PDF text layer may be weak
    or inconsistent across files.
    """
    import chromadb

    client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    try:
        collection = client.get_collection(settings.collection_name)
        count = int(collection.count())
        cache_key = (str(settings.chroma_dir), str(settings.collection_name), count)
        cached = _LEXICAL_INDEX_CACHE.get(cache_key)
        if cached is None:
            payload = collection.get(include=["documents", "metadatas"])
        else:
            payload = None
    finally:
        client.close()

    terms = _lexical_terms(question)
    if not terms:
        return []

    if cached is not None:
        rows, average_length = cached
    else:
        rows = []
        for content, metadata in zip(
            payload.get("documents", []) or [], payload.get("metadatas", []) or []
        ):
            metadata = dict(metadata or {})
            metadata_fields = {
                "source": str(metadata.get("source", "")),
                "source_title": str(metadata.get("source_title", "")),
                "title": str(metadata.get("title", "")),
                "section_path": str(metadata.get("section_path", "")),
                "parent_document": str(metadata.get("parent_document", "")),
            }
            metadata_text = " ".join(metadata_fields.values())
            content_text = str(content or "")
            field_terms = {key: _lexical_terms(value) for key, value in metadata_fields.items()}
            content_terms = _lexical_terms(content_text)
            terms_in_doc = content_terms + [term for values in field_terms.values() for term in values]
            rows.append((content_text, metadata, terms_in_doc, content_terms, field_terms))
        average_length = sum(len(terms) for _, _, terms, _, _ in rows) / len(rows) if rows else 0.0
        _LEXICAL_INDEX_CACHE.clear()
        _LEXICAL_INDEX_CACHE[cache_key] = (rows, average_length)
    # Copy metadata per result below; cached rows are immutable retrieval input.
    rows = [
        (content, dict(metadata), terms, content_terms, field_terms)
        for content, metadata, terms, content_terms, field_terms in rows
    ]
    if not rows:
        return []
    document_frequency = {term: sum(term in terms for _, _, terms, _, _ in rows) for term in terms}
    ranked: list[tuple[float, Document]] = []
    for content_text, metadata, doc_terms, content_terms, field_terms in rows:
        metadata_text = " ".join(
            str(metadata.get(key, ""))
            for key in ("source", "source_title", "title", "section_path", "parent_document")
        )
        length = len(doc_terms)
        frequencies = {
            term: content_terms.count(term)
            + settings.retriever_metadata_weight
            * sum(field_terms[field].count(term) for field in ("source", "title", "section_path"))
            + settings.retriever_parent_weight * field_terms["parent_document"].count(term)
            for term in set(doc_terms)
        }
        score = 0.0
        for term in terms:
            tf = frequencies.get(term, 0)
            if not tf:
                continue
            df = document_frequency.get(term, 0)
            idf = math.log(1 + (len(rows) - df + 0.5) / (df + 0.5))
            denominator = tf + 1.5 * (1 - 0.75 + 0.75 * length / max(average_length, 1))
            score += idf * (tf * 2.5) / denominator
        # Exact identifiers are intentionally stronger than broad lexical overlap.
        identifier_terms = re.findall(
            r"(?:gb\s*/?\s*t?\s*[0-9][0-9./-]*|iso\s*[0-9][0-9./-]*|"
            r"tritan|ppsu|pc|pp|304|316|[A-Za-z0-9_]+\.(?:pdf|md|txt))",
            question.lower(),
        )
        metadata_lower = metadata_text.lower()
        content_lower = content_text.lower()
        exact_matches: list[str] = []
        for identifier in identifier_terms:
            compact = re.sub(r"\s+", "", identifier)
            if compact and (compact in re.sub(r"\s+", "", metadata_lower) or compact in re.sub(r"\s+", "", content_lower)):
                score += 2.0
                exact_matches.append(identifier)

        # Standard numbers and material grades often appear in filenames with
        # different separators/order (for example GB/T 26158 vs 26158-gbt).
        # Match their stable numeric/grade core after punctuation is removed.
        searchable_compact = re.sub(r"[^a-z0-9]", "", f"{metadata_lower} {content_lower}")
        stable_identifiers = re.findall(
            r"(?<![a-z0-9])(?:\d{4,}(?:[._/-]\d+)+|tx\d{3,}|\d{4}-\d{2})(?![a-z0-9])",
            question.lower(),
        )
        for identifier in stable_identifiers:
            compact = re.sub(r"[^a-z0-9]", "", identifier)
            if compact and compact in searchable_compact:
                score += 6.0
                exact_matches.append(identifier)
        query_canonical_ids = {
            _canonical_entity_id(entity)
            for entity in _extract_query_entities(question)
            if _canonical_entity_id(entity)
        }
        document_canonical_ids = _canonical_entity_ids_from_text(
            f"{metadata_lower} {content_lower}"
        )
        for canonical_id in sorted(query_canonical_ids & document_canonical_ids):
            score += 6.0
            exact_matches.append(canonical_id)
        if score <= 0:
            continue
        metadata["source_type"] = "local"
        metadata["retrieval_lexical_score"] = round(score, 6)
        metadata["retrieval_exact_matches"] = list(dict.fromkeys(exact_matches))
        metadata["retrieval_exact_match"] = bool(exact_matches)
        document = Document(page_content=content_text, metadata=metadata)
        ranked.append((score, _annotate_document_evidence(question, document)))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return [document for _, document in ranked[: top_k or settings.retriever_top_k]]


def _lexical_retrieve_documents_multi(
    queries: list[dict[str, object]] | list[str],
    original_top_k: int,
    expansion_top_k: int,
    max_candidates: int,
) -> list[Document]:
    """Run original and expanded BM25 queries in independent lexical lanes."""
    lanes = [
        _normalise_bm25_lane(item, index)
        for index, item in enumerate(queries)
        if str(item.get("query", "") if isinstance(item, dict) else item).strip()
    ]
    unique_lanes: list[dict[str, object]] = []
    seen_queries: set[str] = set()
    for lane in lanes:
        query_key = _compact_query_text(str(lane["query"]))
        if query_key in seen_queries:
            continue
        seen_queries.add(query_key)
        unique_lanes.append(lane)
    merged: dict[str, Document] = {}
    best_scores: dict[str, float] = {}
    for query_index, lane in enumerate(unique_lanes):
        query = str(lane["query"])
        limit = original_top_k if query_index == 0 else min(expansion_top_k, original_top_k)
        documents = _lexical_retrieve_documents(query, limit)
        for query_rank, document in enumerate(documents, 1):
            key = _document_key(document)
            score = float(document.metadata.get("retrieval_lexical_score") or 0.0)
            document.metadata.setdefault("bm25_query_hits", []).append({
                "query": query,
                "query_index": query_index,
                "rank": query_rank,
                "score": round(score, 6),
                "kind": lane["kind"],
                "entity": lane["entity"],
                "canonical_id": lane["canonical_id"],
                "origin": lane["origin"],
                "rule_id": lane["rule_id"],
                "guardable": lane["guardable"],
            })
            if key not in merged:
                merged[key] = document
                best_scores[key] = score
            elif score > best_scores.get(key, -1.0):
                previous = merged[key]
                document.metadata.setdefault("bm25_query_hits", []).extend(
                    previous.metadata.get("bm25_query_hits", [])
                )
                document.metadata["retrieval_exact_matches"] = list(dict.fromkeys(
                    list(document.metadata.get("retrieval_exact_matches", []))
                    + list(previous.metadata.get("retrieval_exact_matches", []))
                ))
                document.metadata["retrieval_exact_match"] = bool(
                    document.metadata.get("retrieval_exact_match")
                    or previous.metadata.get("retrieval_exact_match")
                )
                merged[key] = document
                best_scores[key] = score
            else:
                current = merged[key]
                current.metadata.setdefault("bm25_query_hits", []).extend(
                    document.metadata.get("bm25_query_hits", [])
                )
                current.metadata["retrieval_exact_matches"] = list(dict.fromkeys(
                    list(current.metadata.get("retrieval_exact_matches", []))
                    + list(document.metadata.get("retrieval_exact_matches", []))
                ))
                current.metadata["retrieval_exact_match"] = bool(
                    current.metadata.get("retrieval_exact_match")
                    or document.metadata.get("retrieval_exact_match")
                )
    ranked = sorted(
        merged.values(),
        key=lambda doc: (
            float(doc.metadata.get("retrieval_lexical_score") or 0.0),
            -min(
                (int(item.get("rank", 10**9)) for item in doc.metadata.get("bm25_query_hits", [])),
                default=10**9,
            ),
        ),
        reverse=True,
    )
    for rank, document in enumerate(ranked, 1):
        document.metadata["bm25_rank"] = rank
        document.metadata["bm25_queries"] = [
            item.get("query") for item in document.metadata.get("bm25_query_hits", [])
        ]
        entity_ranks: dict[str, int] = {}
        for hit in document.metadata.get("bm25_query_hits", []):
            canonical_id = str(hit.get("canonical_id") or "")
            if not canonical_id:
                continue
            hit_rank = int(hit.get("rank") or 10**9)
            entity_ranks[canonical_id] = min(entity_ranks.get(canonical_id, 10**9), hit_rank)
        document.metadata["entity_bm25_ranks"] = entity_ranks
    return ranked[:max(1, max_candidates)]


def _annotate_document_evidence(
    question: str,
    document: Document,
    relevance: float | None = None,
) -> Document:
    """Attach conservative, query-specific evidence metadata to a document."""
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
        settings.retriever_min_relevance,
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


def _document_key(document: Document) -> str:
    return str(
        document.metadata.get("chunk_id")
        or "|".join(
            (
                str(document.metadata.get("source", "")),
                str(document.metadata.get("page", "")),
                document.page_content[:200],
            )
        )
    )


def _merge_hybrid_documents(
    vector_documents: list[Document],
    lexical_documents: list[Document],
    top_k: int,
) -> list[Document]:
    """Blend exact lexical hits with semantic hits.

    Keep the strongest lexical half first so exact identifiers such as
    standard numbers survive, then use semantic results to preserve broader
    topical coverage.
    """
    result: list[Document] = []
    seen: set[str] = set()
    # Exact identifiers (standard numbers, grade names, and filenames) are
    # stronger evidence than a broad embedding match. Keep all lexical hits
    # within the budget so an explicitly requested source is not displaced by
    # semantically similar documents.
    lexical_budget = min(len(lexical_documents), top_k)

    ordered_documents = (
        lexical_documents[:lexical_budget]
        + vector_documents
        + lexical_documents[lexical_budget:]
    )
    for document in ordered_documents:
        key = _document_key(document)
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
    scores: dict[str, float] = {}
    documents: dict[str, Document] = {}
    for rank, document in enumerate(vector_documents, 1):
        key = _document_key(document)
        documents.setdefault(key, document)
        scores[key] = scores.get(key, 0.0) + dense_weight / (settings.rrf_k + rank)
        documents[key].metadata["dense_rank"] = rank
    for rank, document in enumerate(lexical_documents, 1):
        key = _document_key(document)
        if key not in documents:
            documents[key] = document
        else:
            # Dense owns the shared Document object, but lexical diagnostics
            # and exact-match metadata must survive the merge as well.
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
        scores[key] = scores.get(key, 0.0) + sparse_weight / (settings.rrf_k + rank)
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
                _document_key(document),
            )
            current_key = (
                -_entity_candidate_priority(current, entity_id),
                int(_entity_lane_rank(current, entity_id) or 10**9),
                int(current.metadata.get("bm25_rank") or 10**9),
                int(current.metadata.get("dense_rank") or 10**9),
                _document_key(current),
            ) if current is not None else (10**9, 10**9, 10**9, 10**9, "")
            if current is None or candidate_key < current_key:
                protected_by_entity[entity_id] = document
    # One representative per explicit identifier prevents a single source
    # from consuming the union budget.
    protected_order = list(protected_by_entity.values())
    selected: list[Document] = []
    seen: set[str] = set()
    for document in protected_order[: max(0, limit)] + documents:
        key = _document_key(document)
        if key in seen:
            continue
        seen.add(key)
        selected.append(document)
        if len(selected) >= limit:
            break
    # The protected list is bounded by the number of explicit entities and
    # must survive even when it consumes the configured candidate budget.
    for document in selected:
        document.metadata["union_truncated"] = True
        document.metadata["union_limit"] = limit
        document.metadata["union_before_count"] = len(documents)
    return selected


# Canonical entities are intentionally small and deterministic. They cover
# identifiers that dense retrieval and a generic cross-encoder commonly miss.
_ENTITY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("gb/t 26158-2010", r"(?<![a-z0-9])gb\s*[/_]?\s*t\s*26158(?:[-—_ ]?2010)?(?![a-z0-9])"),
    ("gb 4806.7-2023", r"(?<![a-z0-9])gb[_ ]?4806[.]?7(?:[-—_ ]?2023)?(?![a-z0-9])"),
    ("gb/t 10000-2023", r"(?<![a-z0-9])gb\s*[/_]?\s*t\s*10000(?:[-—_ ]?2023)?(?![a-z0-9])"),
    ("gb/t 16252-2023", r"(?<![a-z0-9])gb\s*[/_]?\s*t\s*16252(?:[-—_ ]?2023)?(?![a-z0-9])"),
    ("gb/t 3326-2016", r"(?<![a-z0-9])gb\s*[/_]?\s*t\s*3326(?:[-—_ ]?2016)?(?![a-z0-9])"),
    ("pp 3486-01", r"(?<![a-z0-9])(?:pp[_ ]*)?3486[-_ ]?01(?![a-z0-9])"),
    ("tritan tx1001", r"(?<![a-z0-9])(?:tritan[_ ]*)?tx[-_ ]?1001(?![a-z0-9])"),
    ("tritan", r"(?<![a-z0-9])tritan(?![a-z0-9])"),
    ("pp", r"(?<![a-z0-9])pp(?![a-z0-9])"),
    ("pc", r"(?<![a-z0-9])pc(?![a-z0-9])"),
    ("ppsu", r"(?<![a-z0-9])ppsu(?![a-z0-9])"),
    ("tds", r"(?<![a-z0-9])tds(?![a-z0-9])|technical\s+data\s+sheet"),
    ("eastman", r"(?<![a-z0-9])eastman(?![a-z0-9])"),
    ("lyondellbasell", r"(?<![a-z0-9])lyondellbasell(?![a-z0-9])"),
)

_STRICT_ENTITY_NAMES = {
    "gb/t 26158-2010", "gb 4806.7-2023", "gb/t 10000-2023",
    "gb/t 16252-2023", "gb/t 3326-2016", "pp 3486-01",
    "tritan tx1001", "tds", "eastman", "lyondellbasell",
}

_FINAL_ENTITY_NAMES = {
    "gb/t 26158-2010", "gb 4806.7-2023", "gb/t 10000-2023",
    "gb/t 16252-2023", "gb/t 3326-2016", "pp 3486-01",
    "tritan tx1001",
}

_LOW_AUTHORITY_SOURCE_MARKERS = (
    "资料来源清单.md",
    "知识库目录说明.md",
    "readme.md",
    "index.md",
    "catalog.md",
    "source_list.md",
)

_BM25_ENTITY_ALIASES = {
    "gb/t 26158-2010": "GB/T 26158-2010",
    "gb 4806.7-2023": "GB 4806.7-2023",
    "gb/t 10000-2023": "GB/T 10000-2023",
    "gb/t 16252-2023": "GB/T 16252-2023",
    "gb/t 3326-2016": "GB/T 3326-2016",
    "pp 3486-01": "PP 3486-01 TDS",
    "tritan tx1001": "Tritan TX1001 TDS",
}

_MATERIAL_TDS_MAPPINGS = {
    "pp": ("pp 3486-01", "LyondellBasell PP 3486-01 TDS", "pp_3486_01"),
    "tritan": ("tritan tx1001", "Eastman Tritan TX1001 TDS", "tritan_tx1001"),
}

_PROCESS_TERM_ALIASES = (
    ("干燥", "drying"),
    ("挤出", "extrusion"),
    ("注塑", "injection molding"),
    ("成型", "molding"),
    ("浇口", "gate"),
    ("壁厚", "wall thickness"),
    ("排气", "venting"),
    ("圆角", "corner radius"),
    ("残余应力", "residual stress"),
)

_PROCESS_PRIOR_MATERIALS = ("pp", "pc", "tritan", "ppsu")


def _process_prior_signal(
    document: Document,
    bm25_plan: list[dict[str, object]],
    base_rank: int,
    rerank_top_k: int,
) -> tuple[bool, int, str | None]:
    """Return a bounded near-cutoff prior for exact process-guide evidence.

    This signal is deliberately metadata-only and requires a deterministic
    process lane. It does not change retrieval ranks or act as a final guard.
    """
    if not bool(getattr(settings, "retriever_process_guide_prior", True)):
        return False, 0, "disabled"
    if base_rank > rerank_top_k + 2:
        return False, 0, "outside_near_cutoff"
    process_lanes = [
        lane for lane in bm25_plan
        if lane.get("kind") == "process" and lane.get("origin") == "deterministic"
    ]
    if not process_lanes or _source_role(document) != "process_guide":
        return False, 0, "no_deterministic_process_lane_or_role_mismatch"

    metadata_text = " ".join(
        str(document.metadata.get(key, ""))
        for key in ("source", "source_title", "title", "parent_document")
    ).casefold()
    if not any(
        re.search(rf"(?<![a-z0-9]){re.escape(material)}(?![a-z0-9])", metadata_text, re.I)
        for material in _PROCESS_PRIOR_MATERIALS
    ):
        return False, 0, "material_not_in_metadata"

    lane_text = " ".join(str(lane.get("query", "")) for lane in process_lanes).casefold()
    high_signal_terms = [
        english.casefold()
        for _, english in _PROCESS_TERM_ALIASES
        if english.casefold() in lane_text
    ]
    matched_terms = [
        term
        for term in dict.fromkeys(
            english.casefold() for _, english in _PROCESS_TERM_ALIASES
        )
        if term in metadata_text
    ]
    overlapping_terms = [term for term in matched_terms if term in high_signal_terms]
    if len(matched_terms) < 2 or not overlapping_terms:
        return False, len(overlapping_terms), "fewer_than_two_metadata_process_terms"
    return True, len(matched_terms), "deterministic_process_lane_exact_metadata_match"


def _anthropometry_prior_signal(
    question: str,
    document: Document,
    base_rank: int,
    rerank_top_k: int,
) -> tuple[bool, str]:
    """Identify a narrow evidence-priority case for under-specified child sizes.

    This is intentionally not an entity guard: it only helps a substantive
    child anthropometry chunk compete near the rerank cutoff when the user
    asks for an exact value that the corpus explicitly cannot provide.
    """
    if not bool(getattr(settings, "retriever_anthropometry_prior", True)):
        return False, "disabled"
    if base_rank > rerank_top_k + 15:
        return False, "outside_bounded_window"
    text = str(question or "")
    child_scope = any(token in text for token in ("儿童", "孩子", "幼儿", "未成年人"))
    exact_request = any(token in text for token in ("最佳", "精确", "准确", "确定值")) and any(
        token in text for token in ("握持直径", "握持", "手部尺寸", "手部", "直径")
    )
    evidence_gap = any(token in text for token in ("只有", "缺少", "没有", "无法给出", "知识库"))
    if not (child_scope and exact_request and evidence_gap):
        return False, "query_intent_not_matched"
    if _source_role(document) != "anthropometry":
        return False, "source_role_mismatch"
    metadata_text = " ".join(
        str(document.metadata.get(key, ""))
        for key in ("source", "source_title", "title", "parent_document")
    ).casefold()
    content_text = str(document.page_content or "").casefold()
    # Do not boost every anthropometry standard: this prior is for a direct
    # child-study source, identifiable from path/title metadata. Body text is
    # deliberately excluded so a generic standard citing children cannot
    # receive the same boost.
    child_evidence = any(token in metadata_text for token in (
        "儿童", "children", "child", "/children/", "\\children\\",
    ))
    if not child_evidence:
        return False, "no_child_anthropometry_evidence"
    return True, "exact_value_request_with_corpus_evidence_gap"


def _compact_entity_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _compact_query_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _normalise_bm25_lane(item: dict[str, object] | str, index: int = 0) -> dict[str, object]:
    if isinstance(item, dict):
        query = str(item.get("query", "")).strip()
        entity = item.get("entity")
        canonical_id = item.get("canonical_id") or (
            _canonical_entity_id(str(entity)) if entity else None
        )
        origin = str(item.get("origin") or ("explicit" if index == 0 else "expanded"))
        kind = str(item.get("kind") or ("original" if index == 0 else "entity"))
        guardable = bool(item.get("guardable", False))
        return {
            "query": query,
            "kind": kind,
            "entity": entity,
            "canonical_id": canonical_id,
            "origin": origin,
            "rule_id": item.get("rule_id"),
            "guardable": guardable,
        }
    return {
        "query": str(item).strip(),
        "kind": "original" if index == 0 else "entity",
        "entity": None,
        "canonical_id": None,
        "origin": "explicit" if index == 0 else "expanded",
        "rule_id": None,
        "guardable": False,
    }


def _make_bm25_lane(
    query: str,
    *,
    kind: str,
    entity: str | None,
    origin: str,
    rule_id: str | None,
    guardable: bool,
    canonical_id: str | None = None,
) -> dict[str, object]:
    canonical_id = canonical_id or (_canonical_entity_id(entity) if entity else None)
    return {
        "query": query,
        "kind": kind,
        "entity": entity,
        "canonical_id": canonical_id,
        "origin": origin,
        "rule_id": rule_id,
        "guardable": bool(guardable and canonical_id),
    }


def _canonical_entity_id(entity: str) -> str | None:
    """Return a stable ID for standards and material grades."""
    compact = _compact_entity_text(entity)
    if compact in {"gb480672023", "gb48067"}:
        return "gb_4806_7_2023"
    standard = re.search(r"(?:gbt|gb)(\d{4,6})(\d{4})$", compact)
    if standard:
        return f"gb_{standard.group(1)}_{standard.group(2)}"
    reverse_standard = re.search(r"(\d{4,6})(20\d{2})gbt$", compact)
    if reverse_standard:
        return f"gb_{reverse_standard.group(1)}_{reverse_standard.group(2)}"
    pp = re.search(r"(?:pp)?(\d{3,5})0?1$", compact)
    if compact in {"pp348601", "348601"} or pp and pp.group(1) == "3486":
        return "pp_3486_01"
    if compact in {"tritanx1001", "tx1001"}:
        return "tritan_tx1001"
    return None


def _canonical_entity_ids_from_text(value: str) -> set[str]:
    text = str(value or "").casefold()
    result: set[str] = set()
    patterns = (
        r"gb\s*[/_]?\s*t\s*(\d{4,6})\s*[-—_ ]?\s*(20\d{2})",
        r"gb\s*[_ ]?\s*(4806)[._]?7\s*[-—_ ]?\s*(20\d{2})?",
        r"(\d{4,6})\s*[-_ ]\s*(20\d{2})\s*[-_ ]?\s*gbt",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            groups = match.groups()
            if groups[0] == "4806":
                result.add(f"gb_4806_7_{groups[1] or '2023'}")
            else:
                result.add(f"gb_{groups[0]}_{groups[1]}")
    if re.search(r"(?:pp[ _-]*)?3486[ _-]*01", text, re.I):
        result.add("pp_3486_01")
    if re.search(r"(?:tritan[ _-]*)?tx[ _-]*1001", text, re.I):
        result.add("tritan_tx1001")
    return result


def _extract_query_entities(question: str) -> list[str]:
    """Extract standards, grades, materials and domain roles deterministically."""
    text = str(question or "")
    lowered = text.casefold()
    found = [canonical for canonical, pattern in _ENTITY_PATTERNS if re.search(pattern, lowered, re.I)]
    if any(token in text for token in ("食品接触", "食品安全", "合规")):
        found.append("food_contact")
    if any(token in text for token in ("注塑", "加工", "干燥", "浇口", "排气")):
        found.append("process_guide")
    if any(token in text for token in ("儿童", "幼儿", "未成年人")):
        found.append("anthropometry_minors")
    return list(dict.fromkeys(found))


def build_bm25_queries(question: str) -> list[dict[str, object]]:
    """Build a bounded, structured BM25 query plan."""
    text = str(question or "").strip()
    if not text:
        return []
    original = _make_bm25_lane(
        text,
        kind="original",
        entity=None,
        origin="explicit",
        rule_id=None,
        guardable=False,
    )
    mode = str(getattr(settings, "retriever_bm25_mode", "conditional")).lower()
    legacy_independent = bool(getattr(settings, "retriever_independent_bm25_queries", False))
    if mode == "original_only":
        return [original]
    if mode == "independent_all" or legacy_independent:
        lanes = [original]
        for query in expand_query(text) or [text]:
            if _compact_query_text(query) == _compact_query_text(text):
                continue
            lanes.append(_make_bm25_lane(
                query,
                kind="entity",
                entity=None,
                origin="expanded",
                rule_id="full_independent_bm25",
                guardable=False,
            ))
        return lanes

    explicit_lanes: list[dict[str, object]] = []
    deterministic_standard_lanes: list[dict[str, object]] = []
    material_lanes: list[dict[str, object]] = []
    process_lanes: list[dict[str, object]] = []

    explicit_entities = _extract_query_entities(text)
    for entity in explicit_entities:
        if entity not in _BM25_ENTITY_ALIASES or not _canonical_entity_id(entity):
            continue
        explicit_lanes.append(_make_bm25_lane(
            _BM25_ENTITY_ALIASES[entity],
            kind="entity",
            entity=entity,
            origin="explicit",
            rule_id="explicit_identifier",
            guardable=True,
        ))

    child_scope = (
        any(token in text for token in ("儿童", "孩子", "幼儿", "未成年人", "未成年"))
        or bool(re.search(r"\d+\s*(?:[-—~至到]\s*\d+\s*)?岁", text))
    )
    child_dimension = any(
        token in text for token in ("手部", "手掌", "手指", "握", "直径", "尺寸")
    )
    if child_scope and child_dimension:
        deterministic_standard_lanes.append(_make_bm25_lane(
            "GB/T 26158-2010",
            kind="entity",
            entity="gb/t 26158-2010",
            origin="deterministic",
            rule_id="child_hand_dimensions",
            guardable=True,
        ))

    cup_scope = any(
        token in text for token in ("水杯", "饮用容器", "杯体", "食品容器", "可重复使用")
    )
    compliance_scope = any(
        token in text for token in ("材料", "材质", "筛选", "选择", "安全", "合规", "食品接触")
    )
    if cup_scope and compliance_scope:
        deterministic_standard_lanes.append(_make_bm25_lane(
            "GB 4806.7-2023",
            kind="entity",
            entity="gb 4806.7-2023",
            origin="deterministic",
            rule_id="cup_food_contact_compliance",
            guardable=True,
        ))

    materials: list[str] = []
    if re.search(r"(?<![A-Za-z0-9])PP(?![A-Za-z0-9])", text, re.I):
        materials.append("pp")
    if re.search(r"(?<![A-Za-z0-9])PC(?![A-Za-z0-9])", text, re.I):
        materials.append("pc")
    if re.search(r"(?<![A-Za-z0-9])Tritan(?![A-Za-z0-9])", text, re.I):
        materials.append("tritan")
    if re.search(r"(?<![A-Za-z0-9])PPSU(?![A-Za-z0-9])", text, re.I):
        materials.append("ppsu")
    if "玻璃" in text:
        materials.append("glass")
    decision_query = len(set(materials)) >= 2 and any(
        token in text for token in ("比较", "对比", "选择", "筛选", "哪个", "决定", "决策")
    )
    if decision_query:
        for material in list(dict.fromkeys(materials)):
            mapping = _MATERIAL_TDS_MAPPINGS.get(material)
            if not mapping:
                continue
            entity, query, mapped_canonical_id = mapping
            material_lanes.append(_make_bm25_lane(
                query,
                kind="entity",
                entity=entity,
                origin="deterministic",
                rule_id=f"material_comparison_{material}_tds",
                guardable=True,
                canonical_id=mapped_canonical_id,
            ))

    process_terms = [english for chinese, english in _PROCESS_TERM_ALIASES if chinese in text]
    process_materials = [item for item in materials if item in {"pp", "pc", "tritan", "ppsu"}]
    if process_materials and process_terms:
        material_text = " ".join(dict.fromkeys(process_materials))
        process_query = f"{material_text} {' '.join(dict.fromkeys(process_terms))} processing guide"
        process_lanes.append(_make_bm25_lane(
            process_query,
            kind="process",
            entity="process_guide",
            origin="deterministic",
            rule_id="material_process_guide",
            guardable=False,
        ))

    candidates = explicit_lanes + deterministic_standard_lanes + material_lanes + process_lanes
    limit = max(0, int(getattr(settings, "retriever_max_entity_bm25_queries", 3)))
    lanes = [original]
    seen_ids: set[str] = set()
    seen_queries = {_compact_query_text(text)}
    # Canonical deterministic lanes always outrank a process lane. A lane
    # without a canonical ID is still retrievable, but never consumes a slot
    # ahead of a concrete standard or grade when the cap is tight.
    candidates.sort(key=lambda lane: (
        0 if lane.get("origin") == "explicit" else
        1 if lane.get("canonical_id") and lane.get("kind") == "entity" else
        2 if lane.get("kind") == "entity" else 3,
    ))
    for lane in candidates:
        canonical_id = str(lane.get("canonical_id") or "")
        query_key = _compact_query_text(str(lane["query"]))
        if query_key in seen_queries or canonical_id and canonical_id in seen_ids:
            continue
        lanes.append(lane)
        seen_queries.add(query_key)
        if canonical_id:
            seen_ids.add(canonical_id)
        if len(lanes) >= limit + 1:
            break
    return lanes


def _candidate_entity_text(document: Document) -> str:
    metadata = document.metadata
    return " ".join(
        str(metadata.get(key, ""))
        for key in ("source", "source_title", "title", "section_path", "parent_document", "material_grade")
    ) + " " + str(document.page_content or "")


def _entity_match_score(query_entities: list[str], document: Document) -> tuple[float, list[str]]:
    if not query_entities:
        return 0.0, []
    metadata = document.metadata
    fields = {
        "source": str(metadata.get("source", "")).casefold(),
        "title": str(metadata.get("title") or metadata.get("source_title", "")).casefold(),
        "parent_document": str(metadata.get("parent_document", "")).casefold(),
        "section_path": str(metadata.get("section_path", "")).casefold(),
        "content": str(document.page_content or "").casefold(),
    }
    text = " ".join(fields.values())
    compact = _compact_entity_text(text)
    matched: list[str] = []
    scores: list[float] = []
    patterns = dict(_ENTITY_PATTERNS)
    for entity in query_entities:
        if entity == "food_contact":
            hit = any(token in text for token in ("食品接触", "食品安全", "food contact", "gb 4806.7", "gb4806.7"))
            location = "content" if hit else None
        elif entity == "process_guide":
            hit = any(token in text for token in ("processing", "mold", "mould", "drying", "注塑", "加工", "工艺"))
            location = "content" if hit else None
        elif entity == "anthropometry_minors":
            hit = any(token in text for token in ("儿童", "未成年人", "children", "anthropometry", "26158"))
            location = "content" if hit else None
        else:
            pattern = patterns.get(entity)
            location = next((name for name in ("source", "title", "parent_document", "section_path", "content")
                             if pattern and re.search(pattern, fields[name], re.I)), None)
            hit = location is not None
            if not hit and entity.startswith("gb/"):
                # Filenames may reverse the standard identity, e.g. 26158-2010-gbt.
                number = re.search(r"(\d{4,})", entity)
                year = re.search(r"(20\d{2})", entity)
                hit = bool(number and year and number.group(1) + year.group(1) in compact)
                location = "source" if hit else None
            if not hit and _compact_entity_text(entity) in compact:
                hit, location = True, "content"
        if hit:
            matched.append(entity)
            if location in {"source", "title"}:
                scores.append(1.0)
            elif location in {"parent_document", "section_path"}:
                scores.append(0.8)
            elif entity in _STRICT_ENTITY_NAMES and entity not in {"tds", "eastman", "lyondellbasell"}:
                scores.append(0.6)
            else:
                scores.append(0.3 if entity not in _STRICT_ENTITY_NAMES else 0.6)
    if not scores:
        return 0.0, matched
    # Preserve the strength of one exact identifier even when expansion adds
    # several unrelated entities to the query.
    weighted = max(scores) + 0.15 * max(0, len(scores) - 1)
    return min(1.0, weighted), matched


def _source_role(document: Document) -> str:
    explicit_role = str(document.metadata.get("source_role", "")).casefold()
    priority_enabled = bool(getattr(settings, "retriever_source_role_priority", True))
    if priority_enabled and explicit_role in {
        "standard", "anthropometry", "material_tds", "process_guide",
        "internal_rule", "paper", "reference_handbook",
    }:
        return explicit_role
    category = str(document.metadata.get("source_category", "")).casefold()
    # Ingest-time classification is authoritative. A processing guide may
    # cite a GB standard and an internal rule may mention TDS, but those words
    # must not change the document's actual source role.
    valid_categories = {
        "standard", "anthropometry", "material_tds", "process_guide",
        "internal_rule", "paper", "reference_handbook",
    }
    if priority_enabled and category in valid_categories:
        return category
    metadata = document.metadata
    text = " ".join(
        str(metadata.get(key, ""))
        for key in ("source", "source_title", "title", "parent_document")
    ).casefold()
    if re.search(r"tds|technical[ _-]*data|data[ _-]*sheet|datasheet", text):
        return "material_tds"
    if any(token in text for token in ("processing", "mold", "mould", "drying", "extrusion", "工艺", "注塑")):
        return "process_guide"
    if re.search(r"(?<![a-z0-9])gb(?:[/_ ]?t)?(?![a-z0-9])|(?<![a-z0-9])iso(?![a-z0-9])", text):
        return "standard"
    if any(token in text for token in ("anthropometry", "人体", "手部", "握力")):
        return "anthropometry"
    # Content is only a final fallback because citations inside a guide must
    # not override a reliable filename/path classification.
    content = str(document.page_content or "").casefold()
    if re.search(r"tds|technical\s+data\s+sheet", content):
        return "material_tds"
    if any(token in content for token in ("processing guide", "drying guide", "加工指南", "工艺指南")):
        return "process_guide"
    return "unknown"


def _query_roles(entities: list[str]) -> set[str]:
    roles: set[str] = set()
    if any(entity.startswith("gb/") or entity.startswith("gb ") for entity in entities):
        roles.add("standard")
    if any(entity in {"pp", "pc", "tritan", "ppsu", "pp 3486-01", "tritan tx1001", "tds", "eastman", "lyondellbasell", "food_contact"} for entity in entities):
        roles.add("material_tds")
    if "food_contact" in entities:
        roles.add("standard")
    if "process_guide" in entities:
        roles.add("process_guide")
    if "anthropometry_minors" in entities:
        roles.add("anthropometry")
    return roles


def _entity_lane_rank(document: Document, canonical_id: str) -> int | None:
    ranks = [
        int(hit.get("rank") or 10**9)
        for hit in document.metadata.get("bm25_query_hits", [])
        if hit.get("canonical_id") == canonical_id
    ]
    return min(ranks) if ranks else None


def _is_low_authority_document(document: Document) -> bool:
    """Return whether metadata identifies an index/catalog document.

    This deliberately inspects only path/title metadata, never chunk content:
    a technical document may mention an index or catalog in its body.
    """
    fields = (
        document.metadata.get("source"),
        document.metadata.get("parent_document"),
        document.metadata.get("source_title"),
        document.metadata.get("title"),
    )
    markers = {marker.casefold().replace("\\", "/") for marker in _LOW_AUTHORITY_SOURCE_MARKERS}
    for value in fields:
        normalized = str(value or "").strip().replace("\\", "/").casefold()
        if not normalized:
            continue
        basename = normalized.rsplit("/", 1)[-1]
        if basename in markers:
            return True
    return False


def _entity_match_locations(document: Document, canonical_id: str) -> set[str]:
    """Return metadata/content fields that contain a canonical entity."""
    metadata = document.metadata
    fields = {
        "source": str(metadata.get("source", "")),
        "title": str(metadata.get("title") or metadata.get("source_title", "")),
        "parent_document": str(metadata.get("parent_document", "")),
        "section_path": str(metadata.get("section_path", "")),
        "content": str(document.page_content or ""),
    }
    return {
        field
        for field, value in fields.items()
        if canonical_id in _canonical_entity_ids_from_text(value)
    }


def _entity_role_compatible(canonical_id: str, document: Document) -> bool:
    role = _source_role(document)
    if canonical_id.startswith("gb_"):
        allowed = {
            "gb_26158_2010": {"standard", "anthropometry"},
            "gb_4806_7_2023": {"standard"},
        }.get(canonical_id, {"standard"})
        fallback = {"reference_handbook", "process_guide"}
    else:
        allowed = {"material_tds"}
        fallback = {"standard", "process_guide", "reference_handbook"}
    return role in allowed or role in fallback


def _entity_candidate_priority(document: Document, canonical_id: str) -> int:
    """Score authority of a canonical entity match; lower-authority is -1."""
    if _is_low_authority_document(document):
        return -1
    locations = _entity_match_locations(document, canonical_id)
    if not locations or not _entity_role_compatible(canonical_id, document):
        return -1
    role = _source_role(document)
    source_or_title = bool(locations & {"source", "title"})
    if canonical_id.startswith("gb_"):
        if role == "standard":
            return 100 if "source" in locations else 95 if source_or_title else 90
        if canonical_id == "gb_26158_2010" and role == "anthropometry":
            return 88 if "source" in locations else 84 if source_or_title else 80
        return 70 if source_or_title else 60
    if role == "material_tds":
        return 100 if source_or_title else 90
    return 70 if source_or_title else 60


def _build_entity_plan(
    question: str, bm25_plan: list[dict[str, object]]
) -> list[dict[str, object]]:
    """Combine explicit, deterministic and weak expanded entities for ranking."""
    plan: list[dict[str, object]] = []
    seen_names: set[str] = set()
    seen_ids: set[str] = set()

    lane_by_id = {
        str(item.get("canonical_id")): item
        for item in bm25_plan
        if item.get("canonical_id")
    }
    for entity in _extract_query_entities(question):
        canonical_id = _canonical_entity_id(entity)
        lane = lane_by_id.get(str(canonical_id)) if canonical_id else None
        row = {
            "entity": entity,
            "canonical_id": canonical_id,
            "origin": "explicit",
            "rule_id": "explicit_query",
            "guardable": bool(lane and lane.get("origin") == "explicit" and lane.get("guardable")),
        }
        plan.append(row)
        seen_names.add(entity)
        if canonical_id:
            seen_ids.add(canonical_id)

    for lane in bm25_plan:
        entity = str(lane.get("entity") or "")
        if not entity or lane.get("origin") != "deterministic":
            continue
        canonical_id = str(lane.get("canonical_id") or "") or None
        if entity in seen_names or canonical_id and canonical_id in seen_ids:
            continue
        plan.append({
            "entity": entity,
            "canonical_id": canonical_id,
            "origin": "deterministic",
            "rule_id": lane.get("rule_id"),
            "guardable": bool(lane.get("guardable") and canonical_id),
        })
        seen_names.add(entity)
        if canonical_id:
            seen_ids.add(canonical_id)

    expanded_text = " || ".join(expand_query(question) or [])
    for entity in _extract_query_entities(expanded_text):
        canonical_id = _canonical_entity_id(entity)
        if entity in seen_names or canonical_id and canonical_id in seen_ids:
            continue
        plan.append({
            "entity": entity,
            "canonical_id": canonical_id,
            "origin": "expanded",
            "rule_id": "query_expansion",
            "guardable": False,
        })
        seen_names.add(entity)
        if canonical_id:
            seen_ids.add(canonical_id)
    return plan


def _normalise_scores(documents: list[Document], key: str) -> dict[str, float]:
    values = {_document_key(doc): float(doc.metadata[key]) for doc in documents if doc.metadata.get(key) is not None}
    if not values:
        return {}
    low, high = min(values.values()), max(values.values())
    if math.isclose(low, high):
        return {item: 1.0 for item in values}
    return {item: (value - low) / (high - low) for item, value in values.items()}


def _apply_combined_ranking(
    question: str,
    documents: list[Document],
    reranker_success: bool,
    query_plan: list[dict[str, object]] | str | None = None,
) -> list[Document]:
    """Apply semantic/entity/RRF composition to the complete union."""
    bm25_plan = query_plan if isinstance(query_plan, list) else build_bm25_queries(question)
    entity_plan = _build_entity_plan(question, bm25_plan)
    if isinstance(query_plan, str):
        known = {str(item["entity"]) for item in entity_plan}
        for entity in _extract_query_entities(query_plan):
            if entity not in known:
                entity_plan.append({
                    "entity": entity,
                    "canonical_id": _canonical_entity_id(entity),
                    "origin": "expanded",
                    "rule_id": "legacy_entity_question",
                    "guardable": False,
                })
    all_entities = [str(item["entity"]) for item in entity_plan]
    explicit_entities = [
        str(item["entity"]) for item in entity_plan if item.get("origin") == "explicit"
    ]
    deterministic_entities = [
        str(item["entity"]) for item in entity_plan if item.get("origin") == "deterministic"
    ]
    expanded_entities = [
        str(item["entity"]) for item in entity_plan if item.get("origin") == "expanded"
    ]
    weak_entities = list(dict.fromkeys(deterministic_entities + expanded_entities))
    roles = _query_roles(explicit_entities + deterministic_entities)
    guardable_ids = [
        str(item["canonical_id"])
        for item in entity_plan
        if item.get("guardable") and item.get("canonical_id")
    ]
    mode = getattr(settings, "retriever_ranking_mode", "reranker_entity_rrf")
    rerank_norm = _normalise_scores(documents, "rerank_score") if reranker_success else {}
    rrf_norm = _normalise_scores(documents, "rrf_score")
    rerank_weight = float(getattr(settings, "retriever_rerank_weight", 0.82))
    explicit_weight = float(getattr(settings, "retriever_explicit_entity_weight", 0.10))
    expanded_weight = float(getattr(settings, "retriever_expanded_entity_weight", 0.03))
    rrf_weight = float(getattr(settings, "retriever_rrf_weight", 0.05))
    for document in documents:
        if document.metadata.get("rerank_score") is not None and document.metadata.get("model_rerank_rank") is None:
            document.metadata["model_rerank_rank"] = document.metadata.get("rerank_rank")
        explicit_score, explicit_matched = _entity_match_score(explicit_entities, document)
        weak_score, weak_matched = _entity_match_score(weak_entities, document)
        deterministic_matched = [item for item in weak_matched if item in deterministic_entities]
        expanded_matched = [item for item in weak_matched if item in expanded_entities]
        entity_score = max(explicit_score, weak_score)
        matched = list(dict.fromkeys(explicit_matched + weak_matched))
        role = _source_role(document)
        key = _document_key(document)
        semantic = rerank_norm.get(key, rrf_norm.get(key, 0.0))
        if mode == "reranker_only":
            combined = semantic
        elif mode == "reranker_entity":
            combined = 0.90 * semantic + explicit_weight * explicit_score
        else:
            combined = (
                rerank_weight * semantic
                + explicit_weight * explicit_score
                + expanded_weight * weak_score
                + rrf_weight * rrf_norm.get(key, 0.0)
            )
        exact_ids = _canonical_entity_ids_from_text(_candidate_entity_text(document))
        matched_ids = sorted(
            exact_ids & {
                str(item["canonical_id"])
                for item in entity_plan
                if item.get("canonical_id")
            }
        )
        guardable_candidate_ids = sorted(
            canonical_id
            for canonical_id in guardable_ids
            if canonical_id in exact_ids
            and _entity_lane_rank(document, canonical_id) is not None
            and _entity_candidate_priority(document, canonical_id) >= 0
        )
        document.metadata.update({
            "query_entities": all_entities,
            "entity_plan": entity_plan,
            "explicit_entities": explicit_entities,
            "deterministic_entities": deterministic_entities,
            "expanded_entities": expanded_entities,
            "candidate_entities": matched,
            "explicit_candidate_entities": explicit_matched,
            "deterministic_candidate_entities": deterministic_matched,
            "expanded_candidate_entities": expanded_matched,
            "explicit_entity_ids": sorted({
                str(item["canonical_id"]) for item in entity_plan
                if item.get("origin") == "explicit" and item.get("canonical_id")
            }),
            "deterministic_entity_ids": sorted({
                str(item["canonical_id"]) for item in entity_plan
                if item.get("origin") == "deterministic" and item.get("canonical_id")
            }),
            "expanded_entity_ids": sorted({
                str(item["canonical_id"]) for item in entity_plan
                if item.get("origin") == "expanded" and item.get("canonical_id")
            }),
            "candidate_entity_ids": matched_ids,
            "guardable_candidate_entity_ids": guardable_candidate_ids,
            "entity_match_score": round(entity_score, 6),
            "explicit_entity_score": round(explicit_score, 6),
            "deterministic_entity_score": round(
                _entity_match_score(deterministic_entities, document)[0], 6
            ),
            "expanded_entity_score": round(weak_score, 6),
            "source_role": role,
            "query_role_match": bool(roles and role in roles),
            "combined_score": round(combined, 6),
            "base_combined_score": round(combined, 6),
            "process_exact_match": False,
            "process_exact_term_count": 0,
            "process_prior_applied": False,
            "process_prior_reason": None,
            "process_prior_delta": 0.0,
            "entity_guard_triggered": False,
            "entity_final_reserve_eligible": bool(guardable_candidate_ids),
        })
    base_ranked = sorted(
        documents,
        key=lambda doc: (
            float(doc.metadata.get("base_combined_score", 0.0)),
            float(doc.metadata.get("rerank_score", -1e9)),
        ),
        reverse=True,
    )
    base_ranks = {
        _document_key(document): rank
        for rank, document in enumerate(base_ranked, 1)
    }
    process_bonus = float(getattr(settings, "retriever_process_guide_prior_bonus", 0.025))
    anthropometry_bonus = float(getattr(settings, "retriever_anthropometry_prior_bonus", 0.08))
    for document in documents:
        base_rank = base_ranks[_document_key(document)]
        matched, term_count, reason = _process_prior_signal(
            document,
            bm25_plan,
            base_rank,
            int(getattr(settings, "retriever_rerank_top_k", 20)),
        )
        delta = process_bonus if matched else 0.0
        anthropometry_matched, anthropometry_reason = _anthropometry_prior_signal(
            question,
            document,
            base_rank,
            int(getattr(settings, "retriever_rerank_top_k", 20)),
        )
        anthropometry_delta = anthropometry_bonus if anthropometry_matched else 0.0
        document.metadata.update({
            "process_exact_match": matched,
            "process_exact_term_count": term_count,
            "process_prior_applied": matched,
            "process_prior_reason": reason,
            "process_prior_delta": round(delta, 6),
            "anthropometry_prior_applied": anthropometry_matched,
            "anthropometry_prior_reason": anthropometry_reason,
            "anthropometry_prior_delta": round(anthropometry_delta, 6),
            "combined_score": round(
                float(document.metadata.get("base_combined_score", 0.0)) + delta + anthropometry_delta,
                6,
            ),
        })
    # Catalog/source-list chunks are useful as ordinary fallback evidence,
    # but should not outrank substantive technical documents merely because
    # they repeat many entity names. Apply a bounded local penalty only when
    # the current union contains at least one authoritative alternative.
    authority_enabled = bool(getattr(settings, "retriever_low_authority_prior", True))
    authority_penalty = float(getattr(settings, "retriever_low_authority_penalty", 0.12))
    has_authoritative_candidate = any(
        not _is_low_authority_document(document) for document in documents
    )
    for document in documents:
        is_low_authority = _is_low_authority_document(document)
        apply_authority_penalty = (
            authority_enabled
            and authority_penalty > 0
            and is_low_authority
            and has_authoritative_candidate
        )
        penalty = authority_penalty if apply_authority_penalty else 0.0
        document.metadata.update({
            "low_authority_penalty_applied": apply_authority_penalty,
            "low_authority_penalty_delta": round(-penalty, 6),
            "authority_prior_reason": (
                "low_authority_document_with_authoritative_alternative"
                if apply_authority_penalty
                else "not_applied"
            ),
            "combined_score": round(
                float(document.metadata.get("combined_score", 0.0)) - penalty,
                6,
            ),
        })
    # The fixed penalty alone is sensitive to the score spread of each query.
    # Keep catalog/index chunks retrievable, but cap them just below the best
    # substantive candidate so they cannot become the primary evidence.
    authoritative_scores = [
        float(document.metadata.get("combined_score", 0.0))
        for document in documents
        if not _is_low_authority_document(document)
    ]
    authority_ceiling = max(authoritative_scores) - 0.001 if authoritative_scores else None
    for document in documents:
        current_score = float(document.metadata.get("combined_score", 0.0))
        capped = bool(
            authority_enabled
            and authority_ceiling is not None
            and _is_low_authority_document(document)
            and current_score >= authority_ceiling
        )
        if capped:
            document.metadata["combined_score"] = round(authority_ceiling, 6)
        document.metadata["low_authority_score_capped"] = capped
        document.metadata["low_authority_score_ceiling"] = (
            round(authority_ceiling, 6) if capped else None
        )
    ranked = sorted(documents, key=lambda doc: (float(doc.metadata.get("combined_score", 0.0)), float(doc.metadata.get("rerank_score", -1e9))), reverse=True)
    # Deterministic and explicit identifiers may reserve one representative.
    # Insertions begin at effective rank 15 so the semantic Top-14 is stable.
    if reranker_success and getattr(settings, "retriever_entity_guard", True) and len(ranked) > settings.retriever_rerank_top_k:
        limit = settings.retriever_rerank_top_k
        guarded_entities: set[str] = set()
        used_candidates: set[str] = set()
        for canonical_id in guardable_ids:
            if canonical_id in guarded_entities:
                continue
            if any(
                canonical_id in set(item.metadata.get("guardable_candidate_entity_ids", []))
                for item in ranked[:limit]
            ):
                continue
            eligible = [
                item for item in ranked[limit:]
                if canonical_id in set(item.metadata.get("guardable_candidate_entity_ids", []))
                and _document_key(item) not in used_candidates
            ]
            if not eligible:
                continue
            candidate = min(eligible, key=lambda item: (
                -_entity_candidate_priority(item, canonical_id),
                int(_entity_lane_rank(item, canonical_id) or 10**9),
                int(item.metadata.get("bm25_rank") or 10**9),
                int(item.metadata.get("model_rerank_rank") or 10**9),
                _document_key(item),
            ))
            candidate.metadata["entity_guard_triggered"] = True
            candidate.metadata["guarded_entity_id"] = canonical_id
            candidate.metadata["entity_guard_reason"] = "guardable canonical entity + matching BM25 lane reserved for rerank Top-20"
            candidate_index = ranked.index(candidate)
            ranked.pop(candidate_index)
            insertion_index = min(14 + len(guarded_entities), limit - 1)
            ranked.insert(insertion_index, candidate)
            guarded_entities.add(canonical_id)
            used_candidates.add(_document_key(candidate))
    for rank, document in enumerate(ranked, 1):
        document.metadata["combined_rank"] = rank
        # rerank_rank is the effective rank used by Top-20 selection; retain
        # the raw cross-encoder position separately as model_rerank_rank.
        document.metadata["rerank_rank"] = rank
    return ranked


def _select_reranked(documents: list[Document], top_k: int) -> list[Document]:
    """Apply rerank Top-20, then source cap, without legacy score thresholds."""
    rerank_limit = min(len(documents), settings.retriever_rerank_top_k)
    pool = documents[:rerank_limit]
    configured_cap = settings.retriever_source_cap
    per_source = configured_cap if configured_cap and configured_cap > 0 else None
    sources = {str(doc.metadata.get("source", "")) for doc in pool}
    counts: dict[str, int] = {}
    selected: list[Document] = []
    audit: list[dict] = []
    for pre_rank, document in enumerate(documents, 1):
        document.metadata["rerank_rank"] = document.metadata.get("rerank_rank", pre_rank)
        source = str(document.metadata.get("source", ""))
        in_pool = pre_rank <= rerank_limit
        if not in_pool:
            disposition = "outside_rerank_top20"
        elif len(selected) >= top_k:
            disposition = "outside_final_top10"
        elif len(sources) > 1 and per_source is not None and counts.get(source, 0) >= per_source:
            document.metadata["source_cap_filtered"] = True
            disposition = "source_cap_filtered"
        else:
            selected.append(document)
            counts[source] = counts.get(source, 0) + 1
            disposition = "selected"
        document.metadata["selection_disposition"] = disposition
        audit.append({
            "chunk_id": _document_key(document),
            "source": source,
            "dense_rank": document.metadata.get("dense_rank"),
            "bm25_rank": document.metadata.get("bm25_rank"),
            "union_rank": document.metadata.get("union_rank"),
            "fallback_rrf_rank": document.metadata.get("fallback_rrf_rank"),
            "rerank_rank": document.metadata.get("rerank_rank"),
            "model_rerank_rank": document.metadata.get("model_rerank_rank"),
            "retrieval_stage": document.metadata.get("retrieval_stage", "not_retrieved"),
            "threshold_passed": True,
            "pre_diversity_rank": pre_rank,
            "disposition": disposition,
            "rrf_score": document.metadata.get("rrf_score"),
            "rerank_score": document.metadata.get("rerank_score"),
            "combined_score": document.metadata.get("combined_score"),
            "retrieval_relevance": document.metadata.get("retrieval_relevance"),
            "entity_match_score": document.metadata.get("entity_match_score"),
            "explicit_entity_score": document.metadata.get("explicit_entity_score"),
            "expanded_entity_score": document.metadata.get("expanded_entity_score"),
            "explicit_entities": document.metadata.get("explicit_entities", []),
            "expanded_entities": document.metadata.get("expanded_entities", []),
            "explicit_candidate_entities": document.metadata.get("explicit_candidate_entities", []),
            "expanded_candidate_entities": document.metadata.get("expanded_candidate_entities", []),
            "deterministic_candidate_entities": document.metadata.get("deterministic_candidate_entities", []),
            "candidate_entities": document.metadata.get("candidate_entities", []),
            "explicit_entity_ids": document.metadata.get("explicit_entity_ids", []),
            "expanded_entity_ids": document.metadata.get("expanded_entity_ids", []),
            "candidate_entity_ids": document.metadata.get("candidate_entity_ids", []),
            "guardable_candidate_entity_ids": document.metadata.get("guardable_candidate_entity_ids", []),
            "entity_bm25_ranks": document.metadata.get("entity_bm25_ranks", {}),
            "source_role": document.metadata.get("source_role"),
            "query_role_match": document.metadata.get("query_role_match"),
            "entity_guard_triggered": document.metadata.get("entity_guard_triggered", False),
            "entity_guard_reason": document.metadata.get("entity_guard_reason"),
            "guarded_entity_id": document.metadata.get("guarded_entity_id"),
            "process_exact_match": document.metadata.get("process_exact_match", False),
            "process_exact_term_count": document.metadata.get("process_exact_term_count", 0),
            "process_prior_applied": document.metadata.get("process_prior_applied", False),
            "process_prior_reason": document.metadata.get("process_prior_reason"),
            "process_prior_delta": document.metadata.get("process_prior_delta", 0.0),
            "anthropometry_prior_applied": document.metadata.get("anthropometry_prior_applied", False),
            "anthropometry_prior_reason": document.metadata.get("anthropometry_prior_reason"),
            "anthropometry_prior_delta": document.metadata.get("anthropometry_prior_delta", 0.0),
            "low_authority_penalty_applied": document.metadata.get("low_authority_penalty_applied", False),
            "low_authority_penalty_delta": document.metadata.get("low_authority_penalty_delta", 0.0),
            "authority_prior_reason": document.metadata.get("authority_prior_reason"),
            "low_authority_score_capped": document.metadata.get("low_authority_score_capped", False),
            "low_authority_score_ceiling": document.metadata.get("low_authority_score_ceiling"),
            "is_low_authority": _is_low_authority_document(document),
            "authority_priority": next(
                (
                    _entity_candidate_priority(document, entity_id)
                    for entity_id in document.metadata.get("guardable_candidate_entity_ids", [])
                ),
                None,
            ),
            "combined_rank": document.metadata.get("combined_rank"),
            "union_truncated": document.metadata.get("union_truncated", False),
            "bm25_query_hits": document.metadata.get("bm25_query_hits", []),
        })
    # A final reserve is intentionally narrower than the Top-20 guard. It is
    # only for a guardable standard/grade that is already in the rerank pool;
    # generic materials and expansion-only entities never displace a result.
    if len(selected) >= top_k and top_k > 0:
        selected_ids = {_document_key(document) for document in selected}
        represented_entities = {
            canonical_id
            for document in selected
            for canonical_id in document.metadata.get("guardable_candidate_entity_ids", [])
        }
        reserved_entities: set[str] = set()
        for candidate in pool:
            if _document_key(candidate) in selected_ids:
                continue
            if not candidate.metadata.get("entity_final_reserve_eligible"):
                continue
            candidate_entities = (
                set(candidate.metadata.get("guardable_candidate_entity_ids", []))
                - represented_entities
                - reserved_entities
            )
            if not candidate_entities:
                continue
            source = str(candidate.metadata.get("source", ""))
            victim_candidates = [
                document for document in selected
                if counts.get(str(document.metadata.get("source", "")), 0) > 1
                and not document.metadata.get("entity_guard_triggered")
            ]
            victim = min(
                victim_candidates,
                key=lambda document: float(document.metadata.get("combined_score", 0.0)),
                default=None,
            )
            if victim is None:
                continue
            victim_source = str(victim.metadata.get("source", ""))
            resulting_source_count = counts.get(source, 0) + (0 if source == victim_source else 1)
            if len(sources) > 1 and per_source is not None and resulting_source_count > per_source:
                continue
            victim_id = _document_key(victim)
            victim_index = selected.index(victim)
            selected[victim_index] = candidate
            selected_ids.remove(victim_id)
            selected_ids.add(_document_key(candidate))
            if source != victim_source:
                counts[victim_source] -= 1
                counts[source] = counts.get(source, 0) + 1
            guarded_entity = sorted(candidate_entities)[0]
            reserved_entities.add(guarded_entity)
            candidate.metadata["entity_guard_triggered"] = True
            candidate.metadata["guarded_entity_id"] = guarded_entity
            candidate.metadata["entity_guard_reason"] = "guardable canonical entity reserved for final Top-K"
            candidate.metadata["selection_disposition"] = "selected"
            victim.metadata["entity_final_reserve_displaced"] = True
            victim.metadata["selection_disposition"] = "outside_final_top10"
            victim.metadata.pop("final_rank", None)
            for row in audit:
                if row.get("chunk_id") == _document_key(candidate):
                    row["disposition"] = "selected"
                    row["entity_guard_triggered"] = True
                    row["entity_guard_reason"] = candidate.metadata["entity_guard_reason"]
                elif row.get("chunk_id") == victim_id and row.get("disposition") == "selected":
                    row["disposition"] = "outside_final_top10"
                    row["entity_guard_reason"] = "displaced_by_guardable_entity_reserve"
        selected.sort(key=lambda document: float(document.metadata.get("combined_score", 0.0)), reverse=True)
    source_ranks: dict[str, int] = {}
    for final_rank, document in enumerate(selected, 1):
        source = str(document.metadata.get("source", ""))
        source_ranks.setdefault(source, len(source_ranks) + 1)
        document.metadata["source_rank"] = source_ranks[source]
        document.metadata["source_rank_after_diversity"] = source_ranks[source]
        document.metadata["final_rank"] = final_rank
        document.metadata["retrieval_threshold_passed"] = True
        document.metadata["retrieval_candidate_audit"] = audit
        document.metadata["source_cap"] = per_source
        for row in audit:
            if row.get("chunk_id") == _document_key(document):
                row["final_rank"] = final_rank
                row["source_rank"] = source_ranks[source]
    return selected


def _entity_diagnostics(
    union: list[Document],
    selected: list[Document],
    entity_plan: list[dict[str, object]] | str,
) -> tuple[list[str], list[str], list[dict[str, object]]]:
    """Aggregate one representative for every planned retrieval entity."""
    if isinstance(entity_plan, str):
        bm25_plan = build_bm25_queries(entity_plan)
        entity_plan = _build_entity_plan(entity_plan, bm25_plan)
    diagnostics: list[dict[str, object]] = []
    entity_ids: list[str] = []
    chunk_ids: list[str] = []
    selected_by_key = {_document_key(doc): doc.metadata.get("final_rank") for doc in selected}
    seen: set[tuple[str, str, str]] = set()
    for plan_item in entity_plan:
        entity = str(plan_item.get("entity") or "")
        if not entity:
            continue
        canonical_id = str(plan_item.get("canonical_id") or "") or None
        identity = (str(plan_item.get("origin") or "expanded"), canonical_id or "", entity)
        if identity in seen:
            continue
        seen.add(identity)
        if canonical_id:
            raw_matches = [
                doc for doc in union
                if canonical_id in _canonical_entity_ids_from_text(_candidate_entity_text(doc))
            ]
            matches = [
                doc for doc in raw_matches
                if _entity_candidate_priority(doc, canonical_id) >= 0
            ]
        else:
            raw_matches = [
                doc for doc in union
                if entity in set(doc.metadata.get("candidate_entities", []))
                or any(
                    hit.get("rule_id") == plan_item.get("rule_id")
                    for hit in doc.metadata.get("bm25_query_hits", [])
                )
            ]
            matches = [doc for doc in raw_matches if not _is_low_authority_document(doc)]
        if not matches:
            diagnostics.append({
                "entity": entity,
                "canonical_id": canonical_id,
                "origin": plan_item.get("origin"),
                "rule_id": plan_item.get("rule_id"),
                "guardable": bool(plan_item.get("guardable")),
                "entity_guard_triggered": False,
                "guarded_entity_id": None,
                "is_low_authority": bool(raw_matches),
                "authority_priority": None,
                "authority_filtered_count": max(0, len(raw_matches) - len(matches)),
                "representative_source_role": None,
                "representative_source": None,
                "low_authority_only": bool(raw_matches),
                "matched_chunk_id": None,
                "entity_bm25_rank": None,
                "bm25_rank": None,
                "dense_rank": None,
                "union_rank": None,
                "model_rerank_rank": None,
                "combined_rank": None,
                "final_rank": None,
                "disposition": "not_in_union",
            })
            continue
        if canonical_id:
            entity_ids.append(canonical_id)
        best = min(matches, key=lambda doc: (
            0 if selected_by_key.get(_document_key(doc)) is not None else 1,
            0 if int(doc.metadata.get("combined_rank") or 10**9) <= int(
                getattr(settings, "retriever_rerank_top_k", 20)
            ) else 1,
            -_entity_candidate_priority(doc, canonical_id) if canonical_id else 0,
            int(doc.metadata.get("combined_rank") or 10**9),
            int(_entity_lane_rank(doc, canonical_id) or 10**9) if canonical_id else 10**9,
        ))
        chunk_id = _document_key(best)
        chunk_ids.append(chunk_id)
        final_rank = selected_by_key.get(chunk_id)
        if final_rank is not None:
            disposition = "selected"
        elif best.metadata.get("source_cap_filtered") or best.metadata.get("selection_disposition") == "source_cap_filtered":
            disposition = "source_cap_filtered"
        elif int(best.metadata.get("combined_rank") or 10**9) <= int(
            getattr(settings, "retriever_rerank_top_k", 20)
        ):
            disposition = "outside_final_top10"
        else:
            disposition = "outside_rerank_top20"
        diagnostics.append({
            "entity": entity,
            "canonical_id": canonical_id,
            "origin": plan_item.get("origin"),
            "rule_id": plan_item.get("rule_id"),
            "guardable": bool(plan_item.get("guardable")),
            "is_low_authority": _is_low_authority_document(best),
            "authority_priority": _entity_candidate_priority(best, canonical_id) if canonical_id else None,
            "authority_filtered_count": max(0, len(raw_matches) - len(matches)),
            "representative_source_role": _source_role(best),
            "representative_source": str(best.metadata.get("source", "")),
            "low_authority_only": False,
            "entity_guard_triggered": bool(canonical_id) and any(
                doc.metadata.get("entity_guard_triggered")
                and doc.metadata.get("guarded_entity_id") == canonical_id
                for doc in matches
            ),
            "guarded_entity_id": next((
                doc.metadata.get("guarded_entity_id") for doc in matches
                if canonical_id
                and doc.metadata.get("entity_guard_triggered")
                and doc.metadata.get("guarded_entity_id") == canonical_id
            ), None),
            "dense_rank": best.metadata.get("dense_rank"),
            "entity_bm25_rank": _entity_lane_rank(best, canonical_id) if canonical_id else None,
            "bm25_rank": best.metadata.get("bm25_rank"),
            "union_rank": best.metadata.get("union_rank"),
            "model_rerank_rank": best.metadata.get("model_rerank_rank"),
            "combined_rank": best.metadata.get("combined_rank"),
            "final_rank": final_rank,
            "matched_chunk_id": chunk_id,
            "disposition": disposition,
        })
    return list(dict.fromkeys(entity_ids)), list(dict.fromkeys(chunk_ids)), diagnostics


def diversify_by_source(
    documents: list[Document], top_k: int, per_source: int | None = None
) -> list[Document]:
    """Filter weak candidates, then cap repeated sources without slot filling.

    The incoming order is already the RRF/reranker order. We never allocate a
    slot merely because a source is different: candidates must first pass a
    score threshold, and only repeated high-scoring chunks are suppressed.
    """
    if not documents:
        return []
    configured_cap = settings.retriever_source_cap if per_source is None else per_source
    per_source = configured_cap if configured_cap and configured_cap > 0 else None
    sources = {str(doc.metadata.get("source", "")) for doc in documents}
    rrf_scores = [
        float(doc.metadata["rrf_score"])
        for doc in documents
        if doc.metadata.get("rrf_score") is not None
    ]
    dynamic_rrf_threshold = max(rrf_scores) * 0.55 if rrf_scores else None
    source_order_before: dict[str, int] = {}
    for pre_rank, document in enumerate(documents, 1):
        source = str(document.metadata.get("source", ""))
        source_order_before.setdefault(source, len(source_order_before) + 1)
        document.metadata["pre_diversity_rank"] = pre_rank
        document.metadata["source_rank_before_diversity"] = source_order_before[source]
        document.metadata["dynamic_rrf_threshold"] = (
            round(dynamic_rrf_threshold, 8) if dynamic_rrf_threshold is not None else None
        )
    selected = []
    counts: dict[str, int] = {}
    audit: list[dict] = []
    for document in documents:
        relevance = document.metadata.get("retrieval_relevance")
        lexical_score = document.metadata.get("retrieval_lexical_score")
        rrf_score = document.metadata.get("rrf_score")
        exact_match = bool(document.metadata.get("retrieval_exact_match"))
        dense_rank = document.metadata.get("dense_rank")
        bm25_rank = document.metadata.get("bm25_rank")
        if dense_rank is not None and bm25_rank is not None:
            retrieval_stage = "dense_and_bm25"
        elif dense_rank is not None:
            retrieval_stage = "dense_only"
        elif bm25_rank is not None:
            retrieval_stage = "bm25_only"
        else:
            retrieval_stage = "unknown"
        document.metadata["retrieval_stage"] = retrieval_stage
        passes_dense = (
            relevance is not None
            and float(relevance) >= settings.retriever_min_relevance
        )
        passes_rrf = (
            rrf_score is not None
            and dynamic_rrf_threshold is not None
            and float(rrf_score) >= dynamic_rrf_threshold
        )
        # In the fused path lexical score alone is not enough to bypass the
        # dynamic threshold. The exception is the lexical-only fallback,
        # where there is no RRF score to compare.
        passes_lexical = (
            rrf_score is None and lexical_score is not None and float(lexical_score) > 0
        )
        has_score = relevance is not None or lexical_score is not None or rrf_score is not None
        # Exact identifier matches are allowed through even if the dense score
        # is low. This is essential for standard numbers and material grades.
        passes_threshold = (
            not has_score or exact_match or passes_dense or passes_rrf or passes_lexical
        )
        document.metadata["retrieval_threshold_passed"] = passes_threshold
        disposition = "threshold_filtered" if not passes_threshold else "candidate"
        if not passes_threshold:
            audit.append({
                "chunk_id": _document_key(document),
                "source": str(document.metadata.get("source", "")),
                "pre_diversity_rank": document.metadata.get("pre_diversity_rank"),
                "dense_rank": document.metadata.get("dense_rank"),
                "bm25_rank": document.metadata.get("bm25_rank"),
                "retrieval_stage": retrieval_stage,
                "threshold_passed": False,
                "rrf_score": document.metadata.get("rrf_score"),
                "retrieval_relevance": document.metadata.get("retrieval_relevance"),
                "retrieval_exact_match": exact_match,
                "disposition": disposition,
            })
            continue
        source = str(document.metadata.get("source", ""))
        if len(sources) > 1 and per_source is not None and counts.get(source, 0) >= per_source:
            document.metadata["source_cap_filtered"] = True
            audit.append({
                "chunk_id": _document_key(document),
                "source": source,
                "pre_diversity_rank": document.metadata.get("pre_diversity_rank"),
                "dense_rank": document.metadata.get("dense_rank"),
                "bm25_rank": document.metadata.get("bm25_rank"),
                "retrieval_stage": retrieval_stage,
                "threshold_passed": True,
                "rrf_score": document.metadata.get("rrf_score"),
                "retrieval_relevance": document.metadata.get("retrieval_relevance"),
                "retrieval_exact_match": exact_match,
                "disposition": "source_cap_filtered",
            })
            continue
        if len(selected) >= top_k:
            audit.append({
                "chunk_id": _document_key(document),
                "source": source,
                "pre_diversity_rank": document.metadata.get("pre_diversity_rank"),
                "dense_rank": document.metadata.get("dense_rank"),
                "bm25_rank": document.metadata.get("bm25_rank"),
                "retrieval_stage": retrieval_stage,
                "threshold_passed": True,
                "rrf_score": document.metadata.get("rrf_score"),
                "retrieval_relevance": document.metadata.get("retrieval_relevance"),
                "retrieval_exact_match": exact_match,
                "disposition": "outside_final_top_k",
            })
            continue
        selected.append(document)
        counts[source] = counts.get(source, 0) + 1
        audit.append({
            "chunk_id": _document_key(document),
            "source": source,
            "pre_diversity_rank": document.metadata.get("pre_diversity_rank"),
            "dense_rank": document.metadata.get("dense_rank"),
            "bm25_rank": document.metadata.get("bm25_rank"),
            "retrieval_stage": retrieval_stage,
            "threshold_passed": True,
            "rrf_score": document.metadata.get("rrf_score"),
            "retrieval_relevance": document.metadata.get("retrieval_relevance"),
            "retrieval_exact_match": exact_match,
            "disposition": "selected",
        })
    source_ranks: dict[str, int] = {}
    for final_rank, document in enumerate(selected, 1):
        source = str(document.metadata.get("source", ""))
        source_ranks.setdefault(source, len(source_ranks) + 1)
        document.metadata["source_rank"] = source_ranks[source]
        document.metadata["source_rank_after_diversity"] = source_ranks[source]
        document.metadata["final_rank"] = final_rank
        for row in audit:
            if row.get("chunk_id") == _document_key(document):
                row["final_rank"] = final_rank
    for document in selected:
        document.metadata["retrieval_candidate_audit"] = audit
        document.metadata["source_cap"] = per_source
    by_key = {_document_key(document): document for document in documents}
    for row in audit:
        candidate = by_key.get(str(row.get("chunk_id", "")))
        if candidate is not None:
            row["fallback_rrf_rank"] = candidate.metadata.get("fallback_rrf_rank")
            row["rerank_rank"] = candidate.metadata.get("rerank_rank")
            row["final_rank"] = candidate.metadata.get("final_rank")
    return selected


def confidence_from_documents(documents: list[Document]) -> tuple[float | None, str, str | None]:
    """Return conservative confidence using dense relevance when available.

    Chroma relevance is already bounded to [0, 1]. Reranker logits and RRF
    scores are deliberately not treated as confidence because their scales
    vary by model and candidate count.
    """
    values = [
        float(doc.metadata["retrieval_relevance"])
        for doc in documents
        if doc.metadata.get("retrieval_relevance") is not None
    ]
    if not values:
        return None, "unknown", "检索结果没有可比较的 dense relevance 分数"
    values.sort(reverse=True)
    top1 = values[0]
    margin = top1 - values[1] if len(values) > 1 else top1
    if top1 < 0.25:
        return round(top1, 4), "low", f"top1={top1:.3f} 小于 0.25"
    if top1 < 0.50 and margin < 0.04:
        return round(top1, 4), "uncertain", f"top1={top1:.3f} 且 top1-top2={margin:.3f} 小于 0.04"
    return round(top1, 4), "high", f"top1={top1:.3f}, margin={margin:.3f}"


def _format_retrieval_error(exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if "collection" in lowered and (
        "not found" in lowered or "does not exist" in lowered
    ):
        return (
            f"本地知识库 collection `{settings.collection_name}` 不存在。"
            "请先停止应用，再运行 `python ingest.py` 导入文档。"
            f"原始错误：{message}"
        )
    if "hnsw" in message.lower():
        return (
            "Chroma HNSW 索引无法加载，索引可能不完整或已损坏。"
            "请停止应用，删除 `data\\chroma_db` 后再运行 `python ingest.py` 完整重建知识库。"
            f"原始错误：{message}"
        )
    return message


def retrieve_documents(question: str, top_k: int | None = None) -> list[Document]:
    if not settings.chroma_dir.is_dir():
        return []

    cache_key = _retrieval_cache_key(question, top_k)
    with _RETRIEVAL_CACHE_LOCK:
        cached = _RETRIEVAL_CACHE.get(cache_key)
    if cached is not None:
        result = _clone_documents(cached)
        for document in result:
            document.metadata.setdefault("retrieval_profile", {})["cache_hit"] = True
        return result
    # Serialise the complete dense/rerank path. This is intentionally wider
    # than embedding_lock: concurrent subtasks must not each initialise or
    # contend for the same CPU model and Chroma collection.
    with _RETRIEVAL_EXECUTION_LOCK:
        with _RETRIEVAL_CACHE_LOCK:
            cached = _RETRIEVAL_CACHE.get(cache_key)
        if cached is not None:
            result = _clone_documents(cached)
            for document in result:
                document.metadata.setdefault("retrieval_profile", {})["cache_hit"] = True
            return result
        result = _retrieve_documents_uncached(question, top_k)
        with _RETRIEVAL_CACHE_LOCK:
            _RETRIEVAL_CACHE[cache_key] = _clone_documents(result)
            if len(_RETRIEVAL_CACHE) > 128:
                _RETRIEVAL_CACHE.pop(next(iter(_RETRIEVAL_CACHE)))
        return result


def _retrieve_documents_uncached(question: str, top_k: int | None = None) -> list[Document]:
    started = time.perf_counter()
    profile: dict[str, Any] = {"cache_hit": False, "timeout_stage": None}
    k = max(1, top_k or settings.retriever_top_k)
    dense_k = max(k, settings.retriever_dense_top_k)
    bm25_k = max(k, settings.retriever_bm25_top_k)
    expanded_queries = expand_query(question)
    mode = str(getattr(settings, "retriever_bm25_mode", "conditional")).lower()
    legacy_independent = bool(getattr(settings, "retriever_independent_bm25_queries", False))
    if legacy_independent:
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
            expansion_top_k=getattr(settings, "retriever_entity_bm25_top_k", getattr(settings, "retriever_expansion_bm25_top_k", 20)),
            max_candidates=getattr(settings, "retriever_union_max_candidates", 200),
        )
        for document in lexical_documents:
            document.metadata["expanded_queries"] = expanded_queries
            document.metadata["lexical_query"] = lexical_question
            document.metadata["bm25_query_mode"] = mode
            document.metadata["bm25_query_plan"] = bm25_plan
    except Exception:
        # Lexical retrieval is best-effort; semantic retrieval can still work
        # if the persisted collection cannot expose document metadata.
        lexical_documents = []
    profile["bm25_seconds"] = round(time.perf_counter() - lexical_started, 3)

    try:
        with embedding_lock:
            # Query-time encoding runs on CPU (RETRIEVAL_DEVICE) so the GPU is
            # reserved for ingestion batch-embedding; Chroma search is CPU-only.
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
            # Prefer scored similarity search so low-relevance material is
            # visible as indirect context instead of silently becoming direct
            # evidence. The fallback keeps compatibility with older Chroma.
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
                documents[:30], lexical_documents[:30], top_k=None,
                dense_weight=0.55, sparse_weight=0.45,
            )
            fallback_ranks = {
                _document_key(candidate): rank
                for rank, candidate in enumerate(fallback_ranked, 1)
            }
            for candidate in union:
                candidate.metadata["fallback_rrf_rank"] = fallback_ranks.get(_document_key(candidate))
            rerank_started = time.perf_counter()
            outcome = rerank(question, union)
            profile["reranker_seconds"] = round(time.perf_counter() - rerank_started, 3)
            rerank_status = {
                "status": outcome.status,
                "device": outcome.device,
                "model_path": outcome.model_path,
                "candidate_count": outcome.candidate_count,
                "latency_seconds": outcome.latency_seconds,
                "fallback_reason": outcome.fallback_reason,
            }
            if outcome.succeeded:
                ranked_documents = _apply_combined_ranking(question, outcome.documents, True, bm25_plan)
                selected = _select_reranked(ranked_documents, final_k)
            else:
                # Availability fallback intentionally uses the first 30 from
                # each channel and lower, explicit RRF weights. It is never
                # mixed with successful reranker metrics.
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
                # Backward-compatible alias: old evaluators read the first
                # selected document's candidate_entity_ids field.
                if index == 0:
                    document.metadata["candidate_entity_ids"] = candidate_entity_ids
                document.metadata["retrieval_union_ids"] = [
                    _document_key(candidate) for candidate in union
                ]
                document.metadata["retrieval_dense_top_ids"] = [
                    _document_key(candidate) for candidate in documents
                ]
                document.metadata["retrieval_bm25_top_ids"] = [
                    _document_key(candidate) for candidate in lexical_documents
                ]
                document.metadata["retrieval_bm25_query_hits"] = [
                    {
                        "chunk_id": _document_key(candidate),
                        "hits": candidate.metadata.get("bm25_query_hits", []),
                    }
                    for candidate in lexical_documents
                ]
                document.metadata["retrieval_union_before_count"] = union_before_count
                document.metadata["retrieval_union_after_count"] = len(
                    union
                )
                document.metadata["retrieval_union_truncated"] = any(
                    candidate.metadata.get("union_truncated", False) for candidate in union
                )
                document.metadata["retrieval_reranked_top20_ids"] = [
                    _document_key(candidate) for candidate in (ranked_documents if outcome.succeeded else [])[: settings.retriever_rerank_top_k]
                ] if outcome.succeeded else []
                document.metadata["retrieval_fallback_rrf_top20_ids"] = [
                    _document_key(candidate) for candidate in fallback[: settings.retriever_rerank_top_k]
                ] if not outcome.succeeded else []
                document.metadata["retrieval_final_top10_ids"] = [
                    _document_key(candidate) for candidate in selected
                ]
            return selected
    except Exception as exc:
        original_error = _format_retrieval_error(exc)
        if lexical_documents:
            _annotate_union(lexical_documents)
            outcome = rerank(question, lexical_documents)
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
    """Merge original + sub-query results.

    Original results are kept in full (they are the most relevant, and for
    single-source questions they must not be diluted). Sub-query results only
    add chunks from sources the original query missed, capped per new source,
    so extra facets broaden coverage without flooding the grader.
    """
    new_source_cap = settings.retriever_new_source_cap if new_source_cap is None else new_source_cap
    original = _dedupe_chunks(original_docs)
    covered_sources = {doc.metadata.get("source", "") for doc in original}
    result = list(original)
    seen_chunks = {doc.metadata.get("chunk_id") or str(id(doc)) for doc in original}
    per_source_count: dict[str, int] = {}

    for doc in subquery_docs:
        chunk_id = doc.metadata.get("chunk_id") or str(id(doc))
        source = doc.metadata.get("source", "")
        if chunk_id in seen_chunks:
            continue
        if source in covered_sources:
            continue  # this source already represented by the original query
        if per_source_count.get(source, 0) >= new_source_cap:
            continue
        seen_chunks.add(chunk_id)
        per_source_count[source] = per_source_count.get(source, 0) + 1
        result.append(doc)
    return result


def cap_per_source(
    documents: list[Document], per_source: int | None = None
) -> list[Document]:
    """Keep at most `per_source` chunks per source, preserving rank order.

    The grader sees all candidates in one prompt; flooding it with many chunks
    of a single source makes it drop other sources' relevant chunks (measured:
    12 candidates -> only the top source survives). Capping each source keeps
    the candidate list short and balanced so multi-facet sources survive.

    The cap is adaptive: single-source candidates are NOT capped (a fact-lookup
    question may need many chunks of its one source, e.g. a table on a later
    page), while multi-source candidates are capped so no source drowns others.
    """
    distinct_sources = {doc.metadata.get("source", "") for doc in documents}
    if len(distinct_sources) <= 1:
        return documents  # one source: keep everything, grader can handle it

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
    question: str, total_timeout_seconds: float | None = None,
    query_decompose_enabled: bool | None = None,
) -> list[Document]:
    """Hybrid retrieval: original query + LLM-decomposed facet sub-queries.

    A single embedding query cannot cover every facet of a multi-source
    question (e.g. office-chair dimensions need the standard AND adult body
    percentile data AND ergonomic design principles). We keep the original
    top-k intact and append only *new* sources surfaced by sub-queries.

    This function is async: the sync Chroma/embedding work runs in a worker
    thread (asyncio.to_thread) so it never blocks the event loop. Callers
    must await it — do NOT pass it to asyncio.to_thread.
    """
    started = time.perf_counter()
    deadline = (
        time.perf_counter() + float(total_timeout_seconds)
        if total_timeout_seconds and total_timeout_seconds > 0
        else None
    )

    async def bounded(operation, *args):
        if deadline is None:
            return await asyncio.to_thread(operation, *args)
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise asyncio.TimeoutError("local retrieval total deadline exceeded")
        return await asyncio.wait_for(asyncio.to_thread(operation, *args), timeout=remaining)

    async def bounded_coroutine(awaitable):
        """Bound an awaitable without awaiting its cancellation.

        `bounded` above wraps a *sync* function in a thread, where `wait_for` is
        acceptable: cancelling a thread future returns immediately even though
        the thread keeps running.  A coroutine is different -- see
        `src/async_utils`, which exists because this project measured the
        difference at 39286 seconds.
        """
        if deadline is None:
            return await awaitable
        return await wait_bounded(
            awaitable,
            deadline - time.perf_counter(),
            "local retrieval total deadline exceeded",
        )

    original = await bounded(retrieve_documents, question)
    base_profile = dict(original[0].metadata.get("retrieval_profile", {})) if original else {"cache_hit": False}

    def finalize(documents: list[Document]) -> list[Document]:
        result = cap_per_source(documents)
        for document in result:
            profile = document.metadata.setdefault("retrieval_profile", {})
            profile.update(base_profile)
            profile["total_seconds"] = round(time.perf_counter() - started, 3)
        return result

    if query_decompose_enabled is False or (query_decompose_enabled is None and not settings.query_decompose_enabled):
        return finalize(original)

    from src.query_decompose import decompose_question

    if deadline is not None and time.perf_counter() >= deadline:
        return finalize(original)
    # Bounded, and best-effort like the sub-query retrievals below: a
    # decomposition that times out falls back to the original results rather
    # than failing the whole retrieval.  Unbounded was not hypothetical -- with
    # the provider hanging, one question here took 42019 seconds while the other
    # seventeen together took about two hours.
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
                retrieve_documents, sub_query, settings.retriever_subquery_top_k
            )
            extra.extend(sub_docs)
        except Exception:
            continue  # a failing sub-query must not break the whole retrieval
    merged = hybrid_merge(original, extra)
    return finalize(merged)

