"""Lightweight bilingual engineering glossary for cross-lingual BM25 matching.

In a bilingual knowledge base (Chinese queries over English manufacturer DFM
guides and datasheets), pure BM25 cannot match Chinese engineering terms such
as "折弯", "壁厚", "脱模斜度", "圆角", "阳极氧化", or material abbreviations
("PC" -> "polycarbonate") against English passages.

This module provides a small, general-purpose mechanical & industrial-design
term bridge used *only* to augment BM25 query terms. It never mutates document
content or hardcodes corpus filenames/standards.
"""

from __future__ import annotations

import re

# General mechanical / industrial design Chinese <-> English term equivalents.
# Ordered longest-first or checked via substring/word boundaries.
_BILINGUAL_EQUIVALENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Processes
    ("钣金", ("sheet metal", "sheet")),
    ("折弯", ("bend", "bending", "flange")),
    ("法兰", ("flange",)),
    ("注塑", ("injection molding", "molding", "molded")),
    ("脱模斜度", ("draft", "draft angle", "vertical faces")),
    ("脱模", ("draft", "ejection")),
    ("拔模", ("draft",)),
    ("铣削", ("milling", "cnc milling")),
    ("车削", ("turning", "cnc turning")),
    ("压铸", ("die casting", "die cast", "casting")),
    ("包胶", ("overmolding", "lsr")),
    ("阳极氧化", ("anodized", "anodizing", "anodic")),
    ("表面处理", ("surface finish", "finishing", "coating")),
    ("光固化", ("stereolithography", "sla")),
    ("尼龙", ("nylon",)),
    # Geometric / DFM features & units
    ("壁厚", ("wall thickness", "thickness")),
    ("层厚", ("layer thickness",)),
    ("膜厚", ("coating thickness", "thickness", "mil", "microns")),
    ("微米", ("microns", "micron", "mil")),
    ("圆角", ("fillets", "fillet", "radii", "radius")),
    ("尖角", ("sharp corners", "sharp edges", "fillets")),
    ("冷隔", ("cold shuts", "cold shut")),
    ("倒扣", ("undercuts", "undercut")),
    ("加强筋", ("ribs", "rib")),
    ("分型线", ("parting line", "parting lines")),
    ("公差", ("tolerance", "tolerances")),
    ("垂直面", ("vertical faces", "vertical")),
    ("最小", ("minimum", "min")),
    ("最薄", ("minimum", "wall thickness", "thicker")),
    ("最大", ("maximum", "max")),
    ("多大", ("dimensions", "size", "maximum")),
    ("尺寸", ("dimensions", "size")),
    ("距离", ("proximity", "distance", "minimum")),
    ("孔", ("hole", "holes")),
    ("板厚", ("material thickness", "thickness")),
    ("特征", ("feature", "features")),
    # Common materials (full names <-> abbreviations)
    ("不锈钢", ("stainless steel", "stainless")),
    ("铝", ("aluminum", "aluminium")),
    ("聚碳酸酯", ("polycarbonate",)),
    ("聚丙烯", ("polypropylene",)),
    ("聚甲醛", ("acetal", "pom")),
    ("亚克力", ("acrylic", "pmma")),
    ("硅胶", ("silicone", "lsr")),
)

_ASCII_ABBREV_EQUIVALENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pc", ("polycarbonate",)),
    ("pp", ("polypropylene",)),
    ("pa", ("nylon",)),
    ("pom", ("acetal",)),
    ("pmma", ("acrylic",)),
    ("lsr", ("liquid silicone", "silicone")),
)


def expand_bilingual_terms(question: str) -> list[str]:
    """Return English technical equivalents triggered by a Chinese/mixed query."""
    text = str(question or "")
    if not text.strip():
        return []
    lowered = text.lower()
    extras: list[str] = []

    for cn_term, en_equivalents in _BILINGUAL_EQUIVALENTS:
        if cn_term in text:
            extras.extend(en_equivalents)

    for abbr, en_equivalents in _ASCII_ABBREV_EQUIVALENTS:
        if re.search(rf"(?<![a-z0-9]){re.escape(abbr)}(?![a-z0-9])", lowered):
            extras.extend(en_equivalents)

    # Split multi-word phrases into individual tokens too so BM25 matches both
    # whole tokens and bigrams consistently.
    tokens: list[str] = []
    for phrase in extras:
        for word in re.findall(r"[a-z0-9][a-z0-9._/-]*", phrase.lower()):
            if len(word) > 1:
                tokens.append(word)
    return list(dict.fromkeys(tokens))
