"""Slice definitions for generation evaluation.

Why slices rather than one flat score
-------------------------------------
Different changes are supposed to move different metrics.  A prompt edit
should move answer quality and leave retrieval untouched; a provider change
should move usability and leave quality untouched.  That judgement needs the
samples to carry labels, because "quality went down" is not actionable while
"quality went down on the ambiguous slice only" is.

The slice file is deliberately separate from the evaluation contract:

* the contract (``generation_eval.v2.json``) states what each question
  *expects* -- spans, sources, required terms, required behaviours;
* the slice file (``generation_eval_slices.v1.json``) states what each
  question *is* -- its type, risk level and tags.

They change for different reasons and at different rates, so coupling them
would force a contract version bump every time a tag is added.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CASE_TYPE_VALUES = frozenset({
    "fact_numeric",      # answer is one or more numeric values
    "fact_enumeration",  # answer is a list of facts / names / topics
    "refusal",           # context cannot support an answer; refusing is correct
    "ambiguous",         # context partially supports; scoping the answer is correct
    # Answer requires facts from more than one document, so a single-chunk
    # answer cannot be complete however well it is written.  Added together
    # with the composite snapshot builder; see scripts/build_multihop_snapshot.py.
    "multi_hop",
})

RISK_LEVEL_VALUES = frozenset({"low", "medium", "high"})

# High-risk slices are gated on their own rather than averaged into a headline
# number, because a single hallucinated answer there matters more than a
# cosmetic wording regression elsewhere.
HIGH_RISK_LEVEL = "high"

REQUIRED_SLICE_FIELDS = ("id", "case_type", "risk_level", "slice_tags", "flaky")


class SliceError(ValueError):
    """Raised when the slice file is missing, malformed or inconsistent."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SliceError(message)


