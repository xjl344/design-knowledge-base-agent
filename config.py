"""Project configuration and storage boundaries."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parent
# Explicit process environment variables win over .env. This is required by
# the chunking experiment scripts, which select an isolated strategy/index for
# a child process without mutating the user's .env.
load_dotenv(ROOT_DIR / ".env", override=False)

DATA_DIR = ROOT_DIR / "data"
DOCUMENTS_DIR = DATA_DIR / "documents" / "设计知识库"
CHROMA_DIR = Path(os.getenv("CHROMA_DIR", str(DATA_DIR / "chroma_db")))
GRADIO_TEMP_DIR = DATA_DIR / "gradio_tmp"
CACHE_DIR = ROOT_DIR / ".cache"
TEMP_DIR = ROOT_DIR / ".tmp"
LOG_DIR = ROOT_DIR / "logs"
CHECKPOINT_DIR = TEMP_DIR / "checkpoints"
AI_INFRA_DIR = Path(os.getenv("AI_INFRA_DIR", r"E:\AI-Infra"))
SHARED_PIP_CACHE_DIR = AI_INFRA_DIR / "pip-cache"
SHARED_WHEEL_DIR = AI_INFRA_DIR / "wheels"

# Set cache locations before libraries such as Transformers, Torch, or Gradio load.
os.environ.setdefault("PIP_CACHE_DIR", str(SHARED_PIP_CACHE_DIR))
os.environ.setdefault("HF_HOME", str(CACHE_DIR / "huggingface"))
os.environ.setdefault("TORCH_HOME", str(CACHE_DIR / "torch"))
os.environ.setdefault("GRADIO_TEMP_DIR", str(GRADIO_TEMP_DIR))
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("TEMP", str(TEMP_DIR))
os.environ.setdefault("TMP", str(TEMP_DIR))
# Offline evaluation must not accidentally export traces through LangSmith or
# another callback configured in the environment.
if os.getenv("OFFLINE_MODE", "false").lower() == "true":
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ["LANGCHAIN_CALLBACKS_BACKGROUND"] = "false"
    os.environ["LANGCHAIN_ENDPOINT"] = ""


@dataclass(frozen=True)
class Settings:
    root_dir: Path = ROOT_DIR
    documents_dir: Path = DOCUMENTS_DIR
    chroma_dir: Path = CHROMA_DIR
    collection_name: str = os.getenv("COLLECTION_NAME", "design_knowledge")
    embedding_model_path: Path = Path(
        os.getenv(
            "EMBEDDING_MODEL_PATH",
            r"E:\AI-Infra\models\embeddings\bge-m3",
        )
    )
    embedding_device: str = os.getenv("EMBEDDING_DEVICE", "auto")
    # Query-time encoding during retrieval runs on CPU by default, while
    # ingestion batch-encoding uses `embedding_device` (GPU when available).
    retrieval_device: str = os.getenv("RETRIEVAL_DEVICE", "cpu")
    embedding_batch_size: int = int(os.getenv("EMBEDDING_BATCH_SIZE", "1"))
    # The LLM boundary is OpenAI-compatible so it can target OpenAI or a
    # compatible gateway (the active provider is selected in .env).
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    planner_backend: str = os.getenv("PLANNER_BACKEND", "cloud").lower()
    planner_model: str = os.getenv("PLANNER_MODEL", "")
    planner_base_url: str = os.getenv("PLANNER_BASE_URL", "")
    planner_api_key: str = os.getenv("PLANNER_API_KEY", "")
    grader_backend: str = os.getenv("GRADER_BACKEND", "cloud").lower()
    grader_model: str = os.getenv("GRADER_MODEL", "")
    grader_base_url: str = os.getenv("GRADER_BASE_URL", "")
    grader_api_key: str = os.getenv("GRADER_API_KEY", "")
    reranker_model_path: Path | None = Path(
        os.getenv(
            "RERANKER_MODEL_PATH",
            r"E:\AI-Infra\models\reranker\bge-reranker",
        )
    )
    reranker_enabled: bool = os.getenv("RERANKER_ENABLED", "true").lower() == "true"
    reranker_device: str = os.getenv("RERANKER_DEVICE", "auto").lower()
    reranker_batch_size: int = int(os.getenv("RERANKER_BATCH_SIZE", "8"))
    reranker_max_length: int = int(os.getenv("RERANKER_MAX_LENGTH", "1024"))
    reranker_use_fp16: bool = os.getenv("RERANKER_USE_FP16", "true").lower() == "true"
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
    chunking_strategy: str = os.getenv("CHUNKING_STRATEGY", "R").upper()
    chunk_size: int = int(os.getenv("CHUNK_SIZE", "800"))
    chunk_overlap: int = int(os.getenv("CHUNK_OVERLAP", "150"))
    semantic_similarity_threshold: float = float(os.getenv("SEMANTIC_SIMILARITY_THRESHOLD", "0.72"))
    semantic_min_chunk_size: int = int(os.getenv("SEMANTIC_MIN_CHUNK_SIZE", "250"))
    semantic_max_chunk_size: int = int(os.getenv("SEMANTIC_MAX_CHUNK_SIZE", "1200"))
    retriever_top_k: int = int(os.getenv("RETRIEVER_TOP_K", "10"))
    retriever_dense_top_k: int = int(os.getenv("RETRIEVER_DENSE_TOP_K", "50"))
    retriever_bm25_top_k: int = int(os.getenv("RETRIEVER_BM25_TOP_K", "50"))
    retriever_rerank_top_k: int = int(os.getenv("RETRIEVER_RERANK_TOP_K", "20"))
    retriever_union_max_candidates: int = int(os.getenv("RETRIEVER_UNION_MAX_CANDIDATES", "200"))
    retriever_expansion_bm25_top_k: int = int(os.getenv("RETRIEVER_EXPANSION_BM25_TOP_K", "20"))
    retriever_source_role_priority: bool = os.getenv("RETRIEVER_SOURCE_ROLE_PRIORITY", "true").lower() == "true"
    retriever_separate_entities: bool = os.getenv("RETRIEVER_SEPARATE_ENTITIES", "true").lower() == "true"
    retriever_independent_bm25_queries: bool = os.getenv("RETRIEVER_INDEPENDENT_BM25_QUERIES", "false").lower() == "true"
    retriever_bm25_mode: str = os.getenv("RETRIEVER_BM25_MODE", "conditional").lower()
    retriever_max_entity_bm25_queries: int = int(os.getenv("RETRIEVER_MAX_ENTITY_BM25_QUERIES", "3"))
    retriever_entity_bm25_top_k: int = int(os.getenv("RETRIEVER_ENTITY_BM25_TOP_K", "20"))
    retriever_source_cap: int = int(os.getenv("RETRIEVER_SOURCE_CAP", "2"))
    retriever_ranking_mode: str = os.getenv("RETRIEVER_RANKING_MODE", "reranker_entity_rrf").lower()
    retriever_entity_guard: bool = os.getenv("RETRIEVER_ENTITY_GUARD", "true").lower() == "true"
    retriever_process_guide_prior: bool = os.getenv("RETRIEVER_PROCESS_GUIDE_PRIOR", "true").lower() == "true"
    retriever_process_guide_prior_bonus: float = float(os.getenv("RETRIEVER_PROCESS_GUIDE_PRIOR_BONUS", "0.025"))
    # Index/catalog documents may be semantically similar to a question, but
    # should yield to substantive technical documents whenever both are in
    # the candidate pool.  The penalty is local to those low-authority docs.
    retriever_low_authority_prior: bool = os.getenv("RETRIEVER_LOW_AUTHORITY_PRIOR", "true").lower() == "true"
    retriever_low_authority_penalty: float = float(os.getenv("RETRIEVER_LOW_AUTHORITY_PENALTY", "0.12"))
    retriever_anthropometry_prior: bool = os.getenv("RETRIEVER_ANTHROPOMETRY_PRIOR", "true").lower() == "true"
    retriever_anthropometry_prior_bonus: float = float(os.getenv("RETRIEVER_ANTHROPOMETRY_PRIOR_BONUS", "0.08"))
    retriever_metadata_weight: float = float(os.getenv("RETRIEVER_METADATA_WEIGHT", "3.0"))
    retriever_parent_weight: float = float(os.getenv("RETRIEVER_PARENT_WEIGHT", "2.0"))
    retriever_rerank_weight: float = float(os.getenv("RETRIEVER_RERANK_WEIGHT", "0.82"))
    retriever_explicit_entity_weight: float = float(os.getenv("RETRIEVER_EXPLICIT_ENTITY_WEIGHT", "0.10"))
    retriever_expanded_entity_weight: float = float(os.getenv("RETRIEVER_EXPANDED_ENTITY_WEIGHT", "0.03"))
    retriever_rrf_weight: float = float(os.getenv("RETRIEVER_RRF_WEIGHT", "0.05"))
    rrf_k: int = int(os.getenv("RRF_K", "60"))
    dense_weight: float = float(os.getenv("DENSE_WEIGHT", "0.55"))
    bm25_weight: float = float(os.getenv("BM25_WEIGHT", "0.45"))
    retriever_min_relevance: float = float(os.getenv("RETRIEVER_MIN_RELEVANCE", "0.25"))
    retriever_subquery_top_k: int = int(os.getenv("RETRIEVER_SUBQUERY_TOP_K", "5"))
    retriever_new_source_cap: int = int(os.getenv("RETRIEVER_NEW_SOURCE_CAP", "2"))
    retriever_grader_cap: int = int(os.getenv("RETRIEVER_GRADER_CAP", "3"))
    query_decompose_enabled: bool = (
        os.getenv("QUERY_DECOMPOSE_ENABLED", "true").lower() == "true"
    )
    web_search_max_results: int = int(os.getenv("WEB_SEARCH_MAX_RESULTS", "3"))
    agent_planning_enabled: bool = os.getenv("AGENT_PLANNING_ENABLED", "true").lower() == "true"
    max_concurrent_requests: int = int(os.getenv("MAX_CONCURRENT_REQUESTS", "2"))
    max_concurrent_local_retrieval: int = int(os.getenv("MAX_CONCURRENT_LOCAL_RETRIEVAL", "1"))
    embedding_preload: bool = os.getenv("EMBEDDING_PRELOAD", "false").lower() == "true"
    strict_citation_validation: bool = os.getenv("STRICT_CITATION_VALIDATION", "true").lower() == "true"
    require_conditional_recommendation: bool = os.getenv("REQUIRE_CONDITIONAL_RECOMMENDATION", "true").lower() == "true"
    tool_timeout_seconds: float = float(os.getenv("TOOL_TIMEOUT_SECONDS", "30"))
    local_retrieval_timeout_seconds: float = float(
        os.getenv("LOCAL_RETRIEVAL_TIMEOUT_SECONDS", "30")
    )
    local_retrieval_total_timeout_seconds: float = float(
        os.getenv(
            "LOCAL_RETRIEVAL_TOTAL_TIMEOUT_SECONDS",
            os.getenv("LOCAL_RETRIEVAL_TIMEOUT_SECONDS", "20"),
        )
    )
    embedding_timeout_seconds: float = float(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "10"))
    dense_retrieval_timeout_seconds: float = float(os.getenv("DENSE_RETRIEVAL_TIMEOUT_SECONDS", "8"))
    bm25_timeout_seconds: float = float(os.getenv("BM25_TIMEOUT_SECONDS", "5"))
    reranker_timeout_seconds: float = float(os.getenv("RERANKER_TIMEOUT_SECONDS", "10"))
    planner_timeout_seconds: float = float(os.getenv("PLANNER_TIMEOUT_SECONDS", "15"))
    total_pipeline_timeout_seconds: float = float(os.getenv("TOTAL_PIPELINE_TIMEOUT_SECONDS", "120"))
    cancel_on_timeout: bool = os.getenv("CANCEL_ON_TIMEOUT", "true").lower() == "true"
    offline_mode: bool = os.getenv("OFFLINE_MODE", "false").lower() == "true"
    allow_web_cache: bool = os.getenv("ALLOW_WEB_CACHE", "true").lower() == "true"
    max_retrieval_tasks: int = int(os.getenv("MAX_RETRIEVAL_TASKS", "3"))
    max_web_queries: int = int(os.getenv("MAX_WEB_QUERIES", "3"))
    tool_max_retries: int = int(os.getenv("TOOL_MAX_RETRIES", "2"))
    per_query_token_limit: int = int(os.getenv("PER_QUERY_TOKEN_LIMIT", "10000"))
    daily_token_limit: int = int(os.getenv("DAILY_TOKEN_LIMIT", "1000000"))
    langsmith_sampling_rate: float = float(os.getenv("LANGSMITH_SAMPLING_RATE", "0.1"))
    checkpoint_dir: Path = Path(os.getenv("CHECKPOINT_DIR", str(CHECKPOINT_DIR)))

    def validate_chunking(self) -> None:
        if self.chunking_strategy not in {"F", "R", "P", "V"}:
            raise ValueError("CHUNKING_STRATEGY must be one of F, R, P, V")
        if self.chunk_size <= 0 or self.chunk_overlap < 0:
            raise ValueError("CHUNK_SIZE must be positive and CHUNK_OVERLAP non-negative")
        if self.semantic_min_chunk_size <= 0 or self.semantic_max_chunk_size < self.semantic_min_chunk_size:
            raise ValueError("semantic chunk size bounds are invalid")

    def validate_retriever(self) -> None:
        if self.max_concurrent_local_retrieval <= 0:
            raise ValueError("MAX_CONCURRENT_LOCAL_RETRIEVAL must be positive")
        if self.reranker_device not in {"auto", "cuda", "cpu"}:
            raise ValueError("RERANKER_DEVICE must be auto, cuda, or cpu")
        if self.reranker_batch_size <= 0 or self.reranker_max_length <= 0:
            raise ValueError("reranker batch size and max length must be positive")
        if self.max_retrieval_tasks <= 0 or self.max_web_queries < 0:
            raise ValueError("retrieval task and web query budgets are invalid")
        if min(self.retriever_top_k, self.retriever_dense_top_k, self.retriever_bm25_top_k, self.retriever_rerank_top_k) <= 0:
            raise ValueError("retriever top-k values must be positive")
        if self.retriever_ranking_mode not in {"reranker_only", "reranker_entity", "reranker_entity_rrf"}:
            raise ValueError("RETRIEVER_RANKING_MODE must be reranker_only, reranker_entity, or reranker_entity_rrf")
        if self.retriever_bm25_mode not in {"conditional", "original_only", "independent_all"}:
            raise ValueError("RETRIEVER_BM25_MODE must be conditional, original_only, or independent_all")
        if self.retriever_max_entity_bm25_queries < 0 or self.retriever_entity_bm25_top_k <= 0:
            raise ValueError("BM25 entity query limits must be non-negative and top-k positive")
        if self.retriever_process_guide_prior_bonus < 0:
            raise ValueError("RETRIEVER_PROCESS_GUIDE_PRIOR_BONUS must be non-negative")
        if self.retriever_low_authority_penalty < 0:
            raise ValueError("RETRIEVER_LOW_AUTHORITY_PENALTY must be non-negative")
        if self.retriever_anthropometry_prior_bonus < 0:
            raise ValueError("RETRIEVER_ANTHROPOMETRY_PRIOR_BONUS must be non-negative")

    def validate_embedding_model(self) -> None:
        required = ("config.json", "modules.json", "tokenizer.json")
        missing = [name for name in required if not (self.embedding_model_path / name).is_file()]
        if missing:
            missing_text = ", ".join(missing)
            raise FileNotFoundError(
                f"BGE-M3 model is incomplete at {self.embedding_model_path}; "
                f"missing: {missing_text}"
            )

    def require_llm(self) -> None:
        if not self.llm_api_key or self.llm_api_key == "replace_with_your_api_key":
            raise RuntimeError("LLM_API_KEY is not configured in .env")


def ensure_runtime_dirs() -> None:
    for directory in (
        DOCUMENTS_DIR,
        CHROMA_DIR,
        GRADIO_TEMP_DIR,
        CACHE_DIR / "huggingface",
        CACHE_DIR / "torch",
        TEMP_DIR,
        CHECKPOINT_DIR,
        LOG_DIR,
        SHARED_PIP_CACHE_DIR,
        SHARED_WHEEL_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)


settings = Settings()


def check_timeout_consistency(instance: "Settings" = settings) -> list[str]:
    """Return descriptions of timeout settings that can never take effect.

    A nested budget that exceeds its own parent is dead configuration: the
    parent cancels first, so the child's value is unreachable. This was found
    in the wild (retrieval ceiling 180s inside a 120s pipeline ceiling), where
    it silently made the retrieval budget meaningless.

    Returns a list of human-readable problems rather than raising, so callers
    can decide whether to warn or fail.
    """
    problems: list[str] = []
    if instance.local_retrieval_timeout_seconds > instance.total_pipeline_timeout_seconds:
        problems.append(
            "LOCAL_RETRIEVAL_TIMEOUT_SECONDS "
            f"({instance.local_retrieval_timeout_seconds:g}s) 超过 "
            f"TOTAL_PIPELINE_TIMEOUT_SECONDS "
            f"({instance.total_pipeline_timeout_seconds:g}s)；"
            "pipeline 会先取消，检索预算永远无法生效。"
        )
    if instance.local_retrieval_total_timeout_seconds > instance.total_pipeline_timeout_seconds:
        problems.append(
            "LOCAL_RETRIEVAL_TOTAL_TIMEOUT_SECONDS "
            f"({instance.local_retrieval_total_timeout_seconds:g}s) 超过 "
            f"TOTAL_PIPELINE_TIMEOUT_SECONDS "
            f"({instance.total_pipeline_timeout_seconds:g}s)；同样不可达。"
        )
    if instance.tool_timeout_seconds > instance.total_pipeline_timeout_seconds:
        problems.append(
            "TOOL_TIMEOUT_SECONDS "
            f"({instance.tool_timeout_seconds:g}s) 超过 pipeline 总预算 "
            f"({instance.total_pipeline_timeout_seconds:g}s)。"
        )
    return problems
