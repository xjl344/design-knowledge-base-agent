"""Per-query latency, cache-hit, and estimated token/cost ledger.

Implements RICE P0 #2 (``docs/backlog-rice.md``) with zero third-party
dependencies. All latency summaries report p50 / p95 rather than outlier-prone
means, matching the repository's evaluation contract.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from threading import Lock
from typing import Any


def estimate_tokens_from_text(text: str) -> int:
    """Heuristic token estimator for mixed Chinese/English text.

    CJK characters typically map close to 1-1.5 tokens per char, while Latin
    text maps to ~0.25-0.33 tokens per char. This stays deterministic and
    requires no tokenizer download.
    """
    raw = str(text or "")
    if not raw:
        return 0
    cjk = sum(1 for ch in raw if "\u4e00" <= ch <= "\u9fff")
    other = len(raw) - cjk
    return max(1, int(math.ceil(cjk * 1.2 + other / 4.0)))


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    rank = (len(ordered) - 1) * max(0.0, min(100.0, pct)) / 100.0
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return round(ordered[low], 3)
    weight = rank - low
    return round(ordered[low] * (1.0 - weight) + ordered[high] * weight, 3)


@dataclass
class QueryCostRecord:
    created_at: str
    session_id: str
    question_hash: str
    route: str
    question_type: str
    generator_backend: str
    delivery_status: str
    deliverable: bool
    evidence_count: int
    cache_hit: bool
    total_latency_seconds: float
    retrieval_seconds: float
    reranker_seconds: float
    generation_seconds: float
    prompt_tokens_est: int
    completion_tokens_est: int
    total_tokens_est: int
    estimated_cost_cny: float
    input_price_per_1m_cny: float
    output_price_per_1m_cny: float
    snapshot_id: str = "unknown"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "QueryCostRecord":
        known = {name: payload[name] for name in cls.__dataclass_fields__ if name in payload}
        return cls(**known)


def build_query_cost_record(
    *,
    session_id: str,
    question_hash: str,
    question: str,
    answer: str,
    state: dict[str, Any],
    generator_backend: str,
    input_price_per_1m_cny: float,
    output_price_per_1m_cny: float,
    snapshot_id: str = "unknown",
    wall_seconds: float | None = None,
) -> QueryCostRecord:
    """Build a per-query cost & latency record from the completed GraphState."""
    trace = state.get("execution_trace", []) or []
    trace_total = round(sum(float(item.get("duration_seconds", 0.0) or 0.0) for item in trace), 3)
    total_latency = round(float(wall_seconds), 3) if wall_seconds is not None else trace_total

    retrieval_nodes = {"retrieve", "execute_plan"}
    generation_nodes = {"generate", "aggregate_results", "validate_delivery"}
    retrieval_sec = round(
        sum(
            float(item.get("duration_seconds", 0.0) or 0.0)
            for item in trace
            if item.get("node") in retrieval_nodes
        ),
        3,
    )
    generation_sec = round(
        sum(
            float(item.get("duration_seconds", 0.0) or 0.0)
            for item in trace
            if item.get("node") in generation_nodes
        ),
        3,
    )

    profile = state.get("retrieval_profile") or {}
    docs = state.get("documents", []) or []
    if not profile and docs:
        first_meta = getattr(docs[0], "metadata", None)
        if isinstance(first_meta, dict):
            profile = first_meta.get("retrieval_profile") or {}
    cache_hit = bool(profile.get("cache_hit", False))
    reranker_sec = round(float(profile.get("reranker_seconds", 0.0) or 0.0), 3)

    context_chars = sum(len(str(getattr(doc, "page_content", "") or "")) for doc in docs)
    # Prompt includes system rules (~400 chars) + question + retrieved context.
    prompt_tokens = estimate_tokens_from_text(question) + max(0, int(math.ceil(context_chars * 0.7))) + 250
    completion_tokens = estimate_tokens_from_text(answer)
    total_tokens = prompt_tokens + completion_tokens

    cost_cny = (
        prompt_tokens * float(input_price_per_1m_cny) / 1_000_000.0
        + completion_tokens * float(output_price_per_1m_cny) / 1_000_000.0
    )

    sources = state.get("sources", []) or []
    return QueryCostRecord(
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        session_id=str(session_id or ""),
        question_hash=str(question_hash or ""),
        route=str(state.get("route", "unknown")),
        question_type=str(state.get("question_type", "simple")),
        generator_backend=str(generator_backend or "v2"),
        delivery_status=str(state.get("delivery_status") or state.get("status") or "unknown"),
        deliverable=bool(state.get("deliverable", False)),
        evidence_count=len(sources) or len(docs),
        cache_hit=cache_hit,
        total_latency_seconds=total_latency,
        retrieval_seconds=retrieval_sec,
        reranker_seconds=reranker_sec,
        generation_seconds=generation_sec,
        prompt_tokens_est=prompt_tokens,
        completion_tokens_est=completion_tokens,
        total_tokens_est=total_tokens,
        estimated_cost_cny=round(cost_cny, 6),
        input_price_per_1m_cny=float(input_price_per_1m_cny),
        output_price_per_1m_cny=float(output_price_per_1m_cny),
        snapshot_id=str(snapshot_id or "unknown"),
    )


class QueryCostLedger:
    """Append-only JSONL ledger of per-query latency and cost observations."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = Lock()

    def append(self, record: QueryCostRecord) -> QueryCostRecord:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record.as_dict(), ensure_ascii=False) + "\n")
        return record

    def load_all(self) -> list[QueryCostRecord]:
        if not self.path.is_file():
            return []
        with self._lock:
            return [
                QueryCostRecord.from_dict(json.loads(raw))
                for raw in self.path.read_text(encoding="utf-8").splitlines()
                if raw.strip()
            ]


