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


def test_an_empty_section_filler_is_not_a_claim():
    """`不涉及设计建议。` used to be classified `design_inference` -- it contains
    建议 -- and then blocked as an unconditional recommendation, taking the whole
    answer with it.  A sentence that says "this section does not apply" asserts
    nothing about the world."""
    texts = [str(claim["text"]) for claim in extract_claims("不涉及设计建议。\n无额外验证要求。\n")]
    assert texts == [], texts


def test_a_bibliography_entry_is_not_a_claim():
    """A reference list names sources; it does not assert anything about them."""
    answer = (
        "资料事实：参数为 10 mm [L1]。\n\n"
        "[L1]《GB/T 10000—2023》，第4页，来源：`03_…/GBT+10000-2023.pdf`\n"
    )
    texts = [str(claim["text"]) for claim in extract_claims(answer)]
    assert not any("来源：" in text for text in texts), texts


def test_a_standard_designation_is_not_a_numeric_claim():
    """`查询 GB/T 16252—2023 的名称。` was blocked as a numeric claim with no
    citation -- the 需求分析 line that restates the question.  A standard's
    designation is a name, not a measured value."""
    claim = next(
        claim
        for claim in extract_claims("查询 GB/T 16252—2023 的标准名称及适用范围。")
        if "查询" in str(claim["text"])
    )
    assert claim["numbers"] == [], claim["numbers"]
    assert claim["citations"] == []


def test_a_short_fully_cited_answer_is_deliverable():
    """The end-to-end shape: a 354-character answer that cites every fact.

    It was refused with four different blocking statuses, which is what sent the
    investigation into the gate in the first place.
    """
    document = direct_doc()
    answer = (
        "## 二、资料事实与参数\n\n"
        "- 【资料事实】标准名称：《成年人手部尺寸分型》。[L1]\n"
        "- 【资料事实】适用范围：给出了成年人手部尺寸分型的设置。[L1]\n\n"
        "## 三、工程推导\n\n不涉及工程推导。\n\n"
        "## 四、设计建议\n\n不涉及设计建议。\n"
    )
    decision = delivery_decision(answer, [document], audit_claims(answer, [document], {}))
    assert decision["deliverable"] is True, decision["blocking_issues"]


def test_the_gap_and_todo_sections_are_not_audited_for_evidence():
    """§五/§六/§七 exist to state what is unknown and what still needs checking.

    The prompt defines them; treating their sentences as assertions needing
    evidence is what blocked a 792-character answer whose every factual line
    carried `[L2]`.
    """
    document = direct_doc()
    answer = (
        "## 二、资料事实与参数\n\n【资料事实】参数为 10 mm。[L1]\n\n"
        "## 五、证据不足与冲突\n\n当前提供的资料未显示该标准完整条文的其他限制条件。\n\n"
        "## 六、风险与验证\n\n如需用于具体产品设计，还应进一步核验标准正文中的表格数据。\n\n"
        "## 七、参考资料\n\n[L1] 《某标准》，第4页，来源：`a.pdf`。\n"
    )
    decision = delivery_decision(answer, [document], audit_claims(answer, [document], {}))
    assert decision["deliverable"] is True, decision["blocking_issues"]


def test_a_section_that_cites_nothing_is_treated_as_filler():
    """Structural, not lexical.

    Enumerating the words a filler uses was measured failing three rounds
    running: the same meaning came back as `不涉及设计建议。`, then `…未显示…`,
    then `…不需要…`, each time slipping past the list.
    """
    document = direct_doc()
    answer = (
        "## 二、资料事实与参数\n\n【资料事实】参数为 10 mm。[L1]\n\n"
        "## 三、工程推导\n\n本问题不需要工程计算或尺寸推导。\n"
    )
    decision = delivery_decision(answer, [document], audit_claims(answer, [document], {}))
    assert decision["deliverable"] is True, decision["blocking_issues"]


def test_a_section_that_cites_is_still_audited():
    """The guard against over-loosening: the exemption must not become
    "any uncited sentence is fine" once the section cites something."""
    document = direct_doc()
    answer = (
        "## 二、资料事实与参数\n\n【资料事实】参数为 10 mm。[L1]\n\n"
        "## 四、设计建议\n\n"
        "【资料事实】该方案可用。[L1]\n\n"
        "设计建议：建议优先选择该方案。\n"
    )
    claims = extract_claims(answer)
    assert any("建议优先选择" in str(claim["text"]) for claim in claims), (
        "有引用的章节里，没有引用的推荐句仍应进入审计"
    )
    decision = delivery_decision(answer, [document], audit_claims(answer, [document], {}))
    assert decision["deliverable"] is False
    assert any(
        item["status"] == "recommendation_unconditional"
        for item in decision["blocking_issues"]
    )


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