def load_slices(path: str | Path) -> dict[str, Any]:
    """Load and validate a slice definition file.

    Validation is deliberately strict and fails at load time.  A slice file
    that silently loses a question would quietly change every per-slice
    denominator, which is exactly the failure mode this file exists to prevent.
    """
    path = Path(path)
    if not path.exists():
        raise SliceError(f"切片定义不存在：{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))

    declared_types = payload.get("case_type_values")
    _require(isinstance(declared_types, list) and declared_types,
             "切片文件缺少 case_type_values")
    _require(set(declared_types) == CASE_TYPE_VALUES,
             f"case_type_values 与实现不一致：{sorted(set(declared_types) ^ CASE_TYPE_VALUES)}")

    declared_risks = payload.get("risk_level_values")
    _require(isinstance(declared_risks, list) and declared_risks,
             "切片文件缺少 risk_level_values")
    _require(set(declared_risks) == RISK_LEVEL_VALUES,
             f"risk_level_values 与实现不一致：{sorted(set(declared_risks) ^ RISK_LEVEL_VALUES)}")

    slices = payload.get("slices")
    _require(isinstance(slices, list) and slices, "切片文件缺少 slices")

    by_id: dict[str, dict[str, Any]] = {}
    for entry in slices:
        for field in REQUIRED_SLICE_FIELDS:
            _require(field in entry, f"切片 {entry.get('id', '?')} 缺少字段 {field}")
        slice_id = str(entry["id"])
        _require(slice_id not in by_id, f"切片 id 重复：{slice_id}")
        _require(entry["case_type"] in CASE_TYPE_VALUES,
                 f"切片 {slice_id} 的 case_type 非法：{entry['case_type']}")
        _require(entry["risk_level"] in RISK_LEVEL_VALUES,
                 f"切片 {slice_id} 的 risk_level 非法：{entry['risk_level']}")
        _require(isinstance(entry["slice_tags"], list) and entry["slice_tags"],
                 f"切片 {slice_id} 的 slice_tags 必须是非空列表")
        _require(isinstance(entry["flaky"], bool),
                 f"切片 {slice_id} 的 flaky 必须是布尔值")
        by_id[slice_id] = entry

    payload["by_id"] = by_id
    payload["metric_groups"] = payload.get("metric_groups") or {}
    return payload


def validate_against_contract(slices: dict[str, Any], cases: list[dict[str, Any]]) -> None:
    """Ensure the slice file and the evaluation contract describe the same set.

    A question present in one but not the other would be silently dropped from
    every slice-level denominator, so this is a hard failure rather than a
    warning.
    """
    contract_ids = {str(case.get("id")) for case in cases}
    slice_ids = set(slices["by_id"])
    missing = sorted(contract_ids - slice_ids)
    extra = sorted(slice_ids - contract_ids)
    _require(not missing, f"评测契约里有但切片文件缺失的题：{missing}")
    _require(not extra, f"切片文件里有但评测契约缺失的题：{extra}")


def slice_of(slices: dict[str, Any], question_id: str) -> dict[str, Any]:
    """Return the slice entry for a question, failing loudly when absent."""
    entry = slices["by_id"].get(str(question_id))
    if entry is None:
        raise SliceError(f"题 {question_id} 没有切片定义，无法归组")
    return entry


def behaviour_question_ids(slices: dict[str, Any]) -> dict[str, str]:
    """Map question ID -> the behaviour metric it is supposed to exhibit.

    A question's ``case_type`` and its behaviour metric describe the same thing
    from two directions, so they must agree.  Deriving the mapping keeps the
    two from drifting: if a question is relabelled as ``refusal``, the report
    starts scoring it on ``refusal_correctness`` without any second edit.

    ``refusal_correctness`` and ``ambiguity_safety`` are ``None`` on every
    question outside their own case type.  Including those ``None`` values in a
    metric's mean would treat "not applicable" as "scored zero", which is why
    the mapping exists at all.
    """
    mapping = {"refusal": "refusal_correctness", "ambiguous": "ambiguity_safety"}
    return {
        slice_id: mapping[entry["case_type"]]
        for slice_id, entry in slices["by_id"].items()
        if entry["case_type"] in mapping
    }


def behaviour_metric_series(
    rows: list[dict[str, Any]], slices: dict[str, Any], metric: str
) -> dict[str, list[bool]]:
    """Collect one behaviour metric per question, from the questions it applies to.

    Unlike ``group_metric_values`` this deliberately does *not* skip ``None``:
    a question whose behaviour could not be judged (its row failed, or the
    requirement list is malformed) must be visible as a gap rather than
    silently vanish from the denominator.  ``detect_flaky_questions`` treats
    ``None`` as "no verdict", which is the honest reading.

    Only questions whose slotted metric matches are returned, so a refusal
    question never contributes to the ambiguity mean.
    """
    applicable = {
        qid for qid, slotted in behaviour_question_ids(slices).items() if slotted == metric
    }
    series: dict[str, list[bool | None]] = {}
    for row in rows:
        question_id = str(row.get("question_id", ""))
        if question_id not in applicable:
            continue
        value = (row.get("audit") or {}).get(metric)
        series.setdefault(question_id, []).append(None if value is None else bool(value))
    return {qid: values for qid, values in sorted(series.items())}


def group_metric_values(
    rows: list[dict[str, Any]],
    slices: dict[str, Any],
    *,
    by: str = "case_type",
    metric: str,
    applicable_key: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Aggregate one metric per slice value, always reporting the denominator.

    ``sample_size`` counts scored observations, which over several runs is
    larger than the number of questions.  Both are reported, because they answer
    different questions: ``sample_size`` is the weight behind the mean, while
    ``question_ids`` is what a reader needs to name the questions involved.

    ``question_ids`` is deduplicated -- a question appearing once per run must
    not look like several distinct questions.
    """
    _require(by in ("case_type", "risk_level"), f"不支持的切片维度：{by}")
    buckets: dict[str, list[float]] = {}
    questions: dict[str, set[str]] = {}
    for row in rows:
        question_id = str(row.get("question_id", ""))
        entry = slice_of(slices, question_id)
        audit = row.get("audit") or {}
        if applicable_key and not audit.get(applicable_key):
            continue
        value = audit.get(metric)
        if value is None:
            continue
        bucket = buckets.setdefault(str(entry[by]), [])
        bucket.append(float(value))
        questions.setdefault(str(entry[by]), set()).add(question_id)

    return {
        key: {
            "sample_size": len(values),
            "question_count": len(questions.get(key, ())),
            "mean": round(sum(values) / len(values), 3) if values else None,
            "question_ids": sorted(questions.get(key, ())),
        }
        for key, values in sorted(buckets.items())
    }


def flaky_question_ids(slices: dict[str, Any]) -> set[str]:
    """Questions explicitly excluded from regression verdicts."""
    return {sid for sid, entry in slices["by_id"].items() if entry.get("flaky")}


__all__ = [
    "CASE_TYPE_VALUES",
    "HIGH_RISK_LEVEL",
    "REQUIRED_SLICE_FIELDS",
    "RISK_LEVEL_VALUES",
    "SliceError",
    "behaviour_metric_series",
    "behaviour_question_ids",
    "flaky_question_ids",
    "group_metric_values",
    "load_slices",
    "slice_of",
    "validate_against_contract",
]
