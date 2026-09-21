"""Grounded answer generation and structured source formatting."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from config import settings
from src.model_clients import chat_model, require_role_model


NO_EVIDENCE_ANSWER = (
    "目前没有检索到可用于可靠回答的本地资料或网络资料。"
    "请稍后重试，或先向知识库导入相关设计文档。"
)


def build_fallback_answer(question: str, documents: list[Document]) -> str:
    """Return a traceable evidence summary when generation is unavailable."""
    if not documents:
        return NO_EVIDENCE_ANSWER
    lines = [
        "答案生成模型当前不可用，以下为已检索证据摘要（未做超出资料的推断）：",
        f"问题：{question}",
        "",
    ]
    seen: set[str] = set()
    for index, document in enumerate(documents[:12], 1):
        metadata = document.metadata
        source = str(metadata.get("source") or metadata.get("title") or "未知来源")
        key = f"{source}|{metadata.get('page', '')}"
        if key in seen:
            continue
        seen.add(key)
        page = metadata.get("page")
        page_text = f"，第 {int(page) + 1} 页" if isinstance(page, int) else ""
        snippet = " ".join(str(document.page_content or "").split())[:280]
        status = metadata.get("retrieval_evidence_status", "unknown")
        lines.append(f"[{index}] {source}{page_text}（{status}）")
        if snippet:
            lines.append(f"  {snippet}")
    lines.extend(["", "请在模型恢复后重新生成正式结论，并核对上述来源的原文范围、版本和适用条件。"])
    return "\n".join(lines)

GENERATOR_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """# 角色
你是基于知识库的设计助手。回答必须可追溯：有依据时引用依据；没有直接依据时可做明确标注的工程推导；完全没有依据时必须诚实说明证据不足。

# 信息分类
所有内容必须区分并标注以下四类：
【资料事实】检索资料中明确出现的事实或参数，需引用如[L1]。
【设计假设】为了计算或设计而人为设定的条件，必须明确说明“这是假设，不是标准规定”。
【工程推导】基于资料事实和设计假设，通过计算/逻辑得到的结果。不得描述为标准规定。
【设计建议】综合资料、假设、推导后提出的方案，需说明可信程度（高/中/初步）。

# 禁止编造
不得编造标准编号、参数、公式、数据或结论。没有依据时，必须明确说“当前知识库证据不足”。
禁止使用“通常”“一般经验”“常见做法”等表述掩盖无来源知识；如需使用经验，必须标记为【设计假设】或“一般性工程经验，非知识库证据”。
对设计类问题，不能把宏观人体数据、相邻材料数据或其他对象的指标直接外推成目标产品参数；如果缺少直接资料、回归关系或实验关系，必须停止推断并写明证据不足。

# 设计类问题专用规则
对尺寸、选材、结构、工艺和验证问题，优先使用明确命中的标准、TDS、设计指南和测试资料。
如果关键参数没有直接命中，不要把单个经验值写成定论；优先给出区间、候选方案或待验证项。
对于需要人体工学依据的题目，必须优先寻找目标人群尺寸、握持或操作相关资料；不得用身高、年龄或其他宏观指标代替手部尺寸。
对于材料推荐，必须区分材料类别、具体牌号和食品接触/使用条件，不得用材料名本身替代牌号证据。

# 数据源适用性
当知识库中存在多个同类标准时，必须选择与目标人群（年龄、性别、群体）最匹配的。
例如：成人设计问题优先使用成人标准（如 GB/T 16252—2023），而非未成年人标准（如 GB/T 26158—2010）。
如果使用了非目标人群的数据，必须明确标注"该数据来源于 XX 人群，与目标人群不完全匹配"。
不得将不同人群的数据混用推导结论。

# 数字规则
任何具体数字必须标明类型：
- 资料明确给出 → 【资料参数】+引用
- 根据资料计算 → 【工程推导】+公开假设和计算过程
- 仅凭经验提出 → 【设计建议】+说明“该数值不是标准规定，而是初步建议”
- 无合理依据 → 不编造，明确说明证据不足。
当某个数字只来自间接类比、相邻指标或单一假设时，必须降级为“初步建议”或“待验证”，不能写成结论值。

