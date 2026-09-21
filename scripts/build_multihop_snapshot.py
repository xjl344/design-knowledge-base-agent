"""Assemble composite multi-hop cases from the frozen single-hop snapshot.

Why this exists
---------------
The retrieval side is frozen, so a new question cannot go and fetch its own
evidence.  But every case in ``data/frozen_retrieval_cases.jsonl`` carries 4-10
already-frozen chunks, and ``build_evidence_pack`` only ever keeps 5 of them --
so most of the frozen corpus never reaches the generator at all.

A multi-hop question needs facts from *more than one* document.  Rather than
re-running retrieval (forbidden), this script pools chunks that are already
frozen: a composite case draws its documents from the cases that cover the
facts the question needs.

The anti-fabrication guard
--------------------------
The chunks were retrieved for the *original* question, so there is no reason to
assume they contain what a *new* question needs.  Every hop therefore has to
prove itself: its declared span and every required-term group must be findable
verbatim in the chunk it names.  If a hop cannot prove itself the script stops,
because a composite question whose evidence does not contain the answer would
measure retrieval coverage while claiming to measure generation ability.

Usage::

    python scripts/build_multihop_snapshot.py            # write outputs
    python scripts/build_multihop_snapshot.py --check    # verify, write nothing
    python scripts/build_multihop_snapshot.py --positions  # show hop placement
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.frozen_evidence import (  # noqa: E402
    FrozenDocument,
    FrozenRetrievalCase,
    load_cases,
    write_cases,
)

DEFAULT_SPECS = ROOT / "data" / "multihop_specs.json"
DEFAULT_SNAPSHOT = ROOT / "data" / "frozen_retrieval_cases.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "frozen_multihop_cases.jsonl"
DEFAULT_EVAL = ROOT / "data" / "generation_eval.multihop.v1.json"
DEFAULT_SLICES = ROOT / "data" / "generation_eval_slices.multihop.v1.json"

CONTRACT_VERSION = "multihop.v1"
SLICE_VERSION = "multihop.v1"
SPEC_VERSION = "multihop-specs.v1"

# The composite case is assembled from whole source cases in hop order, so the
# later a hop's source case appears, the further down the context its evidence
# sits.  `build_evidence_pack` truncates to 5 by default, which is what makes
# the two-arm experiment meaningful: an arm that keeps 5 items can lose the
# evidence for the last hop entirely.
TRUNCATION_POINT = 5

# `citation_id_usage_ratio_mean` is "citations used / citations allowed", and
# "allowed" is exactly the number of evidence items.  So the denominator grows
# with the evidence count and the ratio falls for reasons that have nothing to
# do with citation quality: measured across arms of one experiment it went
# 0.767 -> 0.307 purely because the evidence cap rose from 5 to 9-20 items.
#
# Gating on it therefore reports a breach on every comparison that changes how
# much evidence is supplied.  It stays measured and reported -- it is a useful
# diagnostic -- but it must not be gated, because a metric that moves with the
# denominator cannot say anything about the system.
CITATION_USAGE_DIAGNOSTIC = {
    "metric": "citation_id_usage_ratio_mean",
    "reason": (
        "用到的引用 / 允许的引用。允许数 = 证据条数，所以证据变多时分母机械变大、"
        "比值必然下降——实测证据上限从 5 放宽到 9~20 条时该指标 0.767 → 0.307，"
        "那是分母效应不是引用退化。跨证据条数不可比，故只报不卡。"
    ),
}


def _fail(message: str) -> None:
    raise SystemExit(f"[build_multihop_snapshot] {message}")


def _normalise(text: str) -> str:
    """Collapse whitespace so a span can be located regardless of layout.

    The extracted standard text separates table cells with newlines
    (``坐高\\n758\\n780``), so a literal substring test against the raw text
    would reject spans a human reads as present.
    """
    return re.sub(r"\s+", "", str(text or ""))


def _contains(haystack: str, needle: str) -> bool:
    return bool(needle) and _normalise(needle) in _normalise(haystack)


def _term_alternatives(group: Any) -> list[str]:
    """Flatten one required-term group into its acceptable literals."""
    if isinstance(group, str):
        return [group]
    if isinstance(group, dict):
        # Composite predicate: {"negation": [...], "object": [...]}.  Both
        # families must be present in the answer, so both must be present in
        # the source chunk for the hop to be answerable from it.
        values: list[str] = []
        for key in ("negation", "object"):
            values.extend(str(item) for item in (group.get(key) or []))
        return values
    if isinstance(group, (list, tuple)):
        return [str(item) for item in group]
    return []


def _composite_snapshot_id(source_question_ids: Iterable[str], chunk_ids: Iterable[str]) -> str:
    payload = json.dumps(
        {"sources": list(source_question_ids), "chunks": list(chunk_ids)},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_specs(path: Path) -> dict[str, Any]:
    if not path.exists():
        _fail(f"spec 文件不存在：{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != SPEC_VERSION:
        _fail(f"spec version 必须是 {SPEC_VERSION}，实际为 {payload.get('version')!r}")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        _fail("spec 缺少 cases")
    seen: set[str] = set()
    for case in cases:
        case_id = str(case.get("id") or "")
        if not case_id:
            _fail("存在缺少 id 的 spec case")
        if case_id in seen:
            _fail(f"spec case id 重复：{case_id}")
        seen.add(case_id)
        if not case.get("question"):
            _fail(f"{case_id} 缺少 question")
        sources = case.get("sources")
        if not isinstance(sources, list) or not sources:
            _fail(f"{case_id} 缺少 sources")
        hops = case.get("hops")
        if not isinstance(hops, list) or len(hops) < 2:
            _fail(f"{case_id} 至少需要 2 跳（多跳题的定义）")
        for hop in hops:
            for field in ("hop_id", "from_question", "chunk_id", "expected_span"):
                if not hop.get(field):
                    _fail(f"{case_id} 的 hop 缺少 {field}")
            if not hop.get("required_terms"):
                _fail(f"{case_id}/{hop.get('hop_id')} 缺少 required_terms")
            if str(hop["from_question"]) not in {str(s) for s in sources}:
                _fail(
                    f"{case_id}/{hop['hop_id']} 的来源题 {hop['from_question']} "
                    "不在 sources 里；证据必须来自声明的来源，否则溯源不成立"
                )
    return payload


def _resolve_hop(
    spec_id: str,
    hop: dict[str, Any],
    cases: dict[str, FrozenRetrievalCase],
) -> tuple[FrozenDocument, dict[str, Any]]:
    """Locate the hop's chunk and prove the hop is answerable from it."""
    source_id = str(hop["from_question"])
    case = cases.get(source_id)
    if case is None:
        _fail(f"{spec_id}/{hop['hop_id']}：来源题 {source_id} 不在冻结快照里")
    chunk_id = str(hop["chunk_id"])
    document = next((item for item in case.documents if item.chunk_id == chunk_id), None)
    if document is None:
        _fail(f"{spec_id}/{hop['hop_id']}：chunk {chunk_id[:12]}… 不在 {source_id} 的证据里")

    content = document.page_content
    span = str(hop["expected_span"])
    if not _contains(content, span):
        _fail(
            f"{spec_id}/{hop['hop_id']}：期望片段 {span!r} 不是 {source_id} 该 chunk 的原文子串。"
            "该跳无法由这份证据支持，拒绝生成复合题。"
        )
    for index, group in enumerate(hop["required_terms"]):
        alternatives = _term_alternatives(group)
        if not alternatives:
            _fail(f"{spec_id}/{hop['hop_id']}：required_terms 第 {index} 组为空")
        if not any(_contains(content, item) for item in alternatives):
            _fail(
                f"{spec_id}/{hop['hop_id']}：required_terms 第 {index} 组 {alternatives!r} "
                f"在该 chunk 原文中一个都找不到；该跳不可答。"
            )
    return document, hop


