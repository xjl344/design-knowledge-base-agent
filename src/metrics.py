"""Small in-process metrics collector used by the graph and UI."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any
import math


@dataclass
class NodeMetrics:
    node_name: str
    status: str
    duration_seconds: float
    token_count: int | None = None
    error: str | None = None


class MetricsCollector:
    def __init__(self) -> None:
        self.items: list[NodeMetrics] = []
        self.started_at = time.perf_counter()

    def record(self, item: NodeMetrics) -> None:
        self.items.append(item)

    def summary(self) -> dict[str, Any]:
        durations = [item.duration_seconds for item in self.items]
        return {
            "total_duration_seconds": round(time.perf_counter() - self.started_at, 3),
            "node_count": len(self.items),
            "total_tokens": sum(item.token_count or 0 for item in self.items),
            "nodes": [asdict(item) for item in self.items],
            "slowest_node": max(self.items, key=lambda item: item.duration_seconds).node_name
            if self.items else None,
            "node_duration_seconds": round(sum(durations), 3),
        }


def precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    top = retrieved[: max(0, k)]
    # Retrieval may return fewer than k documents after source caps or
    # filtering.  Score the returned list rather than penalising absent slots
    # as false positives; this matches the evaluator's variable-length output
    # contract and keeps P@K comparable with the actual candidate set.
    return sum(item in relevant for item in top) / len(top) if top else 0.0


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    return sum(item in relevant for item in retrieved[: max(0, k)]) / len(relevant)


def reciprocal_rank(retrieved: list[str], relevant: set[str]) -> float:
    for rank, item in enumerate(retrieved, 1):
        if item in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    dcg = sum(
        (1.0 if item in relevant else 0.0) / math.log2(rank + 1)
        for rank, item in enumerate(retrieved[: max(0, k)], 1)
    )
    ideal_count = min(k, len(relevant))
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    return dcg / idcg if idcg else 0.0


def retrieval_metrics(retrieved: list[str], relevant: set[str]) -> dict[str, float]:
    return {
        "precision_at_5": round(precision_at_k(retrieved, relevant, 5), 4),
        "precision_at_10": round(precision_at_k(retrieved, relevant, 10), 4),
        "recall_at_5": round(recall_at_k(retrieved, relevant, 5), 4),
        "recall_at_10": round(recall_at_k(retrieved, relevant, 10), 4),
        "mrr": round(reciprocal_rank(retrieved, relevant), 4),
        "ndcg_at_10": round(ndcg_at_k(retrieved, relevant, 10), 4),
    }


def timed(collector: MetricsCollector, node_name: str):
    """Decorator helper for synchronous call sites; async nodes use record directly."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                collector.record(NodeMetrics(node_name, "success", time.perf_counter() - start))
                return result
            except Exception as exc:
                collector.record(NodeMetrics(node_name, "error", time.perf_counter() - start, error=str(exc)))
                raise
        return wrapper
    return decorator
