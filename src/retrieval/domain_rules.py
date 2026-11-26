"""Pluggable domain-specific entity rules, BM25 lanes, and ranking priors.

All corpus-specific heuristics (standards, material grades, cup/anthropometry
clues, process-guide term aliases, and low-authority index filters) live in
this isolated module so the core retrieval pipeline stays domain-agnostic.

Set ``RETRIEVER_DOMAIN_RULES_ENABLED=false`` to disable these heuristics and
run pure generic hybrid retrieval on new corpora.
"""

from __future__ import annotations

import re

from langchain_core.documents import Document

from src.query_expansion import expand_query
from src.retrieval._compat import active_settings

# Canonical entities are intentionally small and deterministic. They cover
# identifiers that dense retrieval and a generic cross-encoder commonly miss.
_ENTITY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("gb/t 26158-2010", r"(?<![a-z0-9])gb\s*[/_]?\s*t\s*26158(?:[-—_ ]?2010)?(?![a-z0-9])"),
    ("gb 4806.7-2023", r"(?<![a-z0-9])gb[_ ]?4806[.]?7(?:[-—_ ]?2023)?(?![a-z0-9])"),
    ("gb/t 10000-2023", r"(?<![a-z0-9])gb\s*[/_]?\s*t\s*10000(?:[-—_ ]?2023)?(?![a-z0-9])"),
    ("gb/t 16252-2023", r"(?<![a-z0-9])gb\s*[/_]?\s*t\s*16252(?:[-—_ ]?2023)?(?![a-z0-9])"),
    ("gb/t 3326-2016", r"(?<![a-z0-9])gb\s*[/_]?\s*t\s*3326(?:[-—_ ]?2016)?(?![a-z0-9])"),
    ("pp 3486-01", r"(?<![a-z0-9])(?:pp[_ ]*)?3486[-_ ]?01(?![a-z0-9])"),
    ("tritan tx1001", r"(?<![a-z0-9])(?:tritan[_ ]*)?tx[-_ ]?1001(?![a-z0-9])"),
    ("tritan", r"(?<![a-z0-9])tritan(?![a-z0-9])"),
    ("pp", r"(?<![a-z0-9])pp(?![a-z0-9])"),
    ("pc", r"(?<![a-z0-9])pc(?![a-z0-9])"),
    ("ppsu", r"(?<![a-z0-9])ppsu(?![a-z0-9])"),
    ("tds", r"(?<![a-z0-9])tds(?![a-z0-9])|technical\s+data\s+sheet"),
    ("eastman", r"(?<![a-z0-9])eastman(?![a-z0-9])"),
    ("lyondellbasell", r"(?<![a-z0-9])lyondellbasell(?![a-z0-9])"),
)

_STRICT_ENTITY_NAMES = {
    "gb/t 26158-2010", "gb 4806.7-2023", "gb/t 10000-2023",
    "gb/t 16252-2023", "gb/t 3326-2016", "pp 3486-01",
    "tritan tx1001", "tds", "eastman", "lyondellbasell",
}

_FINAL_ENTITY_NAMES = {
    "gb/t 26158-2010", "gb 4806.7-2023", "gb/t 10000-2023",
    "gb/t 16252-2023", "gb/t 3326-2016", "pp 3486-01",
    "tritan tx1001",
}

_LOW_AUTHORITY_SOURCE_MARKERS = (
    "资料来源清单.md",
    "知识库目录说明.md",
    "readme.md",
    "index.md",
    "catalog.md",
    "source_list.md",
)

_BM25_ENTITY_ALIASES = {
    "gb/t 26158-2010": "GB/T 26158-2010",
    "gb 4806.7-2023": "GB 4806.7-2023",
    "gb/t 10000-2023": "GB/T 10000-2023",
    "gb/t 16252-2023": "GB/T 16252-2023",
    "gb/t 3326-2016": "GB/T 3326-2016",
    "pp 3486-01": "PP 3486-01 TDS",
    "tritan tx1001": "Tritan TX1001 TDS",
}

