"""Shared document metadata and evidence-scope classification.

The ingestion pipeline writes conservative metadata.  Query-time classification
uses the same fields and never upgrades an indirect source to direct evidence
just because its wording is similar to the question.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any


UNKNOWN = "unknown"


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _path_text(relative_source: str, title: str) -> str:
    return f"{relative_source} {title}".replace("\\", "/")


def _contains_any(text: str, values: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(value.lower() in lowered for value in values)


def _material_family(value: str) -> str:
    lowered = (value or "").lower()
    aliases = {
        "pc": ("pc", "lexan", "聚碳酸酯"),
        "pp": ("pp", "聚丙烯"),
        "tritan": ("tritan",),
        "ppsu": ("ppsu", "radel"),
        "304": ("304",),
        "316": ("316",),
        "silicone": ("silicone", "硅胶"),
        "glass": ("glass", "玻璃"),
        "ceramic": ("ceramic", "陶瓷"),
    }
    for family, tokens in aliases.items():
        if any(token in lowered for token in tokens):
            return family
    return UNKNOWN


def infer_source_category(relative_source: str, title: str, content: str = "") -> str:
    text = _path_text(relative_source, title)
    # Filename/path rules take precedence over incidental citations in body
    # text.  A processing guide may quote a GB standard without becoming a
    # standard document, and a TDS should remain a supplier-data source.
    if _contains_any(text, ("tds", "technical_data", "technical data", "product_data", "data_sheet", "datasheet")):
        return "material_tds"
    if _contains_any(text, ("processing", "processing_guide", "molding", "mould", "dfm", "drying", "extrusion", "制造工艺", "注塑", "吹塑", "工艺指南")):
        return "process_guide"
    if _contains_any(text, ("法规与标准", "gb4806", "gb/t", "iso", "astm", "en_", "iec")):
        return "standard"
    if _contains_any(text, ("anthropometry", "人体工程学", "hand_", "hand dimensions", "nist")):
        return "anthropometry"
    if _contains_any(text, ("tds", "technical_data", "technical data", "product_data", "data_sheet")):
        return "material_tds"
    if _contains_any(text, ("processing", "molding", "mould", "dfm", "制造工艺", "注塑", "吹塑")):
        return "process_guide"
    if "08_设计决策" in text or "设计规则" in text or "decision" in text.lower():
        return "internal_rule"
    if _contains_any(text, ("paper", "论文", "journal", "research", "study")):
        return "paper"
    if _contains_any(text, ("handbook", "手册", "guide", "指南")):
        return "reference_handbook"
    if content and _contains_any(content[:3000], ("中华人民共和国国家标准", "national standard")):
        return "standard"
    return UNKNOWN


def infer_evidence_level(relative_source: str, title: str, source_category: str) -> str:
    text = _path_text(relative_source, title)
    if source_category == "standard" or re.search(r"\b(?:GB(?:/T)?|ISO|IEC|ASTM|EN|DIN|JIS)[ _./A-Za-z0-9—-]*", text, re.I):
        return "standard"
    if source_category == "material_tds":
        return "supplier_data"
    if source_category == "paper":
        return "paper"
    if source_category == "internal_rule":
        return "internal_rule"
    if source_category == "reference_handbook":
        return "reference_handbook"
    if source_category == "process_guide":
        return "supplier_or_process_guide"
    return UNKNOWN


def infer_population(relative_source: str, title: str, content: str = "") -> str:
    text = _path_text(relative_source, title) + " " + (content[:2500] or "")
    if _contains_any(text, ("未成年人", "儿童", "children", "minors", "juvenile")):
        return "minors"
    if _contains_any(text, ("老年", "elderly", "older adult")):
        return "elderly"
    # This mapping is limited to explicit standard identity, not an inference
    # from a generic word such as "adult" in an unrelated paragraph.
    if re.search(r"(?:16252|10000)[ _+—-]*2023", text, re.I):
        return "adults"
    if _contains_any(text, ("成人", "成年人", "adult anthropometry", "adult dimensions")):
        return "adults"
    return UNKNOWN


def infer_material_grade(relative_source: str, title: str, content: str = "") -> str:
    text = _path_text(relative_source, title)
    explicit_patterns = (
        r"\b(?:LEXAN|RADel|RADEL|TX|TRITAN|PPSU|PP|PC|PET|HDPE)[ _-]+[A-Z0-9]+",
        r"\b(?:304|316)\b",
    )
    for pattern in explicit_patterns:
        match = re.search(pattern, text, re.I)
        if match:
            candidate = match.group(0).replace("_", " ").strip()
            tokens = candidate.split()
            while tokens and tokens[-1].upper() in {"TDS", "DATA", "GUIDE", "SHEET"}:
                tokens.pop()
            if len(tokens) > 1:
                return " ".join(tokens)
    for grade in ("Tritan", "PC", "PP", "PPSU", "PET", "HDPE", "Silicone", "304", "316", "Glass", "Ceramic"):
        if re.search(rf"(?<![A-Za-z]){re.escape(grade)}(?![A-Za-z])", text, re.I):
            return grade
    return UNKNOWN


def infer_applicability(relative_source: str, title: str, content: str = "") -> str:
    """Extract only explicit applicability text; otherwise return unknown."""
    text = _normalise(content[:4000])
    patterns = (
        r"(?:适用范围|适用于|适用条件)[:： ]*([^。\n]{4,160})",
        r"(?:scope|applicable to|service condition)\s*[:：]\s*([^\.\n]{4,160})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return _normalise(match.group(1))
    return UNKNOWN


def infer_section(content: str) -> str:
    for line in (content or "").splitlines()[:20]:
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip() or UNKNOWN
    return UNKNOWN


def build_document_metadata(relative_source: str, title: str, content: str = "", page: int | None = None) -> dict[str, Any]:
    category = infer_source_category(relative_source, title, content)
    role_rule = "path_keyword" if category in {"material_tds", "process_guide", "standard"} else "content_or_fallback"
    return {
        "source": PurePosixPath(relative_source.replace("\\", "/")).as_posix(),
        "source_title": title or UNKNOWN,
        "title": title or UNKNOWN,
        "page": page if page is not None else UNKNOWN,
        "section": infer_section(content),
        "source_category": category,
        "source_role": category,
        "source_role_rule": role_rule,
        "source_role_confidence": 0.95 if role_rule == "path_keyword" else 0.55,
        "evidence_level": infer_evidence_level(relative_source, title, category),
        "population": infer_population(relative_source, title, content),
        "material_grade": infer_material_grade(relative_source, title, content),
        "applicability": infer_applicability(relative_source, title, content),
    }


def question_profile(question: str) -> dict[str, Any]:
    text = question or ""
    lowered = text.lower()
    references = re.findall(r"(?:GB/T|GB|ISO|IEC|ASTM|EN|DIN|JIS)\s*[A-Za-z0-9./—-]+", text, re.I)
    materials = [name for name in ("PC", "PP", "Tritan", "PPSU", "PET", "HDPE", "304", "316", "硅胶", "Silicone", "玻璃", "陶瓷") if re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", text, re.I)]
    dimension = _contains_any(text, ("尺寸", "直径", "杯径", "握持", "人体工学", "手部", "高度", "容量", "尺寸建议"))
    material = _contains_any(text, ("材料", "材质", "牌号", "耐热", "抗冲击", "食品接触", "选材", "合规"))
    food_contact = _contains_any(text, ("食品接触", "迁移", "饮用水", "食品安全", "food contact"))
    process = _contains_any(text, ("注塑", "吹塑", "冲压", "焊接", "工艺", "加工", "dfm", "成型"))
    testing = _contains_any(text, ("测试", "验证", "跌落", "泄漏", "耐久", "洗碗机", "可靠性"))
    return {
        "adult": _contains_any(text, ("成人", "成年人", "adult")) and not _contains_any(text, ("未成年人", "儿童", "children")),
        "minors": _contains_any(text, ("未成年人", "儿童", "children", "minors")),
        "references": references,
        "materials": materials,
        "dimension": dimension,
        "material": material,
        "food_contact": food_contact,
        "process": process,
        "testing": testing,
    }


def classify_document_for_question(
    question: str,
    metadata: dict[str, Any],
    content: str = "",
    relevance: float | None = None,
    min_relevance: float = 0.25,
) -> tuple[str, str]:
    """Return (direct|indirect|scope_mismatch, reason)."""
    profile = question_profile(question)
    category = str(metadata.get("source_category", UNKNOWN))
    population = str(metadata.get("population", UNKNOWN))
    grade = str(metadata.get("material_grade", UNKNOWN))
    source_text = " ".join(str(metadata.get(key, "")) for key in ("source", "source_title", "title", "section", "applicability")) + " " + (content or "")
    lowered = source_text.lower()

    if profile["adult"] and population == "minors":
        return "scope_mismatch", "目标问题面向成人，但资料范围为未成年人"
    if profile["minors"] and population == "adults":
        return "scope_mismatch", "目标问题面向未成年人，但资料范围为成人"
    if profile["materials"] and grade != UNKNOWN:
        requested = {_material_family(item) for item in profile["materials"]}
        requested.discard(UNKNOWN)
        if not any(
            family == _material_family(grade)
            or family == _material_family(lowered)
            for family in requested
        ):
            if category == "material_tds":
                return "scope_mismatch", "材料牌号与问题指定材料不匹配"

    if relevance is not None and relevance < min_relevance:
        return "indirect", f"向量相关性 {relevance:.3f} 低于直接证据阈值 {min_relevance:.3f}"
    if profile["dimension"]:
        if category in {"anthropometry", "standard", "paper", "internal_rule", "process_guide"}:
            direct_markers = ("握持", "grip", "杯径", "cup diameter", "圆柱握持", "操作实验")
            product_markers = ("水杯", "饮水容器", "cup", "container", "杯口", "密封")
            if _contains_any(source_text, direct_markers):
                return "direct", "资料包含目标产品的握持、操作或直接尺寸关系"
            if category == "anthropometry" or not _contains_any(source_text, product_markers):
                return "indirect", "人体或一般尺寸资料不能直接证明目标产品尺寸"
            return "direct", "资料包含目标产品尺寸或接口相关内容"
    if profile["food_contact"] and category in {"standard", "material_tds"}:
        return "direct", "资料属于法规或材料合规数据"
    if profile["material"] and category in {"material_tds", "standard", "reference_handbook", "internal_rule", "process_guide"}:
        if grade != UNKNOWN or category == "standard":
            return "direct", "资料包含材料、牌号、法规或加工限制"
        return "indirect", "资料属于材料相关背景，但缺少具体牌号"
    if profile["process"] and category in {"process_guide", "material_tds", "internal_rule"}:
        return "direct", "资料包含制造工艺或成型限制"
    if profile["testing"] and category in {"standard", "paper", "process_guide", "internal_rule", "material_tds"}:
        return "direct", "资料包含测试、验证或失效相关内容"
    if profile["references"] and any(ref.replace(" ", "").lower() in lowered.replace(" ", "") for ref in profile["references"]):
        return "direct", "资料命中用户指定标准或参考编号"
    if category != UNKNOWN:
        return "indirect", "资料与问题领域相关，但未形成直接支持"
    return "indirect", "资料适用范围或证据类别未知"