def test_v2_refusal_phrasing_is_not_blocked_as_unconditional_recommendation():
    """Refusals containing '无法确认' or '不能提供推荐值' must pass delivery."""
    indirect = Document(
        page_content="仅包含注塑定性原则，无压铸最小壁厚数值。",
        metadata={
            "source_type": "local",
            "source": "05_制造工艺/Die_Casting/Xometry_Die_Casting_Design_Tips.md",
            "source_category": "process_guide",
            "retrieval_evidence_status": "indirect",
        },
    )
    answer = (
        "当前资料无法确认压铸件的最小壁厚一般取多少毫米。[L1]\n\n"
        "所列证据均标记为 indirect，没有支持该问题数值结论的 direct 证据。"
        "因此，不能提供具体毫米数或通用推荐值。"
    )
    audit = audit_claims(answer, [indirect], {})
    decision = delivery_decision(answer, [indirect], audit)
    assert decision["deliverable"] is True, decision["blocking_issues"]


def test_direct_process_guide_wall_thickness_recommendation_is_deliverable():
    """Manufacturer DFM process guides are direct evidence for molding wall thickness."""
    from src.document_metadata import classify_document_for_question

    die_status, _ = classify_document_for_question(
        "压铸件为什么不能做尖角？圆角怎么定？",
        {
            "source_category": "process_guide",
            "population": "unknown",
            "material_grade": "unknown",
            "source": "05_制造工艺/Die_Casting/Xometry_Die_Casting_Design_Tips.md",
        },
        "Fillets and radii prevent cold shuts.",
    )
    assert die_status == "direct"

    dfm_doc = Document(
        page_content="Polycarbonate recommended wall thickness: 1.016mm - 3.81mm.",
        metadata={
            "source_type": "local",
            "source": "05_制造工艺/Injection_Molding/Protolabs_Injection_Molding_Design_Guidelines.md",
            "source_category": "process_guide",
            "material_grade": "unknown",
            "retrieval_evidence_status": "direct",
        },
    )
    answer = (
        "PC 注塑件的参考推荐壁厚为 1.016～3.81 mm（适用条件：聚碳酸酯注塑件）。[L1]\n\n"
        "当前资料无法确认不同壁厚的具体风险、限制及验证要求，也无法在缺少零件条件时推荐一个确定数值。"
    )
    audit = audit_claims(answer, [dfm_doc], {})
    decision = delivery_decision(answer, [dfm_doc], audit)
    assert decision["deliverable"] is True, decision["blocking_issues"]


def test_disclosed_web_supplement_is_deliverable_while_invalid_web_citation_is_blocked():
    """Labeled web supplements pass delivery; disguised local facts & invalid W-IDs stay blocked."""
    from src.live_evidence import pack_from_documents

    local = direct_doc()
    web = indirect_doc()
    pack = pack_from_documents(
        "钣金的加工过程是什么？",
        [local, local, local, local, local, web],
        max_items=5,
    )
    assert "W1" in pack.allowed_citations
    assert "来源说明" in pack.context_text()

    answer = (
        "1. 前期设计检查与模具准备：确认树脂牌号、干燥与脱模设计。[L1]\n"
        "2. 合模、填充、保压、冷却与脱模：将熔融塑料注入模腔并冷却定型取出。[W1]\n\n"
        "**来源说明**：前期设计检查来自本地知识库（[L1]）；成型循环步骤来自网络补充参考（[W1]），仅供参考需核验。"
    )
    audit = audit_claims(answer, [local, web], {})
    decision = delivery_decision(answer, [local, web], audit)
    assert decision["deliverable"] is True, decision["blocking_issues"]

    bad_citation = (
        "网络补充参考（需核验）：\n"
        "- 常见流程包括下料与折弯。[W9]"
    )
    bad_decision = delivery_decision(bad_citation, [local, web], audit_claims(bad_citation, [local, web], {}))
    assert bad_decision["deliverable"] is False
    assert any(item["status"] == "citation_invalid" for item in bad_decision["blocking_issues"])


