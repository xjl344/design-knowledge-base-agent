"""Batch relevance and answer-sufficiency grading with one LLM request and one retry."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from functools import lru_cache

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate

from config import settings
from src.model_clients import chat_model, require_role_model


class GradingError(RuntimeError):
    """Raised after the relevance response cannot be parsed twice."""


@dataclass(frozen=True)
class GradingAssessment:
    """Structured output of one LLM grading call: kept docs + answer sufficiency."""

    documents: list[Document]
    coverage: str = "sufficient"  # "sufficient" | "partial" | "insufficient"
    missing_aspects: str = ""
    search_query: str = ""


GRADER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """你是设计知识库的文档相关性与证据充分性评分器。
你需要同时完成两项判断：
1. 筛选相关片段（relevant_ids）：能直接回答问题、解释问题的某个方面，或提供回答所需依据/背景的片段都保留；
   综合类问题涉及多个维度时保留所有相关片段；
   除非所有候选都不匹配，否则不要选择 scope_mismatch。
2. 评估这些被保留的本地片段合起来对“用户核心问题”的覆盖充分性（coverage）：
   - "sufficient"：本地片段已包含足够信息，能直接、较完整地回答用户的核心问题（即使存在常规工程注意事项或具体工况需验证，也算 sufficient）。
   - "partial"：本地片段只回答了局部细节或单个环节，缺少用户问题的核心主体（例如用户问“完整加工过程/步骤”，但文档只讲了折弯压痕等个别细节；或用户对比 A 与 B，但文档只覆盖了 A）。
   - "insufficient"：没有任何片段能有效回答用户的核心问题。
3. 当 coverage 为 "partial" 或 "insufficient" 时：
   - 用一句话说明缺失了什么（missing_aspects）；
   - 给出一条简短精准的中文或中英双语网络搜索关键词（search_query，10～25字，不含问号和口语词），用于精准补充缺失信息；
   - 当 coverage 为 "sufficient" 时，missing_aspects 和 search_query 留空字符串。

只返回严格 JSON，不要 Markdown，不要解释。格式：
{{"relevant_ids":["D1","D3"],"coverage":"sufficient","missing_aspects":"","search_query":""}}
只能使用输入中存在的候选编号；没有相关片段时 relevant_ids 返回空数组且 coverage 为 "insufficient"。""",
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


def parse_grading_result(raw: str, valid_ids: set[str]) -> tuple[set[str], str, str, str]:
    """Parse relevant IDs plus sufficiency assessment from grader JSON output."""
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        raise ValueError("grader did not return a JSON object")
    payload = json.loads(match.group(0))
    relevant_ids = payload.get("relevant_ids")
    if not isinstance(relevant_ids, list) or not all(
        isinstance(item, str) for item in relevant_ids
    ):
        raise ValueError("relevant_ids must be a list of strings")
    selected = set(relevant_ids) & valid_ids
    raw_coverage = str(payload.get("coverage", "")).strip().lower()
    if raw_coverage not in {"sufficient", "partial", "insufficient"}:
        coverage = "sufficient" if selected else "insufficient"
    elif not selected:
        coverage = "insufficient"
    else:
        coverage = raw_coverage
    missing_aspects = str(payload.get("missing_aspects") or "").strip()
    search_query = str(payload.get("search_query") or "").strip()
    return selected, coverage, missing_aspects, search_query


def parse_relevant_ids(raw: str, valid_ids: set[str]) -> set[str]:
    selected, _coverage, _missing, _query = parse_grading_result(raw, valid_ids)
    return selected


async def grade_documents_with_assessment(
    question: str, documents: list[Document]
) -> GradingAssessment:
    if not documents:
        return GradingAssessment(documents=[], coverage="insufficient")

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
            relevant_ids, coverage, missing_aspects, search_query = parse_grading_result(
                _message_text(response.content), valid_ids
            )
            kept = [document for doc_id, document in numbered if doc_id in relevant_ids]
            return GradingAssessment(
                documents=kept,
                coverage=coverage,
                missing_aspects=missing_aspects,
                search_query=search_query,
            )
        except Exception as exc:
            last_error = exc

    raise GradingError(f"Relevance grading failed: {last_error}") from last_error


async def grade_documents(question: str, documents: list[Document]) -> list[Document]:
    assessment = await grade_documents_with_assessment(question, documents)
    return assessment.documents
