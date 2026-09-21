"""Batch relevance grading with one LLM request and one retry."""

from __future__ import annotations

import json
import re
from functools import lru_cache

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate

from config import settings
from src.model_clients import chat_model, require_role_model


class GradingError(RuntimeError):
    """Raised after the relevance response cannot be parsed twice."""


GRADER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """你是设计知识库的文档相关性评分器。
判断每个候选片段是否与用户问题相关：能直接回答问题、解释问题的某个方面、
或提供回答所需的依据/背景信息，都算相关。
注意：综合类问题可能涉及多个维度（标准数值、人体数据、设计方法等），
请保留所有与问题任一维度相关的片段，不要只选择最相关的少数几个。
候选片段带有检索证据状态：direct 可作为直接依据，indirect 只能作为背景，
scope_mismatch 不得作为支持性证据；除非所有候选都不匹配，否则不要选择 scope_mismatch。
只返回严格 JSON，不要 Markdown，不要解释。格式：
{{"relevant_ids":["D1","D3"]}}
只能使用输入中存在的候选编号；没有相关片段时返回空数组。""",
        ),
        (
            "human",
            "用户问题：{question}\n\n候选文档：\n{candidates}",
        ),
    ]
)


@lru_cache(maxsize=1)
def get_grader_chain():
    require_role_model("grader")
    llm = chat_model("grader", temperature=0.0)
    return GRADER_PROMPT | llm


def _message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", "")) if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content)


def _candidate_text(document: Document, doc_id: str) -> str:
    metadata = document.metadata
    return (
        f"[{doc_id}]\n"
        f"来源：{metadata.get('source', 'unknown')}\n"
        f"证据类别：{metadata.get('source_category', 'unknown')}\n"
        f"证据等级：{metadata.get('evidence_level', 'unknown')}\n"
        f"人群：{metadata.get('population', 'unknown')}\n"
        f"材料牌号：{metadata.get('material_grade', 'unknown')}\n"
        f"检索状态：{metadata.get('retrieval_evidence_status', 'unknown')}\n"
        f"判断原因：{metadata.get('retrieval_evidence_reason', 'unknown')}\n"
        f"正文：{document.page_content}"
    )


def parse_relevant_ids(raw: str, valid_ids: set[str]) -> set[str]:
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        raise ValueError("grader did not return a JSON object")
    payload = json.loads(match.group(0))
    relevant_ids = payload.get("relevant_ids")
    if not isinstance(relevant_ids, list) or not all(
        isinstance(item, str) for item in relevant_ids
    ):
        raise ValueError("relevant_ids must be a list of strings")
    return set(relevant_ids) & valid_ids


async def grade_documents(question: str, documents: list[Document]) -> list[Document]:
    if not documents:
        return []

    numbered = [(f"D{index}", document) for index, document in enumerate(documents, 1)]
    candidates = "\n\n".join(
        _candidate_text(document, doc_id) for doc_id, document in numbered
    )
    valid_ids = {doc_id for doc_id, _ in numbered}
    last_error: Exception | None = None

    for _attempt in range(2):
        try:
            response = await get_grader_chain().ainvoke(
                {"question": question, "candidates": candidates}
            )
            relevant_ids = parse_relevant_ids(
                _message_text(response.content), valid_ids
            )
            return [
                document for doc_id, document in numbered if doc_id in relevant_ids
            ]
        except Exception as exc:
            last_error = exc

    raise GradingError(f"Relevance grading failed: {last_error}") from last_error