def test_natural_web_supplement_phrasing_passes_without_magic_heading():
    """Honest descriptive [W1] statements pass regardless of heading wording; web recommendations stay blocked."""
    local = direct_doc()
    web = indirect_doc()

    natural = (
        "现有资料介绍了折弯特征加工要点 [L1]。\n"
        "常见工艺还包含激光下料、折弯成形、焊接装配与表面涂装 [W1]。"
    )
    decision = delivery_decision(natural, [local, web], audit_claims(natural, [local, web], {}))
    assert decision["deliverable"] is True, decision["blocking_issues"]

    web_rec = "建议优先选择该工艺方案 [W1]。"
    rec_decision = delivery_decision(web_rec, [web], audit_claims(web_rec, [web], {}))
    assert rec_decision["deliverable"] is False


def test_bold_subheading_and_honest_refusal_phrasing_are_not_false_blocked():
    """Bold subheadings and '不能据此确认' refusals must not falsely block a web-supplemented answer."""
    web = indirect_doc()
    answer = (
        "**本地知识库确认**\n\n"
        "当前本地资料均为 indirect 证据，不能据此确认 MIM 金属注射成型适合生产哪些零件。\n\n"
        "**网络补充参考（需核验）**\n\n"
        "- MIM 工艺常用于大批量生产小型、结构复杂的精密金属零件，如手机卡托、医疗器械微型件和齿轮。[W1]\n\n"
        "**有限建议（非资料原文结论）**\n\n"
        "如需工程定型，仍需结合零件重量、壁厚和材料牌号进一步验证。"
    )
    audit = audit_claims(answer, [web], {})
    decision = delivery_decision(answer, [web], audit)
    assert decision["deliverable"] is True, decision["blocking_issues"]


def test_ordered_list_numbers_and_3d_token_are_not_treated_as_unreferenced_measurements():
    """Ordered-list prefixes (1., 2.) and '3D' must not trigger unreferenced numeric blocks."""
    local = direct_doc()
    web = indirect_doc()
    answer = (
        "SLA 可选后处理包括涂装、透明件精整和纹理。[L1]\n\n"
        "1. **先做样件对比**：在设备、材料、模型方向相同的条件下测试不同可用层厚，并比较层纹和打印时间。\n"
        "2. **需要外观处理时再评估后处理**：适用于 3D 打印外观件；应先在非关键区域验证，并复测关键尺寸。\n\n"
        "网络资料称光固化层纹可从曝光参数和后处理打磨入手改善。[W1]"
    )
    claims = extract_claims(answer)
    list_claim = next(c for c in claims if "先做样件对比" in c["text"])
    second_claim = next(c for c in claims if "需要外观处理时再评估后处理" in c["text"])
    assert list_claim["numbers"] == [], list_claim["numbers"]
    assert second_claim["numbers"] == [], second_claim["numbers"]

    audit = audit_claims(answer, [local, web], {})
    decision = delivery_decision(answer, [local, web], audit)
    assert decision["deliverable"] is True, decision["blocking_issues"]


def test_qualitative_indirect_background_caveat_passes_while_numeric_indirect_fact_blocks():
    """Qualitative background notes citing an indirect handbook pass; numeric/fact claims still block."""
    handbook = Document(
        page_content="PET瓶生产需要先注塑瓶坯再拉伸吹塑。",
        metadata={
            "source_type": "local",
            "source": "实用工程塑料手册.pdf",
            "title": "实用工程塑料手册",
            "retrieval_evidence_status": "indirect",
        },
    )
    direct = direct_doc()
    caveat_answer = (
        "前期需确认树脂牌号、干燥与脱模设计。[L1]\n"
        "若生产的是 PET 瓶，瓶坯注射后还需再加热、拉伸吹塑和冷却，不能等同于普通注塑流程。[L2]"
    )
    decision = delivery_decision(
        caveat_answer,
        [direct, handbook],
        audit_claims(caveat_answer, [direct, handbook], {}),
    )
    assert decision["deliverable"] is True, decision["blocking_issues"]

    numeric_indirect = "PET 瓶坯再加热温度应控制在 110℃。[L1]"
    bad_decision = delivery_decision(
        numeric_indirect,
        [handbook],
        audit_claims(numeric_indirect, [handbook], {}),
    )
    assert bad_decision["deliverable"] is False
    assert any(item["status"] == "indirect_evidence" for item in bad_decision["blocking_issues"])
