"""Compare F/R/P indexes using the same retrieval-only question set."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from config import settings
from src.metrics import retrieval_metrics
from src.retriever import retrieve_documents
from src.query_expansion import expand_query
from eval_chunking_langsmith import load_questions


def _configure_console_encoding() -> None:
    """Keep Windows GBK consoles from failing on source names such as ™."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _normalise(value: str) -> str:
    return "".join(str(value or "").replace("\\", "/").casefold().split())


def _source_id(value: str) -> str:
    """Compare source paths by filename, matching the LangSmith evaluator."""
    normalised = _normalise(value)
    return normalised.rsplit("/", 1)[-1]


def _source_matches(expected: str, actual: str) -> bool:
    expected_normalised = _normalise(expected)
    actual_normalised = _normalise(actual)
    return (
        _source_id(expected_normalised) == _source_id(actual_normalised)
        or expected_normalised in actual_normalised
    )


def _source_hit(expected: list[str], documents) -> float:
    actual = [str(doc.metadata.get("source", "")) for doc in documents]
    scores = []
    for item in expected:
        scores.append(any(_source_matches(item, source) for source in actual))
    return sum(scores) / len(scores) if scores else (1.0 if not actual else 0.0)


def _first_hit_rank(expected: list[str], documents) -> int | None:
    for rank, document in enumerate(documents, 1):
        actual = str(document.metadata.get("source", ""))
        if any(_source_matches(item, actual) for item in expected):
            return rank
    return None


def _source_ranking(values: list[str]) -> list[dict]:
    """Return one row per source while retaining its first chunk rank/count."""
    rows: dict[str, dict] = {}
    for chunk_rank, value in enumerate(values, 1):
        source = _source_id(value)
        if source not in rows:
            rows[source] = {
                "source": source,
                "source_rank": len(rows) + 1,
                "first_chunk_rank": chunk_rank,
                "chunk_count": 0,
            }
        rows[source]["chunk_count"] += 1
    return list(rows.values())


def _expected_source_diagnostics(expected: list[str], audit: list[dict]) -> list[dict]:
    diagnostics = []
    for wanted in expected:
        matches = [row for row in audit if _source_matches(wanted, str(row.get("source", "")))]
        if not matches:
            diagnostics.append({"expected_source": wanted, "status": "not_in_candidate_pool"})
            continue
        selected = [row for row in matches if row.get("disposition") == "selected"]
        best = min(
            matches,
            key=lambda row: (
                row.get("rerank_rank") if row.get("rerank_rank") is not None else 10**9,
                row.get("fallback_rrf_rank") if row.get("fallback_rrf_rank") is not None else 10**9,
                row.get("pre_diversity_rank") if row.get("pre_diversity_rank") is not None else 10**9,
            ),
        )
        diagnostics.append({
            "expected_source": wanted,
            "status": "selected" if selected else str(best.get("disposition", "unknown")),
            "best_pre_diversity_rank": best.get("pre_diversity_rank"),
            "best_dense_rank": best.get("dense_rank"),
            "best_bm25_rank": best.get("bm25_rank"),
            "union_rank": best.get("union_rank"),
            "fallback_rrf_rank": best.get("fallback_rrf_rank"),
            "rerank_rank": best.get("rerank_rank"),
            "model_rerank_rank": best.get("model_rerank_rank"),
            "final_rank": next((row.get("final_rank") for row in matches if row.get("disposition") == "selected"), None),
            "retrieval_stage": best.get("retrieval_stage"),
            "best_rrf_score": best.get("rrf_score"),
            "combined_score": best.get("combined_score"),
            "entity_match_score": best.get("entity_match_score"),
            "process_exact_match": best.get("process_exact_match", False),
            "process_prior_applied": best.get("process_prior_applied", False),
            "process_prior_delta": best.get("process_prior_delta", 0.0),
            "source_cap_filtered": any(row.get("disposition") == "source_cap_filtered" for row in matches),
            "entity_guard_triggered": any(row.get("entity_guard_triggered") for row in matches),
            "candidate_entities": best.get("candidate_entities", []),
            "source_role": best.get("source_role"),
            "combined_rank": best.get("combined_rank"),
            "best_retrieval_relevance": best.get("retrieval_relevance"),
            "candidate_count": len(matches),
            "selected_chunk_count": len(selected),
        })
    return diagnostics