_MATERIAL_TDS_MAPPINGS = {
    "pp": ("pp 3486-01", "LyondellBasell PP 3486-01 TDS", "pp_3486_01"),
    "tritan": ("tritan tx1001", "Eastman Tritan TX1001 TDS", "tritan_tx1001"),
}

_PROCESS_TERM_ALIASES = (
    ("干燥", "drying"),
    ("挤出", "extrusion"),
    ("注塑", "injection molding"),
    ("成型", "molding"),
    ("浇口", "gate"),
    ("壁厚", "wall thickness"),
    ("排气", "venting"),
    ("圆角", "corner radius"),
    ("残余应力", "residual stress"),
)

_PROCESS_PRIOR_MATERIALS = ("pp", "pc", "tritan", "ppsu")


def domain_rules_enabled() -> bool:
    return bool(getattr(active_settings(), "retriever_domain_rules_enabled", True))


def _compact_entity_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _compact_query_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _normalise_bm25_lane(item: dict[str, object] | str, index: int = 0) -> dict[str, object]:
    if isinstance(item, dict):
        query = str(item.get("query", "")).strip()
        entity = item.get("entity")
        canonical_id = item.get("canonical_id") or (
            _canonical_entity_id(str(entity)) if entity else None
        )
        origin = str(item.get("origin") or ("explicit" if index == 0 else "expanded"))
        kind = str(item.get("kind") or ("original" if index == 0 else "entity"))
        guardable = bool(item.get("guardable", False))
        return {
            "query": query,
            "kind": kind,
            "entity": entity,
            "canonical_id": canonical_id,
            "origin": origin,
            "rule_id": item.get("rule_id"),
            "guardable": guardable,
        }
    return {
        "query": str(item).strip(),
        "kind": "original" if index == 0 else "entity",
        "entity": None,
        "canonical_id": None,
        "origin": "explicit" if index == 0 else "expanded",
        "rule_id": None,
        "guardable": False,
    }


def _make_bm25_lane(
    query: str,
    *,
    kind: str,
    entity: str | None,
    origin: str,
    rule_id: str | None,
    guardable: bool,
    canonical_id: str | None = None,
) -> dict[str, object]:
    canonical_id = canonical_id or (_canonical_entity_id(entity) if entity else None)
    return {
        "query": query,
        "kind": kind,
        "entity": entity,
        "canonical_id": canonical_id,
        "origin": origin,
        "rule_id": rule_id,
        "guardable": bool(guardable and canonical_id),
    }


def _canonical_entity_id(entity: str) -> str | None:
    """Return a stable ID for standards and material grades."""
    compact = _compact_entity_text(entity)
    if compact in {"gb480672023", "gb48067"}:
        return "gb_4806_7_2023"
    standard = re.search(r"(?:gbt|gb)(\d{4,6})(\d{4})$", compact)
    if standard:
        return f"gb_{standard.group(1)}_{standard.group(2)}"
    reverse_standard = re.search(r"(\d{4,6})(20\d{2})gbt$", compact)
    if reverse_standard:
        return f"gb_{reverse_standard.group(1)}_{reverse_standard.group(2)}"
    pp = re.search(r"(?:pp)?(\d{3,5})0?1$", compact)
    if compact in {"pp348601", "348601"} or pp and pp.group(1) == "3486":
        return "pp_3486_01"
    if compact in {"tritanx1001", "tx1001"}:
        return "tritan_tx1001"
    return None