def build_composite(
    spec: dict[str, Any],
    cases: dict[str, FrozenRetrievalCase],
) -> tuple[FrozenRetrievalCase, list[dict[str, Any]]]:
    spec_id = str(spec["id"])
    source_ids = [str(item) for item in spec["sources"]]

    hop_rows: list[dict[str, Any]] = []
    needed_chunk_ids: set[str] = set()
    for hop in spec["hops"]:
        document, _ = _resolve_hop(spec_id, hop, cases)
        needed_chunk_ids.add(document.chunk_id)
        hop_rows.append({"hop_id": str(hop["hop_id"]), "document": document, "spec": hop})

    # Pool whole source cases, in hop order, so a hop whose source case comes
    # later lands further down the context.  Deduplicate by chunk_id because the
    # same chunk can legitimately be retrieved for two different questions.
    documents: list[FrozenDocument] = []
    seen_chunks: set[str] = set()
    for source_id in source_ids:
        for document in cases[source_id].documents:
            if document.chunk_id in seen_chunks:
                continue
            seen_chunks.add(document.chunk_id)
            documents.append(document)

    missing = needed_chunk_ids - seen_chunks
    if missing:
        _fail(f"{spec_id}：池化后仍缺少必要 chunk {sorted(item[:12] for item in missing)}")

    positions = {document.chunk_id: index + 1 for index, document in enumerate(documents)}
    for row in hop_rows:
        row["position"] = positions[row["document"].chunk_id]
        row["within_truncation"] = row["position"] <= TRUNCATION_POINT

    primary = cases[source_ids[0]]
    composite = FrozenRetrievalCase(
        question_id=spec_id,
        question=str(spec["question"]),
        retrieval_snapshot_id=_composite_snapshot_id(source_ids, [d.chunk_id for d in documents]),
        # The chunks genuinely came from the source case's index under its
        # config, so those fields are copied rather than invented.  What is
        # *not* true is that this question was ever retrieved, and that is what
        # the profile below records.
        retrieval_config=dict(primary.retrieval_config),
        index_fingerprint=dict(primary.index_fingerprint),
        documents=tuple(documents),
        retrieval_profile={
            "composite": True,
            "composite_spec": spec_id,
            "composite_theme": spec.get("theme"),
            "composite_source_cases": source_ids,
            "composite_hops": [
                {
                    "hop_id": row["hop_id"],
                    "from_question": row["spec"]["from_question"],
                    "chunk_id": row["document"].chunk_id,
                    "position": row["position"],
                }
                for row in hop_rows
            ],
            "note": "由冻结的单跳 case 池化而成，未执行检索（retrieval_calls=0）",
        },
        retrieval_timing={"composite": True, "document_count": len(documents)},
    )
    return composite, hop_rows


