"""Upload retrieval-only F/R/P chunking comparisons to LangSmith."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QA_FILE = ROOT / "data" / "chunking_evaluation" / "chunking_qa_10.json"
DATASET_NAME = "design-knowledge-chunking-10"


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def load_questions(path: Path = QA_FILE) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    questions = data.get("questions", [])
    if len(questions) != 10:
        raise ValueError(f"chunking evaluation set must contain 10 questions, got {len(questions)}")
    return questions


def _normalise(value: str) -> str:
    return "".join(str(value or "").replace("\\", "/").casefold().split())


def _source_id(value: str) -> str:
    return _normalise(value).rsplit("/", 1)[-1]


def _source_matches(expected: str, actual: str) -> bool:
    expected_normalised = _normalise(expected)
    actual_normalised = _normalise(actual)
    return (
        _source_id(expected_normalised) == _source_id(actual_normalised)
        or expected_normalised in actual_normalised
    )


def _source_ranking(values: list[str]) -> list[dict]:
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
            "final_rank": next((row.get("final_rank") for row in matches if row.get("disposition") == "selected"), None),
            "retrieval_stage": best.get("retrieval_stage"),
            "best_rrf_score": best.get("rrf_score"),
            "best_retrieval_relevance": best.get("retrieval_relevance"),
            "candidate_count": len(matches),
            "selected_chunk_count": len(selected),
        })
    return diagnostics


def source_hit(run, example) -> dict:
    expected = (example.outputs or {}).get("expected_sources", [])
    predicted = (run.outputs or {}).get("retrieved_sources", [])
    hits = sum(
        1 for item in expected
        if any(_source_matches(item, source) for source in predicted)
    )
    score = hits / len(expected) if expected else (1.0 if not predicted else 0.0)
    return {
        "key": "source_hit",
        "score": score,
        "comment": f"期望来源 {len(expected)} 个，命中 {hits} 个",
    }


def _source_metric(run, example, metric: str) -> dict:
    expected = [_source_id(item) for item in (example.outputs or {}).get("expected_sources", [])]
    predicted = [_source_id(item) for item in (run.outputs or {}).get("retrieved_sources", [])]
    ranked = list(dict.fromkeys(predicted))
    relevant = set(expected)
    if metric == "recall_at_5":
        score = sum(item in relevant for item in ranked[:5]) / len(relevant) if relevant else 0.0
    elif metric == "recall_at_10":
        score = sum(item in relevant for item in ranked[:10]) / len(relevant) if relevant else 0.0
    elif metric == "mrr":
        score = next((1.0 / rank for rank, item in enumerate(ranked, 1) if item in relevant), 0.0)
    else:
        ideal = min(10, len(relevant))
        dcg = sum((1.0 if item in relevant else 0.0) / math.log2(rank + 1) for rank, item in enumerate(ranked[:10], 1))
        idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal + 1))
        score = dcg / idcg if idcg else 0.0
    return {"key": metric, "score": round(score, 6)}


def recall_at_5(run, example):
    return _source_metric(run, example, "recall_at_5")


def recall_at_10(run, example):
    return _source_metric(run, example, "recall_at_10")


def mrr(run, example):
    return _source_metric(run, example, "mrr")


def ndcg_at_10(run, example):
    return _source_metric(run, example, "ndcg_at_10")


def chunk_recall_at_10(run, example):
    """Score only when the dataset provides explicit expected chunk IDs."""
    expected = (example.outputs or {}).get("expected_chunks", [])
    if not expected:
        return {"key": "chunk_recall_at_10", "score": None, "status": "not_scored", "comment": "数据集未标注 expected_chunks"}
    ranked = (run.outputs or {}).get("retrieved_chunk_ids", [])[:10]
    expected_ids = set(str(item) for item in expected)
    score = sum(item in expected_ids for item in ranked) / len(expected_ids) if expected_ids else 0.0
    return {"key": "chunk_recall_at_10", "score": round(min(1.0, score), 6)}


def build_evaluators(questions: list[dict]) -> list:
    """Register only evaluators backed by valid labels in this dataset."""
    evaluators = [source_hit, recall_at_5, recall_at_10, mrr, ndcg_at_10]
    # A None score is displayed by LangSmith as evaluator failure. The source
    # benchmark has no expected chunk IDs, so omit chunk scoring entirely.
    if any(question.get("expected_chunks") for question in questions):
        evaluators.append(chunk_recall_at_10)
    return evaluators


def make_target():
    from config import settings
    from src.retriever import retrieve_documents
    from src.query_expansion import expand_query

    def target(example: dict) -> dict:
        started = time.perf_counter()
        documents = retrieve_documents(example["question"], 10)
        sources = [str(doc.metadata.get("source", "")) for doc in documents]
        unique_sources = list(dict.fromkeys(_source_id(source) for source in sources))
        expected = [str(item) for item in example.get("expected_sources", [])]
        first_metadata = documents[0].metadata if documents else {}
        missing = [
            item
            for item in expected
            if not any(_source_matches(item, source) for source in sources)
        ]
        source_counts = {}
        for source in sources:
            source_id = _source_id(source)
            source_counts[source_id] = source_counts.get(source_id, 0) + 1
        first_hit_rank = next(
            (
                rank
                for rank, source in enumerate(sources, 1)
                if any(_source_matches(item, source) for item in expected)
            ),
            None,
        )
        return {
            "retrieved_sources": sources,
            "expanded_queries": expand_query(example["question"]),
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
            "unique_sources": unique_sources,
            "missing_sources": missing,
            "expected_source_ids": [_source_id(item) for item in expected],
            "retrieved_source_ids": [_source_id(source) for source in sources],
            "first_hit_rank": first_hit_rank,
            "source_counts": source_counts,
            "source_count_before_dedupe": len(sources),
            "source_count_after_dedupe": len(unique_sources),
            "retrieved_sources_deduped": [_source_id(source) for source in unique_sources],
            "chunk_ranking_before_source_dedupe": [
                {"chunk_rank": rank, "source": _source_id(source)}
                for rank, source in enumerate(sources, 1)
            ],
            "source_ranking_after_dedupe": _source_ranking(sources),
            "candidate_audit": (
                documents[0].metadata.get("retrieval_candidate_audit", [])
                if documents
                else []
            ),
            "dense_top_ids": documents[0].metadata.get("retrieval_dense_top_ids", []) if documents else [],
            "bm25_top_ids": documents[0].metadata.get("retrieval_bm25_top_ids", []) if documents else [],
            "union_ids": documents[0].metadata.get("retrieval_union_ids", []) if documents else [],
            "union_before_count": first_metadata.get("retrieval_union_before_count"),
            "union_after_count": first_metadata.get("retrieval_union_after_count"),
            "union_truncated": first_metadata.get("retrieval_union_truncated", False),
            "bm25_query_hits": first_metadata.get("retrieval_bm25_query_hits", []),
            "bm25_query_plan": first_metadata.get("bm25_query_plan", []),
            "reranked_top20_ids": documents[0].metadata.get("retrieval_reranked_top20_ids", []) if documents else [],
            "fallback_rrf_top20_ids": documents[0].metadata.get("retrieval_fallback_rrf_top20_ids", []) if documents else [],
            "final_top10_ids": documents[0].metadata.get("retrieval_final_top10_ids", []) if documents else [],
            "reranker": documents[0].metadata.get("reranker_status", {}) if documents else {},
            "expected_source_diagnostics": _expected_source_diagnostics(
                expected,
                documents[0].metadata.get("retrieval_candidate_audit", []) if documents else [],
            ),
            "source_cap": documents[0].metadata.get("source_cap") if documents else None,
            "retrieved_chunk_ids": [str(doc.metadata.get("chunk_id", "")) for doc in documents],
            "retrieval_relevance": [doc.metadata.get("retrieval_relevance") for doc in documents],
            "dense_rank": [doc.metadata.get("dense_rank") for doc in documents],
            "bm25_rank": [doc.metadata.get("bm25_rank") for doc in documents],
            "rrf_score": [doc.metadata.get("rrf_score") for doc in documents],
            "rerank_score": [doc.metadata.get("rerank_score") for doc in documents],
            "ranked_chunks": [
                {
                    "chunk_id": str(doc.metadata.get("chunk_id", "")),
                    "source": str(doc.metadata.get("source", "")),
                    "dense_rank": doc.metadata.get("dense_rank"),
                    "bm25_rank": doc.metadata.get("bm25_rank"),
                    "union_rank": doc.metadata.get("union_rank"),
                    "fallback_rrf_rank": doc.metadata.get("fallback_rrf_rank"),
                    "rerank_rank": doc.metadata.get("rerank_rank"),
                    "retrieval_stage": doc.metadata.get("retrieval_stage"),
                    "rrf_score": doc.metadata.get("rrf_score"),
                    "rerank_score": doc.metadata.get("rerank_score"),
                    "combined_score": doc.metadata.get("combined_score"),
                    "model_rerank_rank": doc.metadata.get("model_rerank_rank"),
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
            "latency_seconds": round(time.perf_counter() - started, 3),
            "chunking_strategy": settings.chunking_strategy,
        }

    return target


def sync_dataset(client, questions: list[dict], dataset_name: str = DATASET_NAME):
    from langsmith.utils import LangSmithNotFoundError

    try:
        dataset = client.read_dataset(dataset_name=dataset_name)
    except LangSmithNotFoundError:
        dataset = client.create_dataset(
            dataset_name=dataset_name,
            description="设计知识库 F/R/P 分块策略纯检索对照集（10题）",
        )
    existing = {
        (item.inputs or {}).get("question", ""): item
        for item in client.list_examples(dataset_id=dataset.id, limit=1000)
    }
    for question in questions:
        outputs = {
            "expected_sources": question.get("expected_sources", []),
            "expected_chunks": question.get("expected_chunks", []),
            "category": question.get("category", ""),
        }
        item = existing.get(question["question"])
        if item is None:
            client.create_examples(dataset_id=dataset.id, examples=[{"inputs": {"question": question["question"]}, "outputs": outputs}])
        elif (item.outputs or {}) != outputs:
            client.update_example(item.id, inputs={"question": question["question"]}, outputs=outputs)
    return dataset


def main() -> int:
    _utf8_console()
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", required=True, choices=["F", "R", "P"])
    parser.add_argument("--dataset", type=Path, default=QA_FILE)
    parser.add_argument("--experiment-prefix", default=None)
    parser.add_argument("--offline", action="store_true", help="只运行本地检索并保存 JSON，不上传 LangSmith")
    args = parser.parse_args()

    os.environ["CHUNKING_STRATEGY"] = args.strategy
    os.environ["CHROMA_DIR"] = str(ROOT / "data" / "chunking_experiments" / args.strategy)
    os.environ["COLLECTION_NAME"] = f"design_knowledge_{args.strategy.lower()}"
    # config.py reads these values at import time; import it only after the
    # strategy-specific environment is set for this process.
    from config import LOG_DIR, settings
    retrieval_config = {
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
        "rerank_weight": settings.retriever_rerank_weight,
        "explicit_entity_weight": settings.retriever_explicit_entity_weight,
        "expanded_entity_weight": settings.retriever_expanded_entity_weight,
        "rrf_weight": settings.retriever_rrf_weight,
        "source_cap": settings.retriever_source_cap,
        "rerank_top_k": settings.retriever_rerank_top_k,
        "union_max_candidates": settings.retriever_union_max_candidates,
    }
    questions = load_questions(args.dataset)
    target = make_target()
    if args.offline:
        rows = [target({"question": item["question"]}) for item in questions]
        output = LOG_DIR / "langsmith_chunking"
        output.mkdir(parents=True, exist_ok=True)
        path = output / f"{args.strategy}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        path.write_text(json.dumps({"strategy": args.strategy, "retrieval_config": retrieval_config, "results": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"offline result written: {path}")
        return 0

    from langsmith import Client
    from langsmith.evaluation import evaluate

    client = Client()
    dataset = sync_dataset(client, questions)
    prefix = args.experiment_prefix or f"design-kb-chunking-{args.strategy}"
    evaluators = build_evaluators(questions)
    evaluate(
        target,
        data=DATASET_NAME,
        evaluators=evaluators,
        experiment_prefix=prefix,
        description=f"F/R/P 分块纯检索对照：{args.strategy}",
        metadata={
            "chunking_strategy": args.strategy,
            "retrieval_config": retrieval_config,
            "collection_name": settings.collection_name,
            "chroma_dir": str(settings.chroma_dir),
            "evaluators": [evaluator.__name__ for evaluator in evaluators],
            "chunk_level_scored": any(question.get("expected_chunks") for question in questions),
        },
        max_concurrency=1,
        client=client,
        blocking=True,
        upload_results=True,
    )
    print(f"LangSmith experiment uploaded: {prefix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
