from langchain_core.documents import Document

from src.evidence import (
    audit_claims,
    delivery_decision,
    extract_claims,
    validate_citations,
)
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


def test_a_recommendation_passes_when_the_answer_states_the_duties_elsewhere():
    """The duty is a property of the answer, not of one sentence.

    A seven-section answer states its conditions in one section and its risks and
    verification plan in another, so a per-sentence test refuses answers that
    plainly satisfy the rule.  Measured 2026-09-24: two of three real questions
    were refused this way -- including a pure "what is the scope of this
    standard" question, which is not a recommendation at all.
    """
    document = direct_doc()
    answer = (
        "资料事实：该方案在 -40℃ 到 100℃ 范围内可用 [L1]。\n"
        "设计建议：建议优先选择该方案 [L1]。\n"
        "风险与限制：长期老化数据缺失，成本高于替代方案。\n"
        "验证要求：需完成冷热循环与跌落测试后确认。"
    )
    decision = delivery_decision(answer, [document], audit_claims(answer, [document], {}))
    assert not any(
        item["status"] == "recommendation_unconditional"
        for item in decision["blocking_issues"]
    )


def test_a_table_header_is_not_a_claim():
    """The row above a separator is a header by definition, not an assertion.

    The previous test looked for a fixed vocabulary of column names, so
    `| 参数 | 建议值 | 类型 | 依据 |` was not recognised: it classified as a
    `design_inference` (the column is called 建议值), carried no citation, and
    blocked the whole answer as an unconditional recommendation.  Reproduced
    offline against a normal seven-section answer, which is the shape the app's
    own prompt asks for.
    """
    document = direct_doc()
    answer = (
        "资料事实：该方案在 -40℃ 到 100℃ 范围内可用 [L1]。\n"
        "| 参数 | 建议值 | 类型 | 依据 |\n"
        "|---|---|---|---|\n"
        "| 主来源 | 方案 A | 设计建议 | [L1] |\n"
        "适用条件：常温场景。\n"
        "风险与限制：长期老化数据缺失。\n"
        "验证要求：需完成冷热循环测试后确认。"
    )
    assert not any(
        "参数 | 建议值" in str(claim["text"]) for claim in extract_claims(answer)
    )
    decision = delivery_decision(answer, [document], audit_claims(answer, [document], {}))
    assert not any(
        item["status"] == "recommendation_unconditional"
        for item in decision["blocking_issues"]
    )


def test_a_separator_row_is_not_a_claim():
    document = direct_doc()
    claims = extract_claims("资料事实：参数为 10 mm [L1]。\n|---|---|\n")
    assert not any(set(str(claim["text"])) <= set("|-: ") for claim in claims)


def test_a_recommendation_without_a_citation_is_blocked_even_when_the_duties_are_stated():
    """Answer-level duties must not turn into "any recommendation is fine".

    The statements are separated by blank lines on purpose: citations are scoped
    to a paragraph, so four lines with no blank line between them are *one*
    paragraph and would legitimately share the one citation it carries.
    """
    document = direct_doc()
    answer = (
        "资料事实：该方案在 -40℃ 到 100℃ 范围内可用 [L1]。\n\n"
        "设计建议：建议优先选择该方案。\n\n"
        "风险与限制：长期老化数据缺失，成本高于替代方案。\n\n"
        "验证要求：需完成冷热循环与跌落测试后确认。"
    )
    decision = delivery_decision(answer, [document], audit_claims(answer, [document], {}))
    assert decision["deliverable"] is False
    assert any(
        item["status"] == "recommendation_unconditional"
        for item in decision["blocking_issues"]
    )


def test_a_citation_at_the_end_of_a_paragraph_covers_its_sentences():
    """Where the whole answer got refused: one citation per paragraph.

    Measured on a real seven-section answer, 25 of 30 claims came back with no
    citation although the text contained 39 lines carrying `[Lx]` -- the model
    cites once per paragraph, and a per-sentence reading marks every sentence but
    the last as unsupported.
    """
    document = direct_doc()
    answer = (
        "【资料事实】该标准的名称为《中国成年人人体尺寸》。其范围是给出用于技术设计的"
        "成年人人体尺寸基本统计数值。[L1]\n\n"
        "适用条件：常温场景。\n\n"
        "风险与限制：长期老化数据缺失。\n\n"
        "验证要求：需完成冷热循环测试后确认。"
    )
    claims = extract_claims(answer)
    first = next(claim for claim in claims if "该标准的名称为" in str(claim["text"]))
    assert first["citations"] == ["L1"], "段首句应继承段末的引用"
    decision = delivery_decision(answer, [document], audit_claims(answer, [document], {}))
    assert not any(
        item["status"] == "unreferenced" for item in decision["blocking_issues"]
    )


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
