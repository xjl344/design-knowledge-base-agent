"""Observable local cross-encoder reranking with CUDA-to-CPU fallback."""

from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from langchain_core.documents import Document

from config import settings


@dataclass(frozen=True)
class RerankOutcome:
    documents: list[Document]
    status: str
    device: str | None
    model_path: str | None
    candidate_count: int
    latency_seconds: float
    fallback_reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status in {"cuda", "cpu_fallback"}


def _model_path() -> str | None:
    return str(settings.reranker_model_path) if settings.reranker_model_path else None


def _cuda_available() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _clear_cuda_cache() -> None:
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


@lru_cache(maxsize=2)
def _load_model(device: str) -> Any:
    from sentence_transformers import CrossEncoder
    model = CrossEncoder(
        _model_path(), device=device, max_length=settings.reranker_max_length,
        local_files_only=True,
    )
    if device == "cuda" and settings.reranker_use_fp16:
        model.model.half()
    return model


def _passage(document: Document) -> str:
    metadata = document.metadata
    section = metadata.get("section_path") or metadata.get("section", "")
    header = "\n".join(
        value for value in (
            f"source: {metadata.get('source', '')}",
            f"title: {metadata.get('title') or metadata.get('source_title', '')}",
            f"section_path: {section}",
        ) if value.rsplit(":", 1)[-1].strip()
    )
    return f"{header}\n\n{document.page_content or ''}".strip()


def _predict(question: str, documents: list[Document], device: str) -> list[Document]:
    model = _load_model(device)
    scores = model.predict(
        [(question, _passage(document)) for document in documents],
        batch_size=settings.reranker_batch_size, show_progress_bar=False,
    )
    ranked = sorted(zip(documents, scores), key=lambda item: float(item[1]), reverse=True)
    result: list[Document] = []
    for rank, (document, score) in enumerate(ranked, 1):
        document.metadata["rerank_score"] = round(float(score), 6)
        document.metadata["rerank_rank"] = rank
        result.append(document)
    return result


def rerank(question: str, documents: list[Document], top_k: int | None = None) -> RerankOutcome:
    """Rerank the full union and report every fallback instead of hiding it."""
    started = time.perf_counter()
    model_path = _model_path()
    candidate_count = len(documents)
    if not settings.reranker_enabled:
        return RerankOutcome(list(documents), "disabled", None, model_path, candidate_count, round(time.perf_counter() - started, 6), "RERANKER_ENABLED=false")
    if not model_path:
        return RerankOutcome(list(documents), "failed", None, None, candidate_count, round(time.perf_counter() - started, 6), "RERANKER_MODEL_PATH is not configured")
    if not documents:
        device = "cuda" if _cuda_available() else "cpu"
        return RerankOutcome([], "cuda" if device == "cuda" else "cpu_fallback", device, model_path, 0, round(time.perf_counter() - started, 6))
    requested = settings.reranker_device
    try_cuda = requested in {"auto", "cuda"} and _cuda_available()
    cuda_error: str | None = None
    if try_cuda:
        try:
            return RerankOutcome(_predict(question, documents, "cuda"), "cuda", "cuda", model_path, candidate_count, round(time.perf_counter() - started, 6))
        except Exception as exc:
            cuda_error = f"CUDA reranker failed: {type(exc).__name__}: {exc}"
            _load_model.cache_clear(); _clear_cuda_cache()
    try:
        ranked = _predict(question, documents, "cpu")
        reason = cuda_error
        if reason is None and requested == "auto":
            reason = "CUDA unavailable; using CPU"
        elif reason is None and requested == "cuda":
            reason = "CUDA requested but unavailable"
        return RerankOutcome(ranked, "cpu_fallback", "cpu", model_path, candidate_count, round(time.perf_counter() - started, 6), reason)
    except Exception as exc:
        cpu_error = f"CPU reranker failed: {type(exc).__name__}: {exc}"
        _load_model.cache_clear()
        return RerankOutcome(list(documents), "failed", "cpu", model_path, candidate_count, round(time.perf_counter() - started, 6), f"{cuda_error}; {cpu_error}" if cuda_error else cpu_error)
