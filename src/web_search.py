"""DuckDuckGo search adapter."""

from __future__ import annotations

import asyncio

from ddgs import DDGS
from langchain_core.documents import Document

from config import settings


def _search_sync(query: str) -> list[Document]:
    results = DDGS().text(
        query,
        region="cn-zh",
        safesearch="moderate",
        max_results=settings.web_search_max_results,
    )
    documents: list[Document] = []
    for result in results:
        url = result.get("href") or result.get("url") or ""
        title = result.get("title") or "网络搜索结果"
        body = result.get("body") or result.get("description") or ""
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
                    "web_query": query,
                },
            )
        )
    return documents


async def web_search(query: str) -> list[Document]:
    return await asyncio.to_thread(_search_sync, query)
