"""Structured records for auditable engineering derivations.

The language model may explain a derivation in prose, but application code can
use this structure when a calculation needs to be stored, reviewed, or tested.
No domain-specific dimensions or default values are embedded here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
import math


@dataclass
class DerivationRecord:
    inputs: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    formula: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    applicability: list[str] = field(default_factory=list)
    verification_items: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_cylinder_capacity(
    inner_diameter_mm: float,
    liquid_height_mm: float,
) -> DerivationRecord:
    """Calculate ideal cylindrical volume with explicit engineering boundaries."""
    diameter = float(inner_diameter_mm)
    height = float(liquid_height_mm)
    if diameter <= 0 or height <= 0:
        raise ValueError("内径和有效液高必须为正数")
    volume_mm3 = math.pi * diameter ** 2 / 4.0 * height
    volume_ml = volume_mm3 / 1000.0
    return DerivationRecord(
        inputs=[
            {"name": "内径", "value": diameter, "unit": "mm", "source": "user_input"},
            {"name": "有效液高", "value": height, "unit": "mm", "source": "user_input"},
        ],
        assumptions=["杯体为直筒圆柱，内径和液高在计算区间内恒定", "忽略杯底圆角、壁厚、杯盖和液面安全余量"],
        formula="V = π × d² / 4 × h；1 mL = 1000 mm³",
        result={"volume_mm3": round(volume_mm3, 3), "ideal_capacity_ml": round(volume_ml, 3)},
        applicability=["仅表示理想几何容量，不等同于额定容量或可用容量"],
        verification_items=["实测内腔尺寸", "确认有效液高和液面余量", "样杯装液实测额定容量"],
    )


def validate_derivation(record: DerivationRecord | dict[str, Any]) -> list[str]:
    """Return missing-field errors without judging the engineering formula."""
    payload = record.as_dict() if isinstance(record, DerivationRecord) else record
    errors: list[str] = []
    inputs = payload.get("inputs", [])
    if not isinstance(inputs, list) or not inputs:
        errors.append("缺少输入参数")
    else:
        for index, item in enumerate(inputs, 1):
            if not isinstance(item, dict):
                errors.append(f"输入参数 {index} 不是对象")
                continue
            for field_name in ("name", "value", "unit", "source"):
                if item.get(field_name) in (None, ""):
                    errors.append(f"输入参数 {index} 缺少 {field_name}")
    if not payload.get("assumptions"):
        errors.append("缺少设计假设")
    if not payload.get("formula"):
        errors.append("缺少计算公式或分析规则")
    if not payload.get("result"):
        errors.append("缺少计算结果")
    if not payload.get("applicability"):
        errors.append("缺少适用边界")
    if not payload.get("verification_items"):
        errors.append("缺少待验证项目")
    return errors
