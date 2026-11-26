"""Combined ranking, reranked selection, source diversification, and diagnostics."""

from __future__ import annotations

import math

from langchain_core.documents import Document

from src.retrieval._compat import active_settings, document_key
from src.retrieval.domain_rules import (
    _anthropometry_prior_signal,
    _build_entity_plan,
    _candidate_entity_text,
    _canonical_entity_id,
    _canonical_entity_ids_from_text,
    _entity_candidate_priority,
    _entity_lane_rank,
    _entity_match_score,
    _extract_query_entities,
    _is_low_authority_document,
    _process_prior_signal,
    _query_roles,
    _source_role,
    build_bm25_queries,
    domain_rules_enabled,
)


def _normalise_scores(documents: list[Document], key: str) -> dict[str, float]:
    values = {
        document_key(doc): float(doc.metadata[key])
        for doc in documents
        if doc.metadata.get(key) is not None
    }
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
    """Apply semantic/entity/RRF composition to the candidate pool."""
    settings = active_settings()
    use_domain_rules = domain_rules_enabled()
    bm25_plan = query_plan if isinstance(query_plan, list) else build_bm25_queries(question)
    entity_plan = _build_entity_plan(question, bm25_plan) if use_domain_rules else []
    if use_domain_rules and isinstance(query_plan, str):
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
    roles = _query_roles(explicit_entities + deterministic_entities) if use_domain_rules else set()
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
        key = document_key(document)
        semantic = rerank_norm.get(key, rrf_norm.get(key, 0.0))
        if mode == "reranker_only" or not use_domain_rules:
            combined = semantic if mode == "reranker_only" else (
                rerank_weight * semantic + (1.0 - rerank_weight) * rrf_norm.get(key, 0.0)
                if reranker_success and rrf_norm
                else semantic
            )
        elif mode == "reranker_entity":
            combined = 0.90 * semantic + explicit_weight * explicit_score
        else:
            combined = (
                rerank_weight * semantic
                + explicit_weight * explicit_score
                + expanded_weight * weak_score
                + rrf_weight * rrf_norm.get(key, 0.0)
            )
        exact_ids = _canonical_entity_ids_from_text(_candidate_entity_text(document)) if use_domain_rules else set()
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
    base_ranks = {document_key(document): rank for rank, document in enumerate(base_ranked, 1)}
    process_bonus = float(getattr(settings, "retriever_process_guide_prior_bonus", 0.025))
    anthropometry_bonus = float(getattr(settings, "retriever_anthropometry_prior_bonus", 0.08))
    rerank_top_k = int(getattr(settings, "retriever_rerank_top_k", 20))

    for document in documents:
        base_rank = base_ranks[document_key(document)]
        matched, term_count, reason = _process_prior_signal(
            document, bm25_plan, base_rank, rerank_top_k
        )
        delta = process_bonus if matched else 0.0
        anthropometry_matched, anthropometry_reason = _anthropometry_prior_signal(
            question, document, base_rank, rerank_top_k
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

    authority_enabled = bool(getattr(settings, "retriever_low_authority_prior", True))
    authority_penalty = float(getattr(settings, "retriever_low_authority_penalty", 0.12))
    has_authoritative_candidate = any(not _is_low_authority_document(doc) for doc in documents)
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

    ranked = sorted(
        documents,
        key=lambda doc: (
            float(doc.metadata.get("combined_score", 0.0)),
            float(doc.metadata.get("rerank_score", -1e9)),
        ),
        reverse=True,
    )

    if (
        use_domain_rules
        and reranker_success
        and getattr(settings, "retriever_entity_guard", True)
        and len(ranked) > rerank_top_k
    ):
        limit = rerank_top_k
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
                and document_key(item) not in used_candidates
            ]
            if not eligible:
                continue
            candidate = min(eligible, key=lambda item: (
                -_entity_candidate_priority(item, canonical_id),
                int(_entity_lane_rank(item, canonical_id) or 10**9),
                int(item.metadata.get("bm25_rank") or 10**9),
                int(item.metadata.get("model_rerank_rank") or 10**9),
                document_key(item),
            ))
            candidate.metadata["entity_guard_triggered"] = True
            candidate.metadata["guarded_entity_id"] = canonical_id
            candidate.metadata["entity_guard_reason"] = (
                "guardable canonical entity + matching BM25 lane reserved for rerank Top-20"
            )
            ranked.pop(ranked.index(candidate))
            insertion_index = min(14 + len(guarded_entities), limit - 1)
            ranked.insert(insertion_index, candidate)
            guarded_entities.add(canonical_id)
            used_candidates.add(document_key(candidate))

    for rank, document in enumerate(ranked, 1):
        document.metadata["combined_rank"] = rank
        document.metadata["rerank_rank"] = rank
    return ranked


def _select_reranked(documents: list[Document], top_k: int) -> list[Document]:
    """Apply rerank Top-K pool, then source cap, without legacy score thresholds."""
    settings = active_settings()
    rerank_limit = min(len(documents), getattr(settings, "retriever_rerank_top_k", 20))
    pool = documents[:rerank_limit]
    configured_cap = getattr(settings, "retriever_source_cap", 2)
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
            "chunk_id": document_key(document),
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

    if len(selected) >= top_k and top_k > 0:
        selected_ids = {document_key(document) for document in selected}
        represented_entities = {
            canonical_id
            for document in selected
            for canonical_id in document.metadata.get("guardable_candidate_entity_ids", [])
        }
        reserved_entities: set[str] = set()
        for candidate in pool:
            if document_key(candidate) in selected_ids:
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
            victim_id = document_key(victim)
            victim_index = selected.index(victim)
            selected[victim_index] = candidate
            selected_ids.remove(victim_id)
            selected_ids.add(document_key(candidate))
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
                if row.get("chunk_id") == document_key(candidate):
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
            if row.get("chunk_id") == document_key(document):
                row["final_rank"] = final_rank
                row["source_rank"] = source_ranks[source]
    return selected


