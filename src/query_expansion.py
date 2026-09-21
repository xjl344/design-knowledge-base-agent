"""Deterministic retrieval query expansion for recurring design entities."""

from __future__ import annotations

import re


def expand_query(question: str) -> list[str]:
    """Return the original query plus bounded, domain-specific variants.

    Expansion is deliberately rule-based so F/R/P comparisons do not depend
    on an LLM, network state, or a changing prompt.
    """
    text = str(question or "").strip()
    if not text:
        return []
    variants = [text]
    lowered = text.casefold()

    if any(term in text for term in ("儿童", "幼儿", "未成年")) and any(
        term in text for term in ("水杯", "握持", "握力", "手部", "杯径")
    ):
        variants.append("儿童手部尺寸 握力 握持直径 GB/T 26158-2010 儿童人体尺寸")

    if "gb/t 26158" in lowered or "26158" in lowered:
        variants.append("GB/T 26158-2010 未成年人人体尺寸 儿童")

    if any(term in lowered for term in ("pp", "tritan")):
        variants.append(
            "PP LyondellBasell 3486-01 TDS Tritan TX1001 TDS 食品接触 GB 4806.7"
        )

    if "tritan" in lowered:
        variants.append("Eastman Tritan TX1001 Technical Data Sheet")

    if "pp" in lowered:
        variants.append("LyondellBasell PP 3486-01 Technical Data Sheet")

    if any(term in lowered for term in ("食品接触", "食品安全", "材料最安全", "合规")):
        variants.append("GB 4806.7-2023 塑料 食品接触 材料合规")

    if any(term in text for term in ("标准冲突", "适用层级", "证据边界")):
        variants.append("GB/T 26158-2010 儿童手部尺寸 握持 适用范围")

    result: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        key = re.sub(r"\s+", "", variant).casefold()
        if key and key not in seen:
            seen.add(key)
            result.append(variant)
    return result[:4]