def build_contract(
    specs: list[dict[str, Any]],
    hop_rows_by_id: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    contract_cases: list[dict[str, Any]] = []
    for spec in specs:
        spec_id = str(spec["id"])
        hop_rows = hop_rows_by_id[spec_id]
        required_hops: list[dict[str, Any]] = []
        for row in hop_rows:
            hop = row["spec"]
            required_hops.append({
                "hop_id": str(hop["hop_id"]),
                "from_question": str(hop["from_question"]),
                "source_chunk_id": row["document"].chunk_id,
                "expected_span": str(hop["expected_span"]),
                "required_terms": hop["required_terms"],
                "position": row["position"],
            })
        # `expected_sources` is deliberately populated: besides giving the
        # source-coverage metric something to measure, a non-empty list keeps
        # the auditor out of its word-list refusal branch, which would
        # otherwise score a factual answer as "failed to refuse".
        expected_sources: list[str] = []
        expected_spans: list[dict[str, Any]] = []
        for index, row in enumerate(hop_rows, 1):
            source = str(row["document"].metadata.get("source") or "")
            if source and source not in expected_sources:
                expected_sources.append(source)
            # Spans follow the contract format the auditor reads: a list of
            # objects with `text`, not bare strings.  Passing strings made
            # `soft_audit` fail with AttributeError on `expected.get("text")`.
            expected_spans.append({
                "id": f"{spec_id}-h{index}",
                "text": str(row["spec"]["expected_span"]),
                "source": source,
            })
        contract_cases.append({
            "id": spec_id,
            "question": str(spec["question"]),
            "expected_answer_spans": expected_spans,
            "expected_sources": expected_sources,
            # Left empty on purpose: the per-hop terms below are the multi-hop
            # signal, and pooling them here too would double-count the same
            # facts into required_term_recall.
            "required_terms": [],
            "refusal_requirements": [],
            "ambiguity_requirements": [],
            "required_hops": required_hops,
        })
    return {
        # Integer version, matching every other contract file.  A string like
        # "multihop.v1" broke `load_evaluation_version`, which parses the field
        # as an int; `family` carries the disambiguation instead.
        "version": 1,
        "family": CONTRACT_VERSION,
        "generated_by": "scripts/build_multihop_snapshot.py",
        "generated_from": str(DEFAULT_SPECS.relative_to(ROOT)).replace("\\", "/"),
        "note": (
            "复合多跳题契约：证据由冻结的单跳 case 池化而成，未执行检索。"
            "required_hops 逐跳声明该跳的事实必须来自哪个 chunk。"
        ),
        "cases": contract_cases,
    }


def build_slices(specs: list[dict[str, Any]]) -> dict[str, Any]:
    slices = []
    for spec in specs:
        slices.append({
            "id": str(spec["id"]),
            "case_type": "multi_hop",
            "risk_level": str(spec.get("risk_level") or "medium"),
            "slice_tags": list(spec.get("slice_tags") or []),
            "flaky": False,
            "notes": str(spec.get("notes") or ""),
        })
    return {
        "version": SLICE_VERSION,
        "generated_by": "scripts/build_multihop_snapshot.py",
        # Must equal `CASE_TYPE_VALUES` in src/generation_slices.py, which
        # load_slices enforces.  Adding multi_hop there is part of this change.
        "case_type_values": ["ambiguous", "fact_enumeration", "fact_numeric", "multi_hop", "refusal"],
        "risk_level_values": ["high", "low", "medium"],
        "metric_groups": {
            "retrieval": {"metrics": ["retrieval_calls"], "allowed_change": 0.0, "enforcement": "hard"},
            "usability": {
                "metrics": ["generation_success_rate", "provider_timeout_rate", "provider_hard_error_rate"],
                "allowed_change": 0.0,
                "enforcement": "gate",
            },
            "quality": {
                "metrics": ["answer_span_recall_mean", "required_term_recall_mean"],
                "allowed_change": 0.05,
                "enforcement": "gate",
            },
            "multi_hop": {
                "metrics": ["hop_recall_mean"],
                "allowed_change": 0.10,
                "enforcement": "gate",
            },
            "safety": {
                "metrics": ["refusal_correctness_rate", "ambiguity_safety_rate"],
                "allowed_change": 0.0,
                "enforcement": "gate",
            },
            "citation": {
                # `citation_id_usage_ratio_mean` is deliberately NOT gated; see
                # CITATION_USAGE_DIAGNOSTIC_REASON.
                "metrics": ["citation_validity_rate"],
                "diagnostics": [CITATION_USAGE_DIAGNOSTIC],
                "allowed_change": 0.05,
                "enforcement": "gate",
            },
        },
        "slices": slices,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _compare(path: Path, payload: dict[str, Any]) -> bool:
    if not path.exists():
        _fail(f"--check 失败：{path} 不存在")
    current = json.loads(path.read_text(encoding="utf-8"))
    if current == payload:
        return True
    _fail(f"--check 失败：{path} 与由 spec 重新生成的结果不一致（文件已过期或被手改）")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="由冻结单跳快照池化出复合多跳题")
    parser.add_argument("--specs", type=Path, default=DEFAULT_SPECS)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--evaluation", type=Path, default=DEFAULT_EVAL)
    parser.add_argument("--slices", type=Path, default=DEFAULT_SLICES)
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    parser.add_argument("--positions", action="store_true", help="打印每跳在上下文中的位置")
    args = parser.parse_args()

    specs_payload = load_specs(args.specs)
    specs = specs_payload["cases"]
    cases = load_cases(args.snapshot)

    composites: list[FrozenRetrievalCase] = []
    hop_rows_by_id: dict[str, list[dict[str, Any]]] = {}
    for spec in specs:
        composite, hop_rows = build_composite(spec, cases)
        composites.append(composite)
        hop_rows_by_id[str(spec["id"])] = hop_rows

    contract = build_contract(specs, hop_rows_by_id)
    slices = build_slices(specs)

    if args.positions:
        for spec in specs:
            spec_id = str(spec["id"])
            composite = next(item for item in composites if item.question_id == spec_id)
            print(f"\n{spec_id}  ({len(composite.documents)} chunks pooled, "
                  f"{len(composite.documents[0].page_content) and ''}"
                  f"ctx={sum(len(d.page_content) for d in composite.documents)} chars)")
            for row in hop_rows_by_id[spec_id]:
                mark = "kept" if row["within_truncation"] else "TRUNCATED@5"
                print(f"   {row['hop_id']:4s} pos={row['position']:<3d} {mark:14s} "
                      f"{row['spec']['from_question']:16s} span={row['spec']['expected_span']!r}")

    if args.check:
        _compare(args.output, {"cases": [c.as_dict() for c in composites]})
        _compare(args.evaluation, contract)
        _compare(args.slices, slices)
        print(json.dumps({"check": "ok", "cases": len(composites)}, ensure_ascii=False))
        return 0

    write_cases(args.output, composites)
    _write_json(args.evaluation, contract)
    _write_json(args.slices, slices)
    print(json.dumps({
        "output": str(args.output),
        "evaluation": str(args.evaluation),
        "slices": str(args.slices),
        "cases": len(composites),
        "hops": sum(len(rows) for rows in hop_rows_by_id.values()),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
