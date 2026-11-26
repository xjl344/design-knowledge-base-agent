"""BM25 lexical indexing and multi-lane lexical retrieval over persisted Chroma."""

from __future__ import annotations

import math
import re
from typing import Any

from langchain_core.documents import Document

from src.retrieval._compat import active_settings, document_key
from src.retrieval.bilingual_glossary import expand_bilingual_terms
from src.retrieval.domain_rules import (
    _canonical_entity_id,
    _canonical_entity_ids_from_text,
    _compact_query_text,
    _extract_query_entities,
    _normalise_bm25_lane,
    domain_rules_enabled,
)
from src.retrieval.fusion import _annotate_document_evidence

_LEXICAL_INDEX_CACHE: dict[
    tuple[str, str, int],
    tuple[list[tuple[str, dict[str, Any], list[str], list[str], dict[str, list[str]]]], float],
] = {}


def _lexical_terms(question: str) -> list[str]:
    """Extract small Chinese/Latin terms for lexical BM25 scoring."""
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
    """Query persisted Chroma text and metadata without loading embeddings."""
    import chromadb

    settings = active_settings()
    client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    try:
        collection = client.get_collection(settings.collection_name)
        count = int(collection.count())
        cache_key = (str(settings.chroma_dir), str(settings.collection_name), count)
        cached = _LEXICAL_INDEX_CACHE.get(cache_key)
        payload = collection.get(include=["documents", "metadatas"]) if cached is None else None
    finally:
        client.close()

    terms = _lexical_terms(question)
    if bool(getattr(settings, "retriever_bilingual_glossary_enabled", True)):
        bilingual = expand_bilingual_terms(question)
        if bilingual:
            terms = list(dict.fromkeys(terms + bilingual))
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
            content_text = str(content or "")
            field_terms = {key: _lexical_terms(value) for key, value in metadata_fields.items()}
            content_terms = _lexical_terms(content_text)
            terms_in_doc = content_terms + [term for values in field_terms.values() for term in values]
            rows.append((content_text, metadata, terms_in_doc, content_terms, field_terms))
        average_length = sum(len(doc_terms) for _, _, doc_terms, _, _ in rows) / len(rows) if rows else 0.0
        _LEXICAL_INDEX_CACHE.clear()
        _LEXICAL_INDEX_CACHE[cache_key] = (rows, average_length)

    rows = [
        (content, dict(metadata), doc_terms, content_terms, field_terms)
        for content, metadata, doc_terms, content_terms, field_terms in rows
    ]
    if not rows:
        return []

    use_domain_rules = domain_rules_enabled()
    query_canonical_ids = (
        {
            _canonical_entity_id(entity)
            for entity in _extract_query_entities(question)
            if _canonical_entity_id(entity)
        }
        if use_domain_rules
        else set()
    )
    document_frequency = {term: sum(term in doc_terms for _, _, doc_terms, _, _ in rows) for term in terms}
    metadata_weight = getattr(settings, "retriever_metadata_weight", 3.0)
    parent_weight = getattr(settings, "retriever_parent_weight", 2.0)

    ranked: list[tuple[float, Document]] = []
    for content_text, metadata, doc_terms, content_terms, field_terms in rows:
        metadata_text = " ".join(
            str(metadata.get(key, ""))
            for key in ("source", "source_title", "title", "section_path", "parent_document")
        )
        length = len(doc_terms)
        frequencies = {
            term: content_terms.count(term)
            + metadata_weight * sum(field_terms[field].count(term) for field in ("source", "title", "section_path"))
            + parent_weight * field_terms["parent_document"].count(term)
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
            if compact and (
                compact in re.sub(r"\s+", "", metadata_lower)
                or compact in re.sub(r"\s+", "", content_lower)
            ):
                score += 2.0
                exact_matches.append(identifier)

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

        if query_canonical_ids:
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
            key = document_key(document)
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
    return ranked[: max(1, max_candidates)]
