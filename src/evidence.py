"""Generic claim-level evidence auditing for design answers.

This module intentionally uses claim and source metadata rather than product
names. It can audit answers about any design object in the knowledge base.
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.documents import Document

from src.document_metadata import UNKNOWN


CLAIM_TYPES = {"direct_fact", "derived_result", "design_inference", "assumption", "compliance_claim", "unsupported"}
_CITATION_RE = re.compile(r"\[([LW]\d+)\]")
_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?(?:\s*[~～至到-]\s*[-+]?\d+(?:\.\d+)?)?\s*(?:mm|cm|mL|℃|°|%|MPa|kJ/m²)?", re.IGNORECASE)
_CONFLICT_MARKERS = ("但是", "然而", "相反", "不一致", "冲突", "分别")
_REFERENCE_RE = re.compile(r"(?:GB/T|ISO|IEC|ASTM|EN|DIN|JIS|RFC)\s*[A-Za-z0-9./—\-]+", re.IGNORECASE)
_BLOCKING_STATUSES = {
    "citation_invalid",
    "recommendation_unconditional",
    "indirect_evidence",
    "scope_mismatch",
    "calculation_inconsistent",
    "web_evidence_unverified",
}


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", text)
    return [part.strip(" -*•\t") for part in parts if len(part.strip()) >= 6]


def _is_structural_sentence(text: str) -> bool:
    value = text.strip()
    if re.match(r"^#{1,6}\s*", value):
        return True
    if value.startswith("```") or value in {"---", "***", "___"}:
        return True
    if value.startswith("|"):
        if re.search(r"^\|?\s*:?-{3,}", value):
            return True
        cells = [cell.strip() for cell in value.strip("|").split("|")]
        header_words = {"方案", "材料", "结论", "证据", "来源", "状态", "风险", "验证", "推荐"}
        if cells and all(cell in header_words for cell in cells if cell):
            return True
    if re.fullmatch(r"[【\[]?(?:资料事实|设计建议|待验证项|验证建议|结论|限制与风险)[】\]]?[:：]?", value):
        return True
    if re.fullmatch(r"(?:\[[LW]\d+\]\s*)+", value):
        return True
    return False


def _claim_role(text: str, claim_type: str) -> tuple[str, bool]:
    if _is_structural_sentence(text):
        return "structural", False
    if re.search(r"无法(?:科学地)?给出|资料不足|不能依据|无法支持|不应定案|需要.*验证", text):
        return "refusal", True
    if re.search(r"待验证|限制|风险|尚需|仍需", text):
        return "limitation", True
    return "substantive", True


def classify_claim(text: str) -> str:
    if re.search(r"无法(?:科学地)?给出|资料不足|不能依据|无法支持|不应定案", text):
        return "refusal_claim"
    if re.search(r"资料事实|直接资料|原文指出", text):
        return "direct_fact"
    if re.search(r"设计推断|综合推导|推导结果", text):
        return "derived_result"
    if re.search(r"合规待验证|待验证|需要验证", text):
        return "compliance_claim"
    if re.search(r"假设|假定|如果|在[^。！？]*情况下|前提", text):
        return "assumption"
    if re.search(r"符合|合规|法规|标准要求|安全认证|食品接触|不得|必须", text):
        return "compliance_claim"
    if re.search(r"建议|推荐|适合|优先|更好|应当|应该|因此|综合来看|说明", text):
        return "design_inference"
    if _CITATION_RE.search(text):
        return "direct_fact"
    if re.search(r"推算|计算|由此|意味着|综合", text):
        return "derived_result"
    return "unsupported"


def extract_claims(answer: str) -> list[dict[str, Any]]:
    claims = []
    for index, sentence in enumerate(_sentences(answer), 1):
        citations = _CITATION_RE.findall(sentence)
        claim_type = classify_claim(sentence)
        role, auditable = _claim_role(sentence, claim_type)
        if not auditable and role == "structural":
            continue
        claims.append({
            "id": f"claim-{index}",
            "text": sentence,
            "type": claim_type,
            "claim_role": role,
            "is_auditable": auditable,
            "citations": citations,
            "numbers": _NUMBER_RE.findall(sentence),
            "evidence_status": "unreviewed",
            "confidence": 0.0,
        })
    return claims


def _source_text(document: Document) -> str:
    metadata_text = " ".join(
        str(document.metadata.get(key, ""))
        for key in (
            "source",
            "source_title",
            "title",
            "section",
            "scope",
            "population",
            "applicability",
            "material_grade",
            "source_category",
            "evidence_level",
        )
    )
    return f"{metadata_text} {document.page_content[:4000]}"


def _document_for_citation(citation: str, documents: list[Document]) -> Document | None:
    if not re.fullmatch(r"[LW]\d+", citation or ""):
        return None
    exact = [
        document
        for document in documents
        if str(document.metadata.get("citation_id", "")) == citation
    ]
    if exact:
        return exact[0]
    try:
        index = int(citation[1:]) - 1
        source_type = "local" if citation.startswith("L") else "web"
        candidates = [
            doc
            for doc in documents
            if str(doc.metadata.get("source_type", "local")) == source_type
        ]
        return candidates[index] if 0 <= index < len(candidates) else None
    except (ValueError, IndexError):
        return None


def allowed_citations(documents: list[Document]) -> list[str]:
    """Return the citation IDs that are valid for this answer context."""
    counters = {"local": 0, "web": 0}
    allowed: list[str] = []
    for document in documents:
        source_type = "web" if str(document.metadata.get("source_type", "local")) == "web" else "local"
        counters[source_type] += 1
        citation = f"{'W' if source_type == 'web' else 'L'}{counters[source_type]}"
        document.metadata["citation_id"] = citation
        document.metadata["source_type"] = source_type
        allowed.append(citation)
    return allowed


def validate_citations(answer: str, documents: list[Document]) -> dict[str, Any]:
    """Hard-check every citation against the documents in the current run."""
    allowed = set(allowed_citations(documents))
    used = set(_CITATION_RE.findall(answer or ""))
    invalid = sorted(used - allowed)
    return {
        "allowed_citations": sorted(allowed, key=lambda value: (value[0], int(value[1:]))),
        "used_citations": sorted(used),
        "invalid_citations": invalid,
        "issues": ([{
            "status": "citation_invalid",
            "reason": f"引用编号不存在或不属于本次证据集合：{', '.join(invalid)}",
        }] if invalid else []),
    }


def validate_recommendations(claims: list[dict[str, Any]], documents: list[Document]) -> list[dict[str, Any]]:
    """Require conditions, risks, verification and usable evidence for recommendations."""
    issues: list[dict[str, Any]] = []
    for claim in claims:
        if not claim.get("is_auditable", True) or claim.get("type") not in {"design_inference", "derived_result"}:
            continue
        text = str(claim.get("text", ""))
        if not re.search(r"推荐|建议|优先|更适合|选择|应当|应该", text):
            continue
        has_condition = bool(re.search(r"条件|场景|要求|如果|前提|适用|取决于", text))
        has_risk = bool(re.search(r"风险|限制|局限|注意|不确定|代价|成本", text))
        has_verification = bool(re.search(r"验证|核实|确认|测试|补充资料|待查", text))
        citations = claim.get("citations", [])
        valid_docs = [doc for citation in citations if (doc := _document_for_citation(citation, documents)) is not None]
        if not (has_condition and has_risk and has_verification and valid_docs):
            issues.append({
                "status": "recommendation_unconditional",
                "reason": "推荐必须同时包含适用条件、风险/限制、验证要求和有效证据引用",
                "claim_id": claim.get("id"),
                "claim": text,
            })
        elif not any(doc.metadata.get("retrieval_evidence_status") == "direct" for doc in valid_docs):
            issues.append({
                "status": "indirect_evidence",
                "reason": "推荐仅由间接或未核验资料支持，不能形成可交付定案",
                "claim_id": claim.get("id"),
                "claim": text,
            })
    return issues


def delivery_decision(answer: str, documents: list[Document], audit: dict[str, Any] | None = None,
                      task_errors: list[str] | None = None) -> dict[str, Any]:
    """Convert audit findings into the public delivery contract."""
    audit = audit or {}
    citation_check = validate_citations(answer, documents)
    claims = list(audit.get("claims", []))
    issues = list(citation_check["issues"])
    issues.extend(validate_recommendations(claims, documents))
    for claim in claims:
        evidence_status = str(claim.get("evidence_status", ""))
        if not claim.get("is_auditable", True) or claim.get("claim_role") == "refusal":
            continue
        if evidence_status in {"unsupported", "unreferenced", "citation_invalid", "scope_mismatch",
                               "calculation_inconsistent", "recommendation_unconditional", "indirect_evidence"}:
            # Keep terse service/fallback acknowledgements compatible; only
            # substantive claims (numbers, facts, compliance, or advice) are
            # hard-blocked for missing evidence.
            claim_type = str(claim.get("type", ""))
            if evidence_status in {"unsupported", "unreferenced"} and not (
                claim.get("numbers") or claim_type in {"direct_fact", "compliance_claim", "design_inference", "derived_result"}
            ):
                continue
            issues.append({
                "status": evidence_status,
                "reason": "主张没有满足交付要求的可定位证据",
                "claim_id": claim.get("id"),
                "claim": claim.get("text", ""),
            })
    for warning in audit.get("warnings", []):
        status = str(warning.get("status", ""))
        if status in _BLOCKING_STATUSES:
            issue = {key: warning.get(key) for key in ("status", "reason", "claim_id", "claim") if warning.get(key) is not None}
            if issue not in issues:
                issues.append(issue)
    for error in task_errors or []:
        issues.append({"status": "retrieval_incomplete", "reason": str(error)})
    # Deduplicate by status/claim_id so one claim cannot inflate failure counts.
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for issue in issues:
        key = (str(issue.get("status", "unknown")), str(issue.get("claim_id", "")))
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    deliverable = not unique and bool(documents)
    summary: dict[str, int] = {}
    for issue in unique:
        status = str(issue.get("status", "unknown"))
        summary[status] = summary.get(status, 0) + 1
    return {
        "deliverable": deliverable,
        "delivery_status": "completed" if deliverable else "degraded_insufficient_evidence",
        "blocking_issues": unique,
        "allowed_citations": citation_check["allowed_citations"],
        "blocking_issue_summary": summary,
    }
def assess_source_applicability(claim: dict[str, Any], documents: list[Document]) -> list[dict[str, Any]]:
    assessments = []
    for citation in claim.get("citations", []):
        document = _document_for_citation(citation, documents)
        if document is None:
            assessments.append({"citation": citation, "status": "missing", "reason": "引用编号没有对应资料"})
            continue
        source_text = _source_text(document).lower()
        claim_text = claim["text"].lower()
        status = "full"
        reason = None
        claim_refs = [ref.lower().replace(" ", "") for ref in _REFERENCE_RE.findall(claim["text"])]
        if claim_refs and not any(ref in source_text.replace(" ", "") for ref in claim_refs):
            status, reason = "mismatch", "主张指定的来源标识未出现在被引用文档的范围信息中"
        if "成年人" in claim_text and "未成年人" in source_text and "成年人" not in source_text:
            status, reason = "mismatch", "主张面向成年人，但来源范围显示为未成年人"
        elif "未成年人" in claim_text and "成年人" in source_text and "未成年人" not in source_text:
            status, reason = "mismatch", "主张面向未成年人，但来源范围显示为成年人"
        if document.metadata.get("retrieval_evidence_status") == "scope_mismatch":
            status, reason = "mismatch", str(
                document.metadata.get(
                    "retrieval_evidence_reason", "检索判定该资料适用范围不匹配"
                )
            )
        elif document.metadata.get("retrieval_evidence_status") == "indirect":
            status, reason = "indirect", str(
                document.metadata.get(
                    "retrieval_evidence_reason", "检索判定该资料只能作为间接参考"
                )
            )
        assessments.append({
            "citation": citation,
            "source": document.metadata.get("source", ""),
            "source_title": document.metadata.get("source_title") or document.metadata.get("title", UNKNOWN),
            "page": document.metadata.get("page", UNKNOWN),
            "scope": document.metadata.get("scope") or document.metadata.get("applicability") or document.metadata.get("population"),
            "evidence_level": document.metadata.get("evidence_level", UNKNOWN),
            "status": status,
            "reason": reason,
        })
    return assessments


def detect_calculation_inconsistencies(answer: str) -> list[dict[str, Any]]:
    """Detect common unit/definition mistakes without re-solving arbitrary math."""
    issues: list[dict[str, Any]] = []
    text = answer or ""
    if (
        re.search(r"H\s*/\s*D", text, re.I)
        and "有效液高" in text
        and "总高度" in text
        and "内径" in text
        and "外径" in text
        and not re.search(r"两个口径|分别定义|不同口径|不得混用", text)
    ):
        issues.append({
            "status": "calculation_inconsistent",
            "reason": "同一回答同时使用有效液高/内径和总高度/外径，但没有明确两个 H/D 口径",
        })
    if (
        re.search(r"有效容量|额定容量", text)
        and re.search(r"容积占比|利用率", text)
        and "几何容量" not in text
        and not re.search(r"除以|折算|换算", text)
    ):
        issues.append({
            "status": "calculation_inconsistent",
            "reason": "同时出现有效容量和容积占比，但没有说明几何容量与有效容量的换算",
        })
    return issues


def detect_claim_warnings(
    claim: dict[str, Any],
    documents: list[Document],
    source_assessments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect claims that need more than citation-number validation."""
    text = claim.get("text", "")
    lowered = text.lower()
    cited_documents = [
        document
        for citation in claim.get("citations", [])
        if (document := _document_for_citation(citation, documents)) is not None
    ]
    source_text = " ".join(_source_text(document) for document in cited_documents)
    warnings: list[dict[str, Any]] = []

    if any(item.get("status") == "missing" for item in source_assessments):
        warnings.append({
            "status": "citation_invalid",
            "reason": "引用编号不存在，或无法映射到真实候选资料",
        })
    if any(item.get("status") == "indirect" for item in source_assessments):
        warnings.append({
            "status": "indirect_evidence",
            "reason": "引用资料只能作为背景或间接参考，不能单独支持该主张",
        })
    if any(
        document.metadata.get("source_type") == "web"
        for document in cited_documents
    ):
        warnings.append({
            "status": "web_evidence_unverified",
            "reason": "引用了网络搜索结果；搜索摘要未核验原始文件、完整条款或适用范围",
        })
    if (
        re.search(r"P\s*(?:5|50|95)|P5|P50|P95|覆盖[^。；]{0,20}百分位", text, re.I)
        and not re.search(r"P\s*(?:5|50|95)|P5|P50|P95|百分位", source_text, re.I)
    ):
        warnings.append({
            "status": "indirect_evidence",
            "reason": "主张使用了百分位或覆盖范围，但被引用资料未显示对应百分位数据",
        })
    if (
        _contains_terms(text, ("手宽", "手长"))
        and _contains_terms(text, ("杯径", "外径", "直径"))
        and not _contains_terms(text, ("不能直接", "不能仅", "无法直接", "不得直接"))
    ):
        warnings.append({
            "status": "indirect_evidence",
            "reason": "手部尺寸不是圆柱体握持尺寸，不能直接推出杯径",
        })
    material_names = ("Tritan", "PC", "PP", "PPSU", "304", "316")
    if (
        any(re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", text, re.I) for name in material_names)
        and re.search(r"推荐|建议|选择|耐热|抗冲击|适合|更好", text)
        and cited_documents
        and not any(
            document.metadata.get("material_grade", UNKNOWN) != UNKNOWN
            for document in cited_documents
        )
    ):
        warnings.append({
            "status": "indirect_evidence",
            "reason": "材料类别结论缺少具体牌号、供应商数据或适用条件",
        })
    if (
        claim.get("type") == "design_inference"
        and re.search(r"推荐|建议|优先|更适合|选择|应当|应该", text)
        and not re.search(r"在[^。；]{1,80}(?:条件|场景|要求)|如果|前提|需验证|风险|限制|适用", text)
    ):
        warnings.append({
            "status": "recommendation_unconditional",
            "reason": "推荐结论缺少适用条件、主要风险或验证要求",
        })
    if any(item.get("status") == "mismatch" for item in source_assessments):
        warnings.append({
            "status": "scope_mismatch",
            "reason": "引用资料的适用人群、材料或使用范围与主张不匹配",
        })
    return warnings


def _contains_terms(text: str, terms: tuple[str, ...]) -> bool:
    return any(term.lower() in (text or "").lower() for term in terms)


def detect_claim_conflicts(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect explicit numeric disagreement or contradiction language."""
    conflicts = []
    numeric_claims = [claim for claim in claims if claim.get("numbers")]
    for index, left in enumerate(numeric_claims):
        for right in numeric_claims[index + 1:]:
            left_words = set(re.findall(r"[\u4e00-\u9fffA-Za-z]{2,}", left["text"]))
            right_words = set(re.findall(r"[\u4e00-\u9fffA-Za-z]{2,}", right["text"]))
            overlap = left_words & right_words
            if overlap and set(left["numbers"]) != set(right["numbers"]):
                conflicts.append({
                    "claims": [left["id"], right["id"]],
                    "shared_terms": sorted(overlap),
                    "status": "potential_conflict",
                    "reason": "相同主题出现不同数值，需核对材料等级、测试条件或适用范围",
                })
    if any(marker in claim.get("text", "") for claim in claims for marker in _CONFLICT_MARKERS):
        conflicts.append({"status": "explicit_conflict_language", "reason": "回答包含需要进一步核验的对比或冲突表述"})
    return conflicts


def detect_source_conflicts(documents: list[Document]) -> list[dict[str, Any]]:
    """Find potential disagreements between source chunks without picking a winner."""
    entries: list[tuple[int, str, set[str], set[str]]] = []
    for index, document in enumerate(documents):
        content = document.page_content or ""
        words = set(re.findall(r"[\u4e00-\u9fffA-Za-z]{2,}", content))
        numbers = set(_NUMBER_RE.findall(content))
        if words and numbers:
            entries.append((index, str(document.metadata.get("source", "")), words, numbers))
    conflicts = []
    for left_index, left_source, left_words, left_numbers in entries:
        for right_index, right_source, right_words, right_numbers in entries:
            if left_index >= right_index or left_source == right_source or not (left_words & right_words):
                continue
            if left_numbers != right_numbers:
                conflicts.append({
                    "sources": [left_source, right_source],
                    "shared_terms": sorted(left_words & right_words)[:12],
                    "values": [sorted(left_numbers), sorted(right_numbers)],
                    "status": "potential_conflict",
                    "reason": "来源对相同主题给出了不同数值，需核对版本、材料等级或测试条件",
                })
    return conflicts


def audit_claims(answer: str, documents: list[Document], problem_spec: dict[str, Any] | None = None) -> dict[str, Any]:
    claims = extract_claims(answer)
    assessments = []
    unsupported: list[str] = []
    warnings: list[dict[str, Any]] = []
    for claim in claims:
        source_assessments = assess_source_applicability(claim, documents)
        claim["source_assessments"] = source_assessments
        claim_warnings = detect_claim_warnings(claim, documents, source_assessments)
        claim["warnings"] = claim_warnings
        warnings.extend(
            {**warning, "claim_id": claim.get("id"), "claim": claim.get("text", "")}
            for warning in claim_warnings
        )
        if claim.get("claim_role") == "refusal":
            claim["evidence_status"] = "supported"
            claim["confidence"] = 0.8
        elif any(item.get("status") == "citation_invalid" for item in claim_warnings):
            claim["evidence_status"] = "citation_invalid"
            unsupported.append(claim["text"])
        elif any(item.get("status") == "scope_mismatch" for item in claim_warnings):
            claim["evidence_status"] = "scope_mismatch"
            unsupported.append(claim["text"])
        elif any(item.get("status") == "calculation_inconsistent" for item in claim_warnings):
            claim["evidence_status"] = "calculation_inconsistent"
            unsupported.append(claim["text"])
        elif any(item.get("status") == "recommendation_unconditional" for item in claim_warnings):
            claim["evidence_status"] = "recommendation_unconditional"
            unsupported.append(claim["text"])
        elif any(item.get("status") == "indirect_evidence" for item in claim_warnings):
            claim["evidence_status"] = "indirect_evidence"
            unsupported.append(claim["text"])
        elif claim["type"] in {"direct_fact", "derived_result", "compliance_claim"} and not source_assessments:
            claim["evidence_status"] = "unsupported"
            unsupported.append(claim["text"])
        elif any(item["status"] == "mismatch" for item in source_assessments):
            claim["evidence_status"] = "scope_mismatch"
            unsupported.append(claim["text"])
        elif any(item["status"] == "indirect" for item in source_assessments):
            claim["evidence_status"] = "indirect_evidence"
            unsupported.append(claim["text"])
        elif source_assessments:
            claim["evidence_status"] = "supported"
            claim["confidence"] = 0.9 if claim["type"] == "direct_fact" else 0.7
        else:
            claim["evidence_status"] = "unreferenced"
            if claim["type"] == "unsupported" or (
                claim.get("numbers")
                and claim["type"] not in {"assumption"}
            ):
                unsupported.append(claim["text"])
    calculation_warnings = detect_calculation_inconsistencies(answer)
    warnings.extend(calculation_warnings)
    if calculation_warnings:
        for claim in claims:
            if claim.get("numbers") and claim.get("evidence_status") in {
                "supported",
                "unreviewed",
            }:
                claim["evidence_status"] = "calculation_inconsistent"
                unsupported.append(claim["text"])
                break
    recommendation_conditions = build_recommendation_conditions(claims, problem_spec or {})
    return {
        "claims": claims,
        "source_assessments": [assessment for claim in claims for assessment in claim.get("source_assessments", [])],
        "conflicts": detect_claim_conflicts(claims) + detect_source_conflicts(documents),
        "unsupported_claims": unsupported,
        "warnings": warnings,
        "severe_issues": [
            warning
            for warning in warnings
            if warning.get("status")
            in {
                "citation_invalid",
                "scope_mismatch",
                "calculation_inconsistent",
                "indirect_evidence",
                "web_evidence_unverified",
                "recommendation_unconditional",
            }
        ],
        "recommendation_conditions": recommendation_conditions,
        "claim_support_rate": round(
            sum(claim.get("evidence_status") == "supported" for claim in claims) / len(claims), 3
        ) if claims else 1.0,
    }


def build_recommendation_conditions(claims: list[dict[str, Any]], problem_spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn recommendation claims into auditable, conditional decisions."""
    conditions = list(problem_spec.get("constraints", [])) + list(problem_spec.get("criteria", []))
    assumptions = list(problem_spec.get("assumptions", []))
    result = []
    for claim in claims:
        if claim.get("type") not in {"design_inference", "derived_result"}:
            continue
        text = claim.get("text", "")
        if not re.search(r"推荐|建议|优先|更适合|选择|应当|应该", text):
            continue
        result.append({
            "claim_id": claim.get("id"),
            "recommendation": text,
            "conditions": conditions or ["以当前问题描述和已检索资料为前提"],
            "assumptions": assumptions,
            "risks": ["具体牌号、测试条件或适用范围可能与当前资料不同，需核验"],
            "verification": ["核对官方原文、适用范围和必要的验证测试"],
            "evidence_ids": list(claim.get("citations", [])),
            "evidence_status": claim.get("evidence_status", "unreviewed"),
            "reversible": True,
        })
    return result
