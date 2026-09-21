"""LLM query decomposition: split a question into multi-facet sub-queries.

Multi-source design questions (e.g. "which human dimensions matter for an
office chair?") need evidence from several angles — standard values, body
measurement data, and design methodology. A single embedding query can only
point in one semantic direction, so it silently misses sources whose wording
differs (e.g. 人机工程/设计方法 vs 座高/人体尺寸). We ask the LLM to emit
2-3 facet sub-queries; retrieval then runs each one and merges results.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

from langchain_core.prompts import ChatPromptTemplate

from config import settings
from src.model_clients import chat_model, require_role_model

DECOMPOSE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "你是检索查询分解器，为向量检索生成多个独立视角的子查询。"
            "设计类问题通常需要从多个维度查资料：①具体标准/数值（如 GB/T 标准、尺寸要求）②人体数据/测量项目 "
            "③设计方法/原则（如人机工程、系统化设计）。"
            "请根据问题输出 2-3 个覆盖不同维度的子查询，每个子查询是独立短句、聚焦一个维度，不要包含疑问词。"
            '只输出 JSON：{{"sub_queries": ["子查询1", "子查询2", "子查询3"]}}',
        ),
        ("human", "问题：{question}"),
    ]
)


@lru_cache(maxsize=1)
def _get_decompose_chain():
    require_role_model("planner")
    llm = chat_model("planner", temperature=0.0)
    return DECOMPOSE_PROMPT | llm


def _parse_sub_queries(raw: str) -> list[str]:
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return []
    payload = json.loads(match.group(0))
    sub_queries = payload.get("sub_queries")
    if not isinstance(sub_queries, list):
        return []
    return [item for item in sub_queries if isinstance(item, str) and item.strip()][:3]


async def decompose_question(question: str) -> list[str]:
    """Return 1-3 facet sub-queries, or [] on any failure (caller falls back)."""
    if not settings.query_decompose_enabled:
        return []
    try:
        response = await _get_decompose_chain().ainvoke({"question": question})
        raw = response.content if hasattr(response, "content") else str(response)
        return _parse_sub_queries(raw)
    except Exception:
        # Decomposition is best-effort: never block retrieval on it.
        return []