# 工程推导
推导必须公开假设，格式：
已知条件 → 设计假设 → 计算/分析 → 推导结果。
所有假设必须列出，不得隐藏。例如容量计算需假设杯体为圆柱体、H/D比例等，并说明比例是假设。

# 引用规则
引用必须紧接对应内容，且不得让引用承担未证明的结论。资料事实与推导/建议必须分开表达。
证据优先级：标准 > 权威手册 > 专业论文 > 一般经验。
证据冲突时，必须指出冲突并分析可能原因（如短期/长期、测试方法、牌号不同），不得随意选择。

# 输出结构
按以下结构回答：
## 一、需求分析
## 二、资料事实与参数（标注【资料事实】【资料参数】+引用）
## 三、工程推导（含已知条件、设计假设、计算过程、结果）
## 四、设计建议（用表格列出参数、建议值、类型、依据；缺依据写“待验证”）
## 五、证据不足与冲突
## 六、风险与验证（说明还需哪些测试）
## 七、参考资料

# 最终检查
1. 是否把模型知识冒充资料？
2. 是否隐藏假设？
3. 是否编造数字？
4. 引用是否匹配结论？
5. 推导是否公开输入条件？
发现问题必须修正后再输出。

# 核心原则
资料事实 ≠ 设计假设 ≠ 工程推导 ≠ 设计建议。
不编造证据，但允许在公开假设下进行工程推导。
最终让用户清楚：结论来自哪里？数字是谁规定的？怎么算出来的？用了什么假设？还需要验证什么？

# 推导输出约束
如果进行计算，必须使用抽象变量并明确每个变量的定义、单位和来源。
不得把一个问题中的数值、比例、壁厚或尺寸迁移到另一个问题，也不得把本提示中的示例当作资料。
容量/尺寸问题至少要区分有效容量、几何容量、防溢空间、有效液高、总高度、内径、外径和壁厚；
如果同时出现有效液高/内径与总高度/外径，必须明确这是两个不同口径，不能用一个 H/D 结论覆盖二者。