def _entity_diagnostics(
    union: list[Document],
    selected: list[Document],
    entity_plan: list[dict[str, object]] | str,
) -> tuple[list[str], list[str], list[dict[str, object]]]:
    """Aggregate one representative for every planned retrieval entity."""
    settings = active_settings()
    if isinstance(entity_plan, str):
        bm25_plan = build_bm25_queries(entity_plan)
        entity_plan = _build_entity_plan(entity_plan, bm25_plan)
    diagnostics: list[dict[str, object]] = []
    entity_ids: list[str] = []
    chunk_ids: list[str] = []
    selected_by_key = {document_key(doc): doc.metadata.get("final_rank") for doc in selected}
    seen: set[tuple[str, str, str]] = set()
    rerank_top_k = int(getattr(settings, "retriever_rerank_top_k", 20))

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
            0 if selected_by_key.get(document_key(doc)) is not None else 1,
            0 if int(doc.metadata.get("combined_rank") or 10**9) <= rerank_top_k else 1,
            -_entity_candidate_priority(doc, canonical_id) if canonical_id else 0,
            int(doc.metadata.get("combined_rank") or 10**9),
            int(_entity_lane_rank(doc, canonical_id) or 10**9) if canonical_id else 10**9,
        ))
        chunk_id = document_key(best)
        chunk_ids.append(chunk_id)
        final_rank = selected_by_key.get(chunk_id)
        if final_rank is not None:
            disposition = "selected"
        elif best.metadata.get("source_cap_filtered") or best.metadata.get("selection_disposition") == "source_cap_filtered":
            disposition = "source_cap_filtered"
        elif int(best.metadata.get("combined_rank") or 10**9) <= rerank_top_k:
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
    """Filter weak candidates, then cap repeated sources without slot filling."""
    if not documents:
        return []
    settings = active_settings()
    configured_cap = getattr(settings, "retriever_source_cap", 2) if per_source is None else per_source
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
    min_relevance = getattr(settings, "retriever_min_relevance", 0.25)

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

        passes_dense = relevance is not None and float(relevance) >= min_relevance
        passes_rrf = (
            rrf_score is not None
            and dynamic_rrf_threshold is not None
            and float(rrf_score) >= dynamic_rrf_threshold
        )
        passes_lexical = (
            rrf_score is None and lexical_score is not None and float(lexical_score) > 0
        )
        has_score = relevance is not None or lexical_score is not None or rrf_score is not None
        passes_threshold = (
            not has_score or exact_match or passes_dense or passes_rrf or passes_lexical
        )
        document.metadata["retrieval_threshold_passed"] = passes_threshold
        source = str(document.metadata.get("source", ""))
        base_row = {
            "chunk_id": document_key(document),
            "source": source,
            "pre_diversity_rank": document.metadata.get("pre_diversity_rank"),
            "dense_rank": document.metadata.get("dense_rank"),
            "bm25_rank": document.metadata.get("bm25_rank"),
            "retrieval_stage": retrieval_stage,
            "threshold_passed": passes_threshold,
            "rrf_score": document.metadata.get("rrf_score"),
            "retrieval_relevance": document.metadata.get("retrieval_relevance"),
            "retrieval_exact_match": exact_match,
        }
        if not passes_threshold:
            audit.append({**base_row, "disposition": "threshold_filtered"})
            continue
        if len(sources) > 1 and per_source is not None and counts.get(source, 0) >= per_source:
            document.metadata["source_cap_filtered"] = True
            audit.append({**base_row, "disposition": "source_cap_filtered"})
            continue
        if len(selected) >= top_k:
            audit.append({**base_row, "disposition": "outside_final_top_k"})
            continue
        selected.append(document)
        counts[source] = counts.get(source, 0) + 1
        audit.append({**base_row, "disposition": "selected"})

    source_ranks: dict[str, int] = {}
    for final_rank, document in enumerate(selected, 1):
        source = str(document.metadata.get("source", ""))
        source_ranks.setdefault(source, len(source_ranks) + 1)
        document.metadata["source_rank"] = source_ranks[source]
        document.metadata["source_rank_after_diversity"] = source_ranks[source]
        document.metadata["final_rank"] = final_rank
        for row in audit:
            if row.get("chunk_id") == document_key(document):
                row["final_rank"] = final_rank
    for document in selected:
        document.metadata["retrieval_candidate_audit"] = audit
        document.metadata["source_cap"] = per_source
    by_key = {document_key(document): document for document in documents}
    for row in audit:
        candidate = by_key.get(str(row.get("chunk_id", "")))
        if candidate is not None:
            row["fallback_rrf_rank"] = candidate.metadata.get("fallback_rrf_rank")
            row["rerank_rank"] = candidate.metadata.get("rerank_rank")
            row["final_rank"] = candidate.metadata.get("final_rank")
    return selected
