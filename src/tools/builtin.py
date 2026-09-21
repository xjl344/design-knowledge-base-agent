"""Built-in tools backed by the existing retrieval and web-search services."""

from __future__ import annotations

from src.retriever import retrieve_documents_multi
from src.web_search import web_search
from src.tools import Tool, ToolRegistry


async def _web_search_tool(question: str):
    return await web_search(question)


def register_builtin_tools(
    registry: ToolRegistry,
    timeout_seconds: float = 30.0,
    local_retrieval_timeout_seconds: float = 180.0,
    max_retries: int = 2,
    allow_web: bool = True,
    query_decompose_enabled: bool | None = None,
) -> None:
    async def _local_retrieval_tool(question: str):
        # The timeout is enforced by ToolRegistry and also passed to the
        # retriever so it can stop before starting another sub-query.
        return await retrieve_documents_multi(
            question,
            total_timeout_seconds=local_retrieval_timeout_seconds,
            query_decompose_enabled=query_decompose_enabled,
        )

    registry.register(
        Tool(
            name="local_retrieval",
            description="从本地设计知识库检索相关文档",
            func=_local_retrieval_tool,
            parameters={"question": str},
            # CPU BGE-M3 may need over a minute on its first query. Retrying a
            # timed-out embedding call can start another worker while the first
            # one is still loading, so local retrieval is deliberately single-shot.
            timeout_seconds=local_retrieval_timeout_seconds,
            max_retries=0,
        )
    )
    if not allow_web:
        registry._disabled_tools.add("web_search")
    registry.register(
        Tool(
            name="web_search",
            description="使用网络搜索补充本地知识库没有的资料",
            func=_web_search_tool,
            parameters={"question": str},
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
    )
