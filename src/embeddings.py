"""Local BGE-M3 embedding model factory."""

from __future__ import annotations

from functools import lru_cache
import threading

from langchain_huggingface import HuggingFaceEmbeddings

from config import settings


def resolve_embedding_device(configured: str | None = None) -> str:
    device = (configured or settings.embedding_device).lower()
    if device == "gpu":
        device = "cuda"
    try:
        import torch

        if device == "auto":
            return "cuda" if torch.cuda.is_available() else "cpu"
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError(
                f"CUDA embedding was requested, but PyTorch {torch.__version__} "
                "cannot access the GPU. Install the CUDA PyTorch wheel or set "
                "EMBEDDING_DEVICE=cpu explicitly for troubleshooting."
            )
        return device
    except ImportError as exc:
        if device.startswith("cuda"):
            raise RuntimeError("CUDA embedding requires PyTorch") from exc
        return "cpu"


@lru_cache(maxsize=2)
def _build_embeddings(device: str, batch_size: int) -> HuggingFaceEmbeddings:
    settings.validate_embedding_model()
    embeddings = HuggingFaceEmbeddings(
        model_name=str(settings.embedding_model_path),
        model_kwargs={"device": device, "local_files_only": True},
        encode_kwargs={
            "normalize_embeddings": True,
            "batch_size": batch_size,
        },
    )
    # Chunk sizes are already bounded by the project splitter. Capping the
    # tokenizer length prevents an unusually long query from creating a large
    # attention tensor on a constrained machine.
    # langchain-huggingface 1.x renamed the wrapped SentenceTransformer from
    # `.client` to `._client`; accept both so model construction never crashes.
    model_client = getattr(embeddings, "_client", None) or getattr(
        embeddings, "client", None
    )
    if model_client is not None:
        model_client.max_seq_length = min(
            getattr(model_client, "max_seq_length", 8192), 1024
        )
    return embeddings


def get_embeddings(
    show_progress: bool = False, device: str | None = None
) -> HuggingFaceEmbeddings:
    """Return one shared embedding model; ``show_progress`` is kept for API compatibility.

    ``device`` overrides the configured device: ingestion passes no device and
    runs on ``settings.embedding_device`` (GPU), while the retriever passes
    ``settings.retrieval_device`` (CPU) so query-time encoding stays off the GPU.
    """
    del show_progress
    resolved_device = resolve_embedding_device(device or settings.embedding_device)
    batch_size = max(1, settings.embedding_batch_size)
    try:
        return _build_embeddings(resolved_device, batch_size)
    except RuntimeError as exc:
        # A GPU can become unavailable between the free-memory check and model
        # construction. Retry once on CPU so the app remains usable.
        if resolved_device == "cuda" and "out of memory" in str(exc).lower():
            import torch

            torch.cuda.empty_cache()
            _build_embeddings.cache_clear()
            return _build_embeddings("cpu", 1)
        raise


# Sentence-transformers is not guaranteed to be thread-safe during concurrent
# encode calls. Serializing retrieval also prevents concurrent subtasks from
# multiplying the embedding memory peak.
embedding_lock = threading.Lock()


def preload_embeddings() -> bool:
    """Best-effort model warmup for startup scripts and long-running demos."""
    try:
        embeddings = get_embeddings(device=settings.retrieval_device)
        embeddings.embed_query("warmup")
        return True
    except Exception:
        return False
