from src.calculation import DerivationRecord, calculate_cylinder_capacity, validate_derivation


def test_derivation_record_requires_auditable_fields():
    record = DerivationRecord(
        inputs=[{"name": "x", "value": 1, "unit": "mm", "source": "L1"}],
        assumptions=["这是设计假设，不是标准规定"],
        formula="f(x)",
        result={"y": 2},
        applicability=["仅适用于当前结构边界"],
        verification_items=["样机验证"],
    )
    assert validate_derivation(record) == []


def test_incomplete_derivation_is_rejected():
    errors = validate_derivation({"inputs": [{"name": "x"}]})
    assert "输入参数 1 缺少 unit" in errors
    assert "缺少设计假设" in errors
    assert "缺少待验证项目" in errors


def test_cylinder_capacity_is_auditable_and_close_to_398_ml():
    record = calculate_cylinder_capacity(65, 120)
    assert 398.0 < record.result["ideal_capacity_ml"] < 398.5
    assert "π" in record.formula
    assert validate_derivation(record) == []
