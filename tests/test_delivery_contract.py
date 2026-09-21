from langchain_core.documents import Document

from src.evidence import audit_claims, delivery_decision, validate_citations
import src.retriever as retriever


def direct_doc() -> Document:
    return Document(
        page_content="官方 TDS 给出测试条件和参数。",
        metadata={
            "source_type": "local",
            "source": "official-tds.pdf",
            "title": "Official TDS",
            "retrieval_evidence_status": "direct",
        },
    )


def indirect_doc() -> Document:
    return Document(
        page_content="搜索摘要提到某材料可能具有相关性能。",
        metadata={
            "source_type": "web",
            "source": "https://example.com",
            "title": "Search snippet",
            "retrieval_evidence_status": "indirect",
        },
    )


def test_invalid_citation_is_hard_blocked():
    document = direct_doc()
    decision = delivery_decision("资料事实：参数为 10 mm [W8]。", [document], audit_claims("资料事实：参数为 10 mm [W8]。", [document], {}))
    assert decision["deliverable"] is False
    assert any(item["status"] == "citation_invalid" for item in decision["blocking_issues"])


def test_indirect_evidence_cannot_be_deliverable_fact():
    document = indirect_doc()
    answer = "资料事实：该材料耐温范围为 -40℃ 到 100℃ [W1]。"
    decision = delivery_decision(answer, [document], audit_claims(answer, [document], {}))
    assert decision["deliverable"] is False
    assert any(item["status"] == "indirect_evidence" for item in decision["blocking_issues"])


def test_unconditional_recommendation_is_blocked():
    document = direct_doc()
    answer = "建议优先选择该方案 [L1]。"
    decision = delivery_decision(answer, [document], audit_claims(answer, [document], {}))
    assert decision["deliverable"] is False
    assert any(item["status"] == "recommendation_unconditional" for item in decision["blocking_issues"])


def test_valid_citation_set_is_recomputed_from_current_documents():
    documents = [direct_doc(), indirect_doc()]
    result = validate_citations("依据 [L1] 和 [W1]。", documents)
    assert result["allowed_citations"] == ["L1", "W1"]
    assert result["invalid_citations"] == []


def test_local_retrieval_cache_reuses_identical_query(monkeypatch):
    document = direct_doc()
    calls = {"count": 0}
    monkeypatch.setattr(retriever, "_retrieval_cache_key", lambda question, top_k=None: (question, top_k))

    def uncached(question, top_k=None):
        calls["count"] += 1
        return [document]

    monkeypatch.setattr(retriever, "_retrieve_documents_uncached", uncached)
    retriever._RETRIEVAL_CACHE.clear()
    assert retriever.retrieve_documents("same query")
    assert retriever.retrieve_documents("same query")
    assert calls["count"] == 1