def _unique_in_order(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _retrieval_config_snapshot() -> dict:
    return {
        "bm25_mode": settings.retriever_bm25_mode,
        "max_entity_bm25_queries": settings.retriever_max_entity_bm25_queries,
        "entity_bm25_top_k": settings.retriever_entity_bm25_top_k,
        "separate_entities": settings.retriever_separate_entities,
        "source_role_priority": settings.retriever_source_role_priority,
        "entity_guard": settings.retriever_entity_guard,
        "process_guide_prior": settings.retriever_process_guide_prior,
        "process_guide_prior_bonus": settings.retriever_process_guide_prior_bonus,
        "low_authority_prior": settings.retriever_low_authority_prior,
        "low_authority_penalty": settings.retriever_low_authority_penalty,
        "anthropometry_prior": settings.retriever_anthropometry_prior,
        "anthropometry_prior_bonus": settings.retriever_anthropometry_prior_bonus,
        "ranking_mode": settings.retriever_ranking_mode,
        "rerank_weight": settings.retriever_rerank_weight,
        "explicit_entity_weight": settings.retriever_explicit_entity_weight,
        "expanded_entity_weight": settings.retriever_expanded_entity_weight,
        "rrf_weight": settings.retriever_rrf_weight,
        "source_cap": settings.retriever_source_cap,
        "rerank_top_k": settings.retriever_rerank_top_k,
        "union_max_candidates": settings.retriever_union_max_candidates,
    }


def run(path: Path, per_category: int, top_k: int) -> dict:
    del per_category  # The dedicated 10-question file is already fixed.
    questions = load_questions(path)
    rows = []
    started_all = time.perf_counter()
    for item in questions:
        started = time.perf_counter()
        documents = retrieve_documents(item["question"], top_k)
        elapsed = time.perf_counter() - started
        retrieved_sources = [str(doc.metadata.get("source", "")) for doc in documents]
        relevant = {_source_id(source) for source in item.get("expected_sources", [])}
        chunk_ids = [str(doc.metadata.get("chunk_id", "")) for doc in documents]
        ranked = _unique_in_order([_source_id(source) for source in retrieved_sources])
        expected = [_source_id(source) for source in item.get("expected_sources", [])]
        missing_sources = [
            source
            for source in expected
            if not any(_source_matches(source, actual) for actual in retrieved_sources)
        ]
        source_counts = {}
        for source in retrieved_sources:
            source_id = _source_id(source)
            source_counts[source_id] = source_counts.get(source_id, 0) + 1
        source_ranking_after_dedupe = _source_ranking(retrieved_sources)
        candidate_audit = (
            documents[0].metadata.get("retrieval_candidate_audit", [])
            if documents
            else []
        )
        run_diagnostics = documents[0].metadata.get("reranker_status", {}) if documents else {}
        first_metadata = documents[0].metadata if documents else {}
        audit_entities = {
            "explicit_entities": first_metadata.get("explicit_entities", []),
            "expanded_entities": first_metadata.get("expanded_entities", []),
            "explicit_entity_ids": first_metadata.get("explicit_entity_ids", []),
            "expanded_entity_ids": first_metadata.get("expanded_entity_ids", []),
            "candidate_entity_ids": first_metadata.get(
                "retrieval_candidate_entity_ids",
                first_metadata.get("candidate_entity_ids", []),
            ),
            "retrieval_candidate_entities": first_metadata.get("retrieval_candidate_entities", []),
            "retrieval_candidate_entity_ids": first_metadata.get("retrieval_candidate_entity_ids", []),
            "retrieval_candidate_chunk_ids": first_metadata.get("retrieval_candidate_chunk_ids", []),
            "candidate_entity_diagnostics": first_metadata.get("candidate_entity_diagnostics", []),
        }
        expected_source_diagnostics = _expected_source_diagnostics(
            item.get("expected_sources", []), candidate_audit
        )
        first_hit = _first_hit_rank(item.get("expected_sources", []), documents)
        if first_hit == 1:
            mrr_category = "first_hit_rank_1"
        elif first_hit is None:
            mrr_category = "expected_source_not_in_final"
        elif any(item.get("source_cap_filtered") for item in expected_source_diagnostics):
            mrr_category = "source_cap_or_final_diversity"
        elif any(item.get("entity_guard_triggered") for item in expected_source_diagnostics):
            mrr_category = "entity_guard_promoted"
        else:
            mrr_category = "first_hit_rank_gt_1"
        mrr_attribution = {
            "category": mrr_category,
            "mrr": retrieval_metrics(ranked, relevant)["mrr"],
            "first_hit_rank": first_hit,
            "first_hit_rank_after_dedupe": next(
                (
                    rank
                    for rank, source in enumerate(ranked, 1)
                    if any(_source_matches(item, source) for item in expected)
                ),
                None,
            ),
            "expected_sources": expected_source_diagnostics,
        }
        rows.append({
            # The dataset's own id, so a report can key on data instead of on the
            # row's position.  `Run-RBaseline.ps1` used to synthesise `k01…k10`
            # from the loop index, which silently produces the wrong label if the
            # dataset is ever reordered.
            "question_id": str(item.get("id") or ""),
            "question": item["question"],
            "expanded_queries": expand_query(item["question"]),
            **audit_entities,
            "category": item.get("category", ""),
            "source_hit": round(_source_hit(item.get("expected_sources", []), documents), 4),
            "retrieved_sources": retrieved_sources,
            "unique_sources": _unique_in_order([_source_id(source) for source in retrieved_sources]),
            "missing_sources": missing_sources,
            "expected_source_ids": expected,
            "retrieved_source_ids": [_source_id(source) for source in retrieved_sources],
            "first_hit_rank": first_hit,
            "source_counts": source_counts,
            "source_count_before_dedupe": len(retrieved_sources),
            "source_count_after_dedupe": len(source_counts),
            "retrieved_sources_deduped": ranked,
            "first_hit_rank_after_dedupe": next(
                (
                    rank
                    for rank, source in enumerate(ranked, 1)
                    if any(_source_matches(item, source) for item in expected)
                ),
                None,
            ),
            "chunk_ranking_before_source_dedupe": [
                {"chunk_rank": rank, "source": _source_id(source)}
                for rank, source in enumerate(retrieved_sources, 1)
            ],
            "source_ranking_after_dedupe": source_ranking_after_dedupe,
            "candidate_audit": candidate_audit,
            "dense_top_ids": first_metadata.get("retrieval_dense_top_ids", []),
            "bm25_top_ids": first_metadata.get("retrieval_bm25_top_ids", []),
            "union_ids": first_metadata.get("retrieval_union_ids", []),
            "union_before_count": first_metadata.get("retrieval_union_before_count"),
            "union_after_count": first_metadata.get("retrieval_union_after_count"),
            "union_truncated": first_metadata.get("retrieval_union_truncated", False),
            "bm25_query_hits": first_metadata.get("retrieval_bm25_query_hits", []),
            "bm25_query_plan": first_metadata.get("bm25_query_plan", []),
            "reranked_top20_ids": first_metadata.get("retrieval_reranked_top20_ids", []),
            "fallback_rrf_top20_ids": first_metadata.get("retrieval_fallback_rrf_top20_ids", []),
            "final_top10_ids": first_metadata.get("retrieval_final_top10_ids", chunk_ids),
            "reranker": run_diagnostics,
            "expected_source_diagnostics": expected_source_diagnostics,
            "mrr_attribution": mrr_attribution,
            "source_cap": documents[0].metadata.get("source_cap") if documents else None,
            "retrieved_chunk_ids": chunk_ids,
            "ranked_chunks": [
                {
                    "chunk_id": str(doc.metadata.get("chunk_id", "")),
                    "source": str(doc.metadata.get("source", "")),
                    "dense_rank": doc.metadata.get("dense_rank"),
                     "bm25_rank": doc.metadata.get("bm25_rank"),
                     "union_rank": doc.metadata.get("union_rank"),
                    "fallback_rrf_rank": doc.metadata.get("fallback_rrf_rank"),
                    "rerank_rank": doc.metadata.get("rerank_rank"),
                    "model_rerank_rank": doc.metadata.get("model_rerank_rank"),
                    "retrieval_stage": doc.metadata.get("retrieval_stage"),
                    "rrf_score": doc.metadata.get("rrf_score"),
                    "rerank_score": doc.metadata.get("rerank_score"),
                    "combined_score": doc.metadata.get("combined_score"),
                    "combined_rank": doc.metadata.get("combined_rank"),
                    "query_entities": doc.metadata.get("query_entities", []),
                    "explicit_entities": doc.metadata.get("explicit_entities", []),
                    "expanded_entities": doc.metadata.get("expanded_entities", []),
                    "explicit_entity_ids": doc.metadata.get("explicit_entity_ids", []),
                    "expanded_entity_ids": doc.metadata.get("expanded_entity_ids", []),
                    "candidate_entity_ids": doc.metadata.get("candidate_entity_ids", []),
                    "candidate_entities": doc.metadata.get("candidate_entities", []),
                    "entity_match_score": doc.metadata.get("entity_match_score"),
                    "process_exact_match": doc.metadata.get("process_exact_match", False),
                    "process_exact_term_count": doc.metadata.get("process_exact_term_count", 0),
                    "process_prior_applied": doc.metadata.get("process_prior_applied", False),
                    "process_prior_reason": doc.metadata.get("process_prior_reason"),
                    "process_prior_delta": doc.metadata.get("process_prior_delta", 0.0),
                    "low_authority_penalty_applied": doc.metadata.get("low_authority_penalty_applied", False),
                    "low_authority_penalty_delta": doc.metadata.get("low_authority_penalty_delta", 0.0),
                    "authority_prior_reason": doc.metadata.get("authority_prior_reason"),
                    "low_authority_score_capped": doc.metadata.get("low_authority_score_capped", False),
                    "low_authority_score_ceiling": doc.metadata.get("low_authority_score_ceiling"),
                    "anthropometry_prior_applied": doc.metadata.get("anthropometry_prior_applied", False),
                    "anthropometry_prior_reason": doc.metadata.get("anthropometry_prior_reason"),
                    "anthropometry_prior_delta": doc.metadata.get("anthropometry_prior_delta", 0.0),
                    "source_role": doc.metadata.get("source_role"),
                    "query_role_match": doc.metadata.get("query_role_match"),
                    "entity_guard_triggered": doc.metadata.get("entity_guard_triggered", False),
                    "entity_guard_reason": doc.metadata.get("entity_guard_reason"),
                    "guarded_entity_id": doc.metadata.get("guarded_entity_id"),
                    "entity_bm25_ranks": doc.metadata.get("entity_bm25_ranks", {}),
                    "source_rank": doc.metadata.get("source_rank"),
                    "pre_diversity_rank": doc.metadata.get("pre_diversity_rank"),
                    "source_rank_before_diversity": doc.metadata.get("source_rank_before_diversity"),
                    "source_rank_after_diversity": doc.metadata.get("source_rank_after_diversity"),
                    "final_rank": doc.metadata.get("final_rank"),
                    "retrieval_exact_matches": doc.metadata.get("retrieval_exact_matches", []),
                    "retrieval_threshold_passed": doc.metadata.get("retrieval_threshold_passed"),
                    "dynamic_rrf_threshold": doc.metadata.get("dynamic_rrf_threshold"),
                    "lexical_query": doc.metadata.get("lexical_query"),
                    "bm25_query_hits": doc.metadata.get("bm25_query_hits", []),
                }
                for doc in documents
            ],
            "source_level_metrics": retrieval_metrics(ranked, relevant),
            "chunk_level_metrics": (
                retrieval_metrics(
                    chunk_ids,
                    {str(value) for value in item.get("expected_chunks", [])},
                )
                if item.get("expected_chunks")
                else None
            ),
            "retrieval_metrics": retrieval_metrics(ranked, relevant),
            "latency_seconds": round(elapsed, 3),
            "documents": len(documents),
        })
    aggregate = {
        "question_count": len(rows),
        "source_hit_mean": round(sum(row["source_hit"] for row in rows) / max(1, len(rows)), 4),
        "recall_at_5_mean": round(sum(row["source_level_metrics"]["recall_at_5"] for row in rows) / max(1, len(rows)), 4),
        "recall_at_10_mean": round(sum(row["source_level_metrics"]["recall_at_10"] for row in rows) / max(1, len(rows)), 4),
        "mrr_mean": round(sum(row["source_level_metrics"]["mrr"] for row in rows) / max(1, len(rows)), 4),
        "ndcg_at_10_mean": round(sum(row["source_level_metrics"]["ndcg_at_10"] for row in rows) / max(1, len(rows)), 4),
        "chunk_recall_at_10_mean": (
            round(
                sum(
                    row["chunk_level_metrics"]["recall_at_10"]
                    for row in rows
                    if row["chunk_level_metrics"] is not None
                )
                / sum(1 for row in rows if row["chunk_level_metrics"] is not None),
                4,
            )
            if any(row["chunk_level_metrics"] is not None for row in rows)
            else None
        ),
        "latency_mean_seconds": round(sum(row["latency_seconds"] for row in rows) / max(1, len(rows)), 3),
        "reranker_success_rate": round(
            sum(1 for row in rows if row.get("reranker", {}).get("status") in {"cuda", "cpu_fallback"})
            / max(1, len(rows)), 4
        ),
        "reranker_latency_mean_seconds": round(
            sum(float(row.get("reranker", {}).get("latency_seconds") or 0) for row in rows)
            / max(1, len(rows)), 3
        ),
        "bm25_only_union_count": sum(
            sum(1 for item in row.get("candidate_audit", []) if item.get("retrieval_stage") == "bm25_only")
            for row in rows
        ),
        "bm25_only_rerank_top20_count": sum(
            sum(1 for item in row.get("candidate_audit", []) if item.get("retrieval_stage") == "bm25_only" and item.get("rerank_rank", 999) <= 20)
            for row in rows
        ),
        "bm25_only_final10_count": sum(
            sum(1 for item in row.get("candidate_audit", []) if item.get("retrieval_stage") == "bm25_only" and item.get("disposition") == "selected")
            for row in rows
        ),
        "bm25_only_promoted_to_top20_count": sum(
            sum(
                1 for item in row.get("candidate_audit", [])
                if item.get("retrieval_stage") == "bm25_only"
                and item.get("model_rerank_rank") is not None
                and item.get("combined_rank") is not None
                and item.get("model_rerank_rank") > 20
                and item.get("combined_rank") <= 20
            )
            for row in rows
        ),
        "bm25_only_promoted_to_final10_count": sum(
            sum(
                1 for item in row.get("candidate_audit", [])
                if item.get("retrieval_stage") == "bm25_only"
                and item.get("disposition") == "selected"
                and item.get("fallback_rrf_rank") is not None
                and item.get("fallback_rrf_rank") > 10
            )
            for row in rows
        ),
        "entity_guard_trigger_count": sum(
            sum(1 for item in row.get("candidate_audit", []) if item.get("entity_guard_triggered"))
            for row in rows
        ),
        "elapsed_seconds": round(time.perf_counter() - started_all, 3),
        "mrr_attribution_counts": {
            category: sum(
                1 for row in rows
                if row.get("mrr_attribution", {}).get("category") == category
            )
            for category in sorted({
                row.get("mrr_attribution", {}).get("category")
                for row in rows
                if row.get("mrr_attribution", {}).get("category")
            })
        },
        "process_prior_applied_count": sum(
            sum(
                1 for item in row.get("candidate_audit", [])
                if item.get("process_prior_applied")
            )
            for row in rows
        ),
    }
    return {
        "strategy": settings.chunking_strategy,
        "chroma_dir": str(settings.chroma_dir),
        "collection_name": settings.collection_name,
        "top_k": top_k,
        "retrieval_config": _retrieval_config_snapshot(),
        "aggregate": aggregate,
        "results": rows,
    }


def main() -> int:
    _configure_console_encoding()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/chunking_evaluation/chunking_qa_10.json")
    parser.add_argument("--per-category", type=int, default=2, help=argparse.SUPPRESS)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    result = run(Path(args.dataset), args.per_category, args.top_k)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