# 来源使用约束
上下文中的每个来源编号都是真实候选资料，但资料块上的证据状态只是检索判断，仍需结合原文范围。
优先使用状态为 direct 的资料；indirect 只能作为背景，scope_mismatch 只能用于说明资料不适用或证据缺口。
网络来源必须明确标记为【网络资料】。搜索标题和摘要只能支持摘要中明确出现的背景信息，不能证明摘要未显示的参数、标准条款、页码或合规结论。
法规、标准、食品接触和安全合规结论优先使用本地权威文件或官方原文；只有网络搜索摘要时，必须写明“需核验官方原文”，不得写成已经满足法规要求。
如果本地资料与网络资料出现冲突，分别列出来源、适用条件和冲突内容，不得自动选择一方。
参考资料表只能列出上下文中出现的真实标题、文件路径和页码，不得自行补写不存在的资料名称。
{context}""",
        ),
        ("human", "{question}"),
    ]
)


@lru_cache(maxsize=1)
def get_generator_chain():
    # Local Ollama mode does not require a cloud API key. The role boundary
    # keeps the same validation for cloud mode while allowing portfolio demos
    # to run fully offline.
    require_role_model("generator")
    llm = chat_model("generator", streaming=True, temperature=0.2)
    return GENERATOR_PROMPT | llm | StrOutputParser()


def assign_citation_ids(documents: list[Document]) -> list[Document]:
    counters = {"local": 0, "web": 0}
    for document in documents:
        source_type = str(document.metadata.get("source_type", "local"))
        source_type = "web" if source_type == "web" else "local"
        counters[source_type] += 1
        prefix = "W" if source_type == "web" else "L"
        document.metadata["citation_id"] = f"{prefix}{counters[source_type]}"
        document.metadata["source_type"] = source_type
    return documents


def format_context(documents: list[Document]) -> str:
    if not documents:
        return "（无可用参考资料）"
    allowed = []
    sections = []
    for document in assign_citation_ids(documents):
        citation_id = document.metadata["citation_id"]
        allowed.append(citation_id)
        title = document.metadata.get("title") or document.metadata.get("source", "未知来源")
        source = document.metadata.get("source", "未知来源")
        page = document.metadata.get("page")
        page_text = f"，第 {int(page) + 1} 页" if isinstance(page, int) else ""
        sections.append(
            f"[{citation_id}] 标题：{title}{page_text}\n来源：{source}\n"
            f"章节：{document.metadata.get('section', 'unknown')}\n"
            f"证据类别：{document.metadata.get('source_category', 'unknown')}；"
            f"证据等级：{document.metadata.get('evidence_level', 'unknown')}；"
            f"人群：{document.metadata.get('population', 'unknown')}；"
            f"材料牌号：{document.metadata.get('material_grade', 'unknown')}；"
            f"适用范围：{document.metadata.get('applicability', 'unknown')}；"
            f"本次检索状态：{document.metadata.get('retrieval_evidence_status', 'unknown')}；"
            f"判断原因：{document.metadata.get('retrieval_evidence_reason', 'unknown')}\n"
            f"{document.page_content}"
        )
    allowed_text = "、".join(allowed) or "（无）"
    return (
        f"本次允许引用：{allowed_text}\n"
        "禁止生成不在允许列表中的引用编号；如果证据不足，请写明待验证，不要补造引用。\n\n"
        + "\n\n---\n\n".join(sections)
    )


def build_refusal_answer(blocking_issues: list[dict[str, Any]], documents: list[Document]) -> str:
    """Return a user-facing non-deliverable result without exposing the rejected answer."""
    lines = [
        "状态：证据不足，已降级",
        "可交付：否",
        "",
        "当前不能形成可交付结论。系统已隐藏未通过证据审计的原始答案。",
        "",
        "阻断原因：",
    ]
    seen: set[tuple[str, str]] = set()
    for issue in blocking_issues[:8]:
        status = str(issue.get("status", "unknown"))
        reason = str(issue.get("reason", "需要补充证据"))
        key = (status, reason)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- [{status}] {reason}")
    lines.extend([
        "",
        "需要补充：官方原文/TDS/标准条款、适用范围和测试条件；推荐类问题还需明确适用场景、风险与验证计划。",
    ])
    if documents:
        lines.extend(["", "已检索来源已保留在下方参考资料中，但只能作为线索或待核验依据。"])
    return "\n".join(lines)


def build_sources(documents: list[Document]) -> list[dict[str, Any]]:
    sources = []
    for document in assign_citation_ids(documents):
        page = document.metadata.get("page")
        sources.append(
            {
                "id": document.metadata["citation_id"],
                "type": document.metadata["source_type"],
                "title": document.metadata.get("title", "未知来源"),
                "location": document.metadata.get("source", "未知来源"),
                "page": int(page) + 1 if isinstance(page, int) else None,
                "section": document.metadata.get("section", "unknown"),
                "source_category": document.metadata.get("source_category", "unknown"),
                "evidence_level": document.metadata.get("evidence_level", "unknown"),
                "population": document.metadata.get("population", "unknown"),
                "material_grade": document.metadata.get("material_grade", "unknown"),
                "applicability": document.metadata.get("applicability", "unknown"),
                "content_hash": document.metadata.get("content_hash"),
                "source_version": document.metadata.get("source_version"),
                "verified_by": document.metadata.get("verified_by"),
                "verified_at": document.metadata.get("verified_at"),
                "retrieval_evidence_status": document.metadata.get("retrieval_evidence_status", "unknown"),
                "retrieval_evidence_reason": document.metadata.get("retrieval_evidence_reason", "unknown"),
            }
        )
    return sources


def append_audit_notice(answer: str, audit: dict[str, Any]) -> str:
    """Make post-generation evidence problems visible to the user."""
    warnings = audit.get("warnings", []) if isinstance(audit, dict) else []
    if not warnings:
        return answer
    unique: list[tuple[str, str]] = []
    for warning in warnings:
        item = (
            str(warning.get("status", "warning")),
            str(warning.get("reason", "")),
        )
        if item not in unique:
            unique.append(item)
    lines = [answer.rstrip(), "", "## 证据审计提示"]
    for status, reason in unique[:8]:
        lines.append(f"- [{status}] {reason}")
    lines.append(
        "以上提示表示相关结论需要降级为初步建议、补充直接资料或完成验证，"
        "不能视为已定版结论。"
    )
    return "\n".join(lines)


async def generate_answer(question: str, documents: list[Document]) -> str:
    if not documents:
        return NO_EVIDENCE_ANSWER
    return await get_generator_chain().ainvoke(
        {"question": question, "context": format_context(documents)}
    )