def _canonical_entity_ids_from_text(value: str) -> set[str]:
    text = str(value or "").casefold()
    result: set[str] = set()
    patterns = (
        r"gb\s*[/_]?\s*t\s*(\d{4,6})\s*[-—_ ]?\s*(20\d{2})",
        r"gb\s*[_ ]?\s*(4806)[._]?7\s*[-—_ ]?\s*(20\d{2})?",
        r"(\d{4,6})\s*[-_ ]\s*(20\d{2})\s*[-_ ]?\s*gbt",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            groups = match.groups()
            if groups[0] == "4806":
                result.add(f"gb_4806_7_{groups[1] or '2023'}")
            else:
                result.add(f"gb_{groups[0]}_{groups[1]}")
    if re.search(r"(?:pp[ _-]*)?3486[ _-]*01", text, re.I):
        result.add("pp_3486_01")
    if re.search(r"(?:tritan[ _-]*)?tx[ _-]*1001", text, re.I):
        result.add("tritan_tx1001")
    return result


def _extract_query_entities(question: str) -> list[str]:
    """Extract standards, grades, materials and domain roles deterministically."""
    text = str(question or "")
    lowered = text.casefold()
    found = [canonical for canonical, pattern in _ENTITY_PATTERNS if re.search(pattern, lowered, re.I)]
    if any(token in text for token in ("食品接触", "食品安全", "合规")):
        found.append("food_contact")
    if any(token in text for token in ("注塑", "加工", "干燥", "浇口", "排气")):
        found.append("process_guide")
    if any(token in text for token in ("儿童", "幼儿", "未成年人")):
        found.append("anthropometry_minors")
    return list(dict.fromkeys(found))


def build_bm25_queries(question: str) -> list[dict[str, object]]:
    """Build a bounded, structured BM25 query plan."""
    text = str(question or "").strip()
    if not text:
        return []
    original = _make_bm25_lane(
        text,
        kind="original",
        entity=None,
        origin="explicit",
        rule_id=None,
        guardable=False,
    )
    if not domain_rules_enabled():
        return [original]
    settings = active_settings()
    mode = str(getattr(settings, "retriever_bm25_mode", "conditional")).lower()
    legacy_independent = bool(getattr(settings, "retriever_independent_bm25_queries", False))
    if mode == "original_only":
        return [original]
    if mode == "independent_all" or legacy_independent:
        lanes = [original]
        for query in expand_query(text) or [text]:
            if _compact_query_text(query) == _compact_query_text(text):
                continue
            lanes.append(_make_bm25_lane(
                query,
                kind="entity",
                entity=None,
                origin="expanded",
                rule_id="full_independent_bm25",
                guardable=False,
            ))
        return lanes

    explicit_lanes: list[dict[str, object]] = []
    deterministic_standard_lanes: list[dict[str, object]] = []
    material_lanes: list[dict[str, object]] = []
    process_lanes: list[dict[str, object]] = []

    explicit_entities = _extract_query_entities(text)
    for entity in explicit_entities:
        if entity not in _BM25_ENTITY_ALIASES or not _canonical_entity_id(entity):
            continue
        explicit_lanes.append(_make_bm25_lane(
            _BM25_ENTITY_ALIASES[entity],
            kind="entity",
            entity=entity,
            origin="explicit",
            rule_id="explicit_identifier",
            guardable=True,
        ))

    child_scope = (
        any(token in text for token in ("儿童", "孩子", "幼儿", "未成年人", "未成年"))
        or bool(re.search(r"\d+\s*(?:[-—~至到]\s*\d+\s*)?岁", text))
    )
    child_dimension = any(
        token in text for token in ("手部", "手掌", "手指", "握", "直径", "尺寸")
    )
    if child_scope and child_dimension:
        deterministic_standard_lanes.append(_make_bm25_lane(
            "GB/T 26158-2010",
            kind="entity",
            entity="gb/t 26158-2010",
            origin="deterministic",
            rule_id="child_hand_dimensions",
            guardable=True,
        ))

    cup_scope = any(
        token in text for token in ("水杯", "饮用容器", "杯体", "食品容器", "可重复使用")
    )
    compliance_scope = any(
        token in text for token in ("材料", "材质", "筛选", "选择", "安全", "合规", "食品接触")
    )
    if cup_scope and compliance_scope:
        deterministic_standard_lanes.append(_make_bm25_lane(
            "GB 4806.7-2023",
            kind="entity",
            entity="gb 4806.7-2023",
            origin="deterministic",
            rule_id="cup_food_contact_compliance",
            guardable=True,
        ))

    materials: list[str] = []
    if re.search(r"(?<![A-Za-z0-9])PP(?![A-Za-z0-9])", text, re.I):
        materials.append("pp")
    if re.search(r"(?<![A-Za-z0-9])PC(?![A-Za-z0-9])", text, re.I):
        materials.append("pc")
    if re.search(r"(?<![A-Za-z0-9])Tritan(?![A-Za-z0-9])", text, re.I):
        materials.append("tritan")
    if re.search(r"(?<![A-Za-z0-9])PPSU(?![A-Za-z0-9])", text, re.I):
        materials.append("ppsu")
    if "玻璃" in text:
        materials.append("glass")
    decision_query = len(set(materials)) >= 2 and any(
        token in text for token in ("比较", "对比", "选择", "筛选", "哪个", "决定", "决策")
    )
    if decision_query:
        for material in list(dict.fromkeys(materials)):
            mapping = _MATERIAL_TDS_MAPPINGS.get(material)
            if not mapping:
                continue
            entity, query, mapped_canonical_id = mapping
            material_lanes.append(_make_bm25_lane(
                query,
                kind="entity",
                entity=entity,
                origin="deterministic",
                rule_id=f"material_comparison_{material}_tds",
                guardable=True,
                canonical_id=mapped_canonical_id,
            ))

    process_terms = [english for chinese, english in _PROCESS_TERM_ALIASES if chinese in text]
    process_materials = [item for item in materials if item in {"pp", "pc", "tritan", "ppsu"}]
    if process_materials and process_terms:
        material_text = " ".join(dict.fromkeys(process_materials))
        process_query = f"{material_text} {' '.join(dict.fromkeys(process_terms))} processing guide"
        process_lanes.append(_make_bm25_lane(
            process_query,
            kind="process",
            entity="process_guide",
            origin="deterministic",
            rule_id="material_process_guide",
            guardable=False,
        ))

    candidates = explicit_lanes + deterministic_standard_lanes + material_lanes + process_lanes
    limit = max(0, int(getattr(settings, "retriever_max_entity_bm25_queries", 3)))
    lanes = [original]
    seen_ids: set[str] = set()
    seen_queries = {_compact_query_text(text)}
    candidates.sort(key=lambda lane: (
        0 if lane.get("origin") == "explicit" else
        1 if lane.get("canonical_id") and lane.get("kind") == "entity" else
        2 if lane.get("kind") == "entity" else 3,
    ))
    for lane in candidates:
        canonical_id = str(lane.get("canonical_id") or "")
        query_key = _compact_query_text(str(lane["query"]))
        if query_key in seen_queries or canonical_id and canonical_id in seen_ids:
            continue
        lanes.append(lane)
        seen_queries.add(query_key)
        if canonical_id:
            seen_ids.add(canonical_id)
        if len(lanes) >= limit + 1:
            break
    return lanes


def _candidate_entity_text(document: Document) -> str:
    metadata = document.metadata
    return " ".join(
        str(metadata.get(key, ""))
        for key in ("source", "source_title", "title", "section_path", "parent_document", "material_grade")
    ) + " " + str(document.page_content or "")


def _entity_match_score(query_entities: list[str], document: Document) -> tuple[float, list[str]]:
    if not query_entities:
        return 0.0, []
    metadata = document.metadata
    fields = {
        "source": str(metadata.get("source", "")).casefold(),
        "title": str(metadata.get("title") or metadata.get("source_title", "")).casefold(),
        "parent_document": str(metadata.get("parent_document", "")).casefold(),
        "section_path": str(metadata.get("section_path", "")).casefold(),
        "content": str(document.page_content or "").casefold(),
    }
    text = " ".join(fields.values())
    compact = _compact_entity_text(text)
    matched: list[str] = []
    scores: list[float] = []
    patterns = dict(_ENTITY_PATTERNS)
    for entity in query_entities:
        if entity == "food_contact":
            hit = any(token in text for token in ("食品接触", "食品安全", "food contact", "gb 4806.7", "gb4806.7"))
            location = "content" if hit else None
        elif entity == "process_guide":
            hit = any(token in text for token in ("processing", "mold", "mould", "drying", "注塑", "加工", "工艺"))
            location = "content" if hit else None
        elif entity == "anthropometry_minors":
            hit = any(token in text for token in ("儿童", "未成年人", "children", "anthropometry", "26158"))
            location = "content" if hit else None
        else:
            pattern = patterns.get(entity)
            location = next(
                (
                    name
                    for name in ("source", "title", "parent_document", "section_path", "content")
                    if pattern and re.search(pattern, fields[name], re.I)
                ),
                None,
            )
            hit = location is not None
            if not hit and entity.startswith("gb/"):
                number = re.search(r"(\d{4,})", entity)
                year = re.search(r"(20\d{2})", entity)
                hit = bool(number and year and number.group(1) + year.group(1) in compact)
                location = "source" if hit else None
            if not hit and _compact_entity_text(entity) in compact:
                hit, location = True, "content"
        if hit:
            matched.append(entity)
            if location in {"source", "title"}:
                scores.append(1.0)
            elif location in {"parent_document", "section_path"}:
                scores.append(0.8)
            elif entity in _STRICT_ENTITY_NAMES and entity not in {"tds", "eastman", "lyondellbasell"}:
                scores.append(0.6)
            else:
                scores.append(0.3 if entity not in _STRICT_ENTITY_NAMES else 0.6)
    if not scores:
        return 0.0, matched
    weighted = max(scores) + 0.15 * max(0, len(scores) - 1)
    return min(1.0, weighted), matched


def _source_role(document: Document) -> str:
    explicit_role = str(document.metadata.get("source_role", "")).casefold()
    priority_enabled = bool(getattr(active_settings(), "retriever_source_role_priority", True))
    valid_roles = {
        "standard", "anthropometry", "material_tds", "process_guide",
        "internal_rule", "paper", "reference_handbook",
    }
    if priority_enabled and explicit_role in valid_roles:
        return explicit_role
    category = str(document.metadata.get("source_category", "")).casefold()
    if priority_enabled and category in valid_roles:
        return category
    metadata = document.metadata
    text = " ".join(
        str(metadata.get(key, ""))
        for key in ("source", "source_title", "title", "parent_document")
    ).casefold()
    if re.search(r"tds|technical[ _-]*data|data[ _-]*sheet|datasheet", text):
        return "material_tds"
    if any(token in text for token in ("processing", "mold", "mould", "drying", "extrusion", "工艺", "注塑")):
        return "process_guide"
    if re.search(r"(?<![a-z0-9])gb(?:[/_ ]?t)?(?![a-z0-9])|(?<![a-z0-9])iso(?![a-z0-9])", text):
        return "standard"
    if any(token in text for token in ("anthropometry", "人体", "手部", "握力")):
        return "anthropometry"
    content = str(document.page_content or "").casefold()
    if re.search(r"tds|technical\s+data\s+sheet", content):
        return "material_tds"
    if any(token in content for token in ("processing guide", "drying guide", "加工指南", "工艺指南")):
        return "process_guide"
    return "unknown"


def _query_roles(entities: list[str]) -> set[str]:
    roles: set[str] = set()
    if any(entity.startswith("gb/") or entity.startswith("gb ") for entity in entities):
        roles.add("standard")
    if any(
        entity in {
            "pp", "pc", "tritan", "ppsu", "pp 3486-01", "tritan tx1001",
            "tds", "eastman", "lyondellbasell", "food_contact",
        }
        for entity in entities
    ):
        roles.add("material_tds")
    if "food_contact" in entities:
        roles.add("standard")
    if "process_guide" in entities:
        roles.add("process_guide")
    if "anthropometry_minors" in entities:
        roles.add("anthropometry")
    return roles


def _entity_lane_rank(document: Document, canonical_id: str) -> int | None:
    ranks = [
        int(hit.get("rank") or 10**9)
        for hit in document.metadata.get("bm25_query_hits", [])
        if hit.get("canonical_id") == canonical_id
    ]
    return min(ranks) if ranks else None


def _is_low_authority_document(document: Document) -> bool:
    """Return whether metadata identifies an index/catalog document."""
    fields = (
        document.metadata.get("source"),
        document.metadata.get("parent_document"),
        document.metadata.get("source_title"),
        document.metadata.get("title"),
    )
    markers = {marker.casefold().replace("\\", "/") for marker in _LOW_AUTHORITY_SOURCE_MARKERS}
    for value in fields:
        normalized = str(value or "").strip().replace("\\", "/").casefold()
        if not normalized:
            continue
        basename = normalized.rsplit("/", 1)[-1]
        if basename in markers:
            return True
    return False


def _entity_match_locations(document: Document, canonical_id: str) -> set[str]:
    """Return metadata/content fields that contain a canonical entity."""
    metadata = document.metadata
    fields = {
        "source": str(metadata.get("source", "")),
        "title": str(metadata.get("title") or metadata.get("source_title", "")),
        "parent_document": str(metadata.get("parent_document", "")),
        "section_path": str(metadata.get("section_path", "")),
        "content": str(document.page_content or ""),
    }
    return {
        field
        for field, value in fields.items()
        if canonical_id in _canonical_entity_ids_from_text(value)
    }


def _entity_role_compatible(canonical_id: str, document: Document) -> bool:
    role = _source_role(document)
    if canonical_id.startswith("gb_"):
        allowed = {
            "gb_26158_2010": {"standard", "anthropometry"},
            "gb_4806_7_2023": {"standard"},
        }.get(canonical_id, {"standard"})
        fallback = {"reference_handbook", "process_guide"}
    else:
        allowed = {"material_tds"}
        fallback = {"standard", "process_guide", "reference_handbook"}
    return role in allowed or role in fallback


def _entity_candidate_priority(document: Document, canonical_id: str) -> int:
    """Score authority of a canonical entity match; lower-authority is -1."""
    if _is_low_authority_document(document):
        return -1
    locations = _entity_match_locations(document, canonical_id)
    if not locations or not _entity_role_compatible(canonical_id, document):
        return -1
    role = _source_role(document)
    source_or_title = bool(locations & {"source", "title"})
    if canonical_id.startswith("gb_"):
        if role == "standard":
            return 100 if "source" in locations else 95 if source_or_title else 90
        if canonical_id == "gb_26158_2010" and role == "anthropometry":
            return 88 if "source" in locations else 84 if source_or_title else 80
        return 70 if source_or_title else 60
    if role == "material_tds":
        return 100 if source_or_title else 90
    return 70 if source_or_title else 60


def _build_entity_plan(
    question: str, bm25_plan: list[dict[str, object]]
) -> list[dict[str, object]]:
    """Combine explicit, deterministic and weak expanded entities for ranking."""
    if not domain_rules_enabled():
        return []
    plan: list[dict[str, object]] = []
    seen_names: set[str] = set()
    seen_ids: set[str] = set()

    lane_by_id = {
        str(item.get("canonical_id")): item
        for item in bm25_plan
        if item.get("canonical_id")
    }
    for entity in _extract_query_entities(question):
        canonical_id = _canonical_entity_id(entity)
        lane = lane_by_id.get(str(canonical_id)) if canonical_id else None
        plan.append({
            "entity": entity,
            "canonical_id": canonical_id,
            "origin": "explicit",
            "rule_id": "explicit_query",
            "guardable": bool(lane and lane.get("origin") == "explicit" and lane.get("guardable")),
        })
        seen_names.add(entity)
        if canonical_id:
            seen_ids.add(canonical_id)

    for lane in bm25_plan:
        entity = str(lane.get("entity") or "")
        if not entity or lane.get("origin") != "deterministic":
            continue
        canonical_id = str(lane.get("canonical_id") or "") or None
        if entity in seen_names or canonical_id and canonical_id in seen_ids:
            continue
        plan.append({
            "entity": entity,
            "canonical_id": canonical_id,
            "origin": "deterministic",
            "rule_id": lane.get("rule_id"),
            "guardable": bool(lane.get("guardable") and canonical_id),
        })
        seen_names.add(entity)
        if canonical_id:
            seen_ids.add(canonical_id)

    expanded_text = " || ".join(expand_query(question) or [])
    for entity in _extract_query_entities(expanded_text):
        canonical_id = _canonical_entity_id(entity)
        if entity in seen_names or canonical_id and canonical_id in seen_ids:
            continue
        plan.append({
            "entity": entity,
            "canonical_id": canonical_id,
            "origin": "expanded",
            "rule_id": "query_expansion",
            "guardable": False,
        })
        seen_names.add(entity)
        if canonical_id:
            seen_ids.add(canonical_id)
    return plan


def _process_prior_signal(
    document: Document,
    bm25_plan: list[dict[str, object]],
    base_rank: int,
    rerank_top_k: int,
) -> tuple[bool, int, str | None]:
    """Return a bounded near-cutoff prior for exact process-guide evidence."""
    if not domain_rules_enabled():
        return False, 0, "domain_rules_disabled"
    if not bool(getattr(active_settings(), "retriever_process_guide_prior", True)):
        return False, 0, "disabled"
    if base_rank > rerank_top_k + 2:
        return False, 0, "outside_near_cutoff"
    process_lanes = [
        lane for lane in bm25_plan
        if lane.get("kind") == "process" and lane.get("origin") == "deterministic"
    ]
    if not process_lanes or _source_role(document) != "process_guide":
        return False, 0, "no_deterministic_process_lane_or_role_mismatch"

    metadata_text = " ".join(
        str(document.metadata.get(key, ""))
        for key in ("source", "source_title", "title", "parent_document")
    ).casefold()
    if not any(
        re.search(rf"(?<![a-z0-9]){re.escape(material)}(?![a-z0-9])", metadata_text, re.I)
        for material in _PROCESS_PRIOR_MATERIALS
    ):
        return False, 0, "material_not_in_metadata"

    lane_text = " ".join(str(lane.get("query", "")) for lane in process_lanes).casefold()
    high_signal_terms = [
        english.casefold()
        for _, english in _PROCESS_TERM_ALIASES
        if english.casefold() in lane_text
    ]
    matched_terms = [
        term
        for term in dict.fromkeys(
            english.casefold() for _, english in _PROCESS_TERM_ALIASES
        )
        if term in metadata_text
    ]
    overlapping_terms = [term for term in matched_terms if term in high_signal_terms]
    if len(matched_terms) < 2 or not overlapping_terms:
        return False, len(overlapping_terms), "fewer_than_two_metadata_process_terms"
    return True, len(matched_terms), "deterministic_process_lane_exact_metadata_match"


def _anthropometry_prior_signal(
    question: str,
    document: Document,
    base_rank: int,
    rerank_top_k: int,
) -> tuple[bool, str]:
    """Identify a narrow evidence-priority case for under-specified child sizes."""
    if not domain_rules_enabled():
        return False, "domain_rules_disabled"
    if not bool(getattr(active_settings(), "retriever_anthropometry_prior", True)):
        return False, "disabled"
    if base_rank > rerank_top_k + 15:
        return False, "outside_bounded_window"
    text = str(question or "")
    child_scope = any(token in text for token in ("儿童", "孩子", "幼儿", "未成年人"))
    exact_request = any(token in text for token in ("最佳", "精确", "准确", "确定值")) and any(
        token in text for token in ("握持直径", "握持", "手部尺寸", "手部", "直径")
    )
    evidence_gap = any(token in text for token in ("只有", "缺少", "没有", "无法给出", "知识库"))
    if not (child_scope and exact_request and evidence_gap):
        return False, "query_intent_not_matched"
    if _source_role(document) != "anthropometry":
        return False, "source_role_mismatch"
    metadata_text = " ".join(
        str(document.metadata.get(key, ""))
        for key in ("source", "source_title", "title", "parent_document")
    ).casefold()
    child_evidence = any(token in metadata_text for token in (
        "儿童", "children", "child", "/children/", "\\children\\",
    ))
    if not child_evidence:
        return False, "no_child_anthropometry_evidence"
    return True, "exact_value_request_with_corpus_evidence_gap"
