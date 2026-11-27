"""DuckDuckGo search adapter."""

from __future__ import annotations

import asyncio
import re

from ddgs import DDGS
from langchain_core.documents import Document

from config import settings

_QUERY_FILLER_RE = re.compile(
    r"(?:请问|帮我查一下|查一下|告诉我|是什么|有哪些|怎么样的|怎么样|如何|一般是多少|一般为多少|一般取多少|吗|呢|啊|呀)"
)
_PUNCT_RE = re.compile(r"[？?！!。，,；;：:“”\"'（）()【】\[\]]+")


def _normalize_web_query(query: str) -> str:
    """Turn a conversational or planner-annotated Chinese question into a concise search query."""
    raw = str(query or "").strip()
    # Strip verbose planner annotations such as "；重点评价：..." that cause search engines to fail.
    raw = re.split(r"[；;]\s*(?:重点评价|评估维度|评价维度|输出要求|约束条件)[:：]", raw, maxsplit=1)[0].strip()
    cleaned = _PUNCT_RE.sub(" ", raw)
    cleaned = _QUERY_FILLER_RE.sub(" ", cleaned)
    cleaned = re.sub(r"([\u4e00-\u9fff])的([\u4e00-\u9fff])", r"\1\2", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if len(cleaned) >= 2 else raw


def _run_ddgs(query: str, *, region: str = "cn-zh", timeout: int = 12) -> list[dict]:
    results = DDGS(timeout=timeout).text(
        query,
        region=region,
        safesearch="moderate",
        max_results=settings.web_search_max_results,
    )
    return list(results or [])


def _search_sync(query: str) -> list[Document]:
    normalized = _normalize_web_query(query)
    candidates: list[tuple[str, str]] = [(normalized, "cn-zh")]
    if normalized != str(query or "").strip():
        candidates.append((str(query).strip(), "cn-zh"))
    candidates.append((normalized, "wt-wt"))

    results: list[dict] = []
    last_error: Exception | None = None
    for candidate_query, region in candidates:
        try:
            results = _run_ddgs(candidate_query, region=region)
            if results:
                break
        except Exception as exc:
            last_error = exc
            continue
    if not results and last_error is not None:
        raise last_error

    documents: list[Document] = []
    for result in results:
        url = result.get("href") or result.get("url") or ""
        title = str(result.get("title") or "网络搜索结果").strip()[:120]
        body = str(result.get("body") or result.get("description") or "").strip()
        if not url or not body:
            continue
        documents.append(
            Document(
                page_content=f"{title}\n\n{body}",
                metadata={
                    "source": url,
                    "source_title": title,
                    "title": title,
                    "source_type": "web",
                    "source_category": "web_search",
                    "evidence_level": "web_search",
                    "population": "unknown",
                    "material_grade": "unknown",
                    "applicability": "unknown",
                    "retrieval_evidence_status": "indirect",
                    "retrieval_evidence_reason": "网络搜索摘要，未核验原始文件或完整适用范围",
                    "web_query": normalized,
                },
            )
        )
    return documents


async def web_search(query: str) -> list[Document]:
    return await asyncio.to_thread(_search_sync, query)