def summarize_query_costs(records: list[QueryCostRecord]) -> dict[str, Any]:
    """Summarize latency (p50/p95), cold-start rate, and estimated query cost."""
    total = len(records)
    if total == 0:
        return {
            "total_queries": 0,
            "cold_start_count": 0,
            "cold_start_rate": None,
            "deliverable_count": 0,
            "latency_p50_seconds": None,
            "latency_p95_seconds": None,
            "tokens_p50": None,
            "cost_p50_cny": None,
            "total_estimated_cost_cny": 0.0,
        }
    latencies = [r.total_latency_seconds for r in records]
    tokens = [float(r.total_tokens_est) for r in records]
    costs = [r.estimated_cost_cny for r in records]
    cold_count = sum(1 for r in records if not r.cache_hit)
    deliverable_count = sum(1 for r in records if r.deliverable)
    return {
        "total_queries": total,
        "cold_start_count": cold_count,
        "cold_start_rate": round(cold_count / total, 4),
        "deliverable_count": deliverable_count,
        "latency_p50_seconds": percentile(latencies, 50),
        "latency_p95_seconds": percentile(latencies, 95),
        "tokens_p50": percentile(tokens, 50),
        "cost_p50_cny": percentile(costs, 50),
        "total_estimated_cost_cny": round(sum(costs), 6),
    }


def format_cost_markdown(record: QueryCostRecord) -> str:
    """Compact sidebar panel for per-query latency and estimated cost."""
    cache_label = "缓存命中" if record.cache_hit else "冷检索"
    return (
        "**本次耗时与成本账单（估算）**\n\n"
        f"- 总耗时：**{record.total_latency_seconds:.1f} s**（{cache_label}；"
        f"检索 {record.retrieval_seconds:.1f} s / 重排 {record.reranker_seconds:.1f} s / 生成 {record.generation_seconds:.1f} s）\n"
        f"- 估算 Token：**{record.total_tokens_est}**（输入 ~{record.prompt_tokens_est} / 输出 ~{record.completion_tokens_est}）\n"
        f"- 估算费用：**¥{record.estimated_cost_cny:.4f}**（按 ¥{record.input_price_per_1m_cny:g}/¥{record.output_price_per_1m_cny:g} per 1M tokens）"
    )
