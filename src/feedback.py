"""User feedback ledger, bad-case taxonomy, and weekly triage summaries.

Implements ``docs/prd-feedback-loop.md`` with zero third-party dependencies so
the ledger and weekly reports remain testable and usable in offline CI.

Privacy invariant:
    Only SHA-256 prefixes (``question_hash``, ``answer_hash``) are stored by
    default. Plaintext ``question_text`` is persisted *only* when the user
    explicitly opts in.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import Lock
from typing import Any
import uuid


VERDICTS = frozenset({"up", "down"})

# Four-choice negative reason codes from PRD §6.
REASON_CODES: dict[str, str] = {
    "wrong_evidence": "哪条证据不对",
    "hallucinated": "答案是编的",
    "did_not_answer": "没回答我的问题",
    "other": "其他",
}

# Seven-class bad-case taxonomy from PRD §7.
FAILURE_CATEGORIES: dict[str, str] = {
    "R1": "检索漏（需要的来源没进上下文）",
    "R2": "排序错（来源进了上下文但排在截断之后）",
    "G1": "生成漏项（提到了类别/概念，没给出具体项与数值）",
    "G2": "过度保守（证据在包里，仍以资料不足收尾）",
    "G3": "引用错（引用编号不可解析或引了不支持来源）",
    "M1": "度量口径误判（不是系统错，是判据错）",
    "U1": "问法歧义（问题本身有歧义，正确行为是澄清）",
}

# PRD §8 forces explicit separation of system failures vs metric defects.
ATTRIBUTION_KINDS = frozenset({"system_failure", "metric_defect"})

MIN_STATISTICAL_SAMPLE_SIZE = 30


def short_hash(text: str) -> str:
    """Return a stable 16-hex-char SHA-256 prefix for privacy-preserving tracking."""
    normalized = " ".join(str(text or "").strip().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compute_snapshot_id(
    chroma_dir: Path,
    collection_name: str,
    chunking_strategy: str,
) -> str:
    """Compute a lightweight index snapshot fingerprint without loading models."""
    manifest_path = chroma_dir / "ingest_manifest.json"
    if not manifest_path.is_file():
        manifest_path = chroma_dir.parent / "ingest_manifest.json"
    parts = [str(collection_name), str(chunking_strategy)]
    if manifest_path.is_file():
        try:
            raw = manifest_path.read_bytes()
            parts.append(hashlib.sha256(raw).hexdigest()[:16])
        except OSError:
            parts.append("manifest-unreadable")
    else:
        parts.append("no-manifest")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


@dataclass
class FeedbackRecord:
    feedback_id: str
    created_at: str
    session_id: str
    question_id: str
    question_hash: str
    answer_hash: str
    verdict: str
    reason_code: str | None = None
    blamed_evidence_ids: list[str] = field(default_factory=list)
    free_text: str = ""
    question_text: str | None = None
    generator_backend: str = "v2"
    latency_seconds: float = 0.0
    evidence_count: int = 0
    snapshot_id: str = "unknown"
    delivery_status: str = "unknown"
    deliverable: bool = False
    was_refusal: bool = False
    triage_status: str = "pending"
    failure_category: str | None = None
    attribution_kind: str | None = None
    disposition: str = ""
    triaged_at: str | None = None
    promoted_to_regression: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FeedbackRecord":
        known = {name: payload[name] for name in cls.__dataclass_fields__ if name in payload}
        return cls(**known)


def build_feedback_record(
    *,
    session_id: str,
    question: str,
    answer: str,
    verdict: str,
    reason_code: str | None = None,
    blamed_evidence_ids: list[str] | None = None,
    free_text: str = "",
    include_question_text: bool = False,
    generator_backend: str = "v2",
    latency_seconds: float = 0.0,
    evidence_count: int = 0,
    snapshot_id: str = "unknown",
    delivery_status: str = "unknown",
    deliverable: bool = False,
    question_id: str | None = None,
) -> FeedbackRecord:
    """Validate inputs and construct a privacy-safe ``FeedbackRecord``."""
    verdict_norm = str(verdict or "").strip().lower()
    if verdict_norm not in VERDICTS:
        raise ValueError(f"verdict 必须为 {sorted(VERDICTS)} 之一，收到：{verdict!r}")

    reason_norm = str(reason_code).strip() if reason_code else None
    if verdict_norm == "down":
        if not reason_norm or reason_norm not in REASON_CODES:
            raise ValueError(
                f"点踩反馈必须提供有效 reason_code（可选：{sorted(REASON_CODES)}）"
            )
    else:
        reason_norm = None

    q_hash = short_hash(question)
    a_hash = short_hash(answer)
    blamed = [str(item).strip() for item in (blamed_evidence_ids or []) if str(item).strip()]
    was_refusal = (not bool(deliverable)) or ("insufficient" in str(delivery_status).lower())

    return FeedbackRecord(
        feedback_id=f"fb-{uuid.uuid4().hex[:12]}",
        created_at=utc_now_iso(),
        session_id=str(session_id or ""),
        question_id=str(question_id or f"q-{q_hash[:10]}"),
        question_hash=q_hash,
        answer_hash=a_hash,
        verdict=verdict_norm,
        reason_code=reason_norm,
        blamed_evidence_ids=blamed,
        free_text=str(free_text or "").strip()[:1000],
        question_text=str(question).strip() if include_question_text and question else None,
        generator_backend=str(generator_backend or "v2"),
        latency_seconds=round(max(0.0, float(latency_seconds or 0.0)), 3),
        evidence_count=max(0, int(evidence_count or 0)),
        snapshot_id=str(snapshot_id or "unknown"),
        delivery_status=str(delivery_status or "unknown"),
        deliverable=bool(deliverable),
        was_refusal=was_refusal,
    )


class FeedbackLedger:
    """Append-only JSONL feedback ledger with triage & regression export."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = Lock()

    def append(self, record: FeedbackRecord) -> FeedbackRecord:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record.as_dict(), ensure_ascii=False)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return record

    def load_all(self) -> list[FeedbackRecord]:
        if not self.path.is_file():
            return []
        records: list[FeedbackRecord] = []
        with self._lock:
            for raw in self.path.read_text(encoding="utf-8").splitlines():
                stripped = raw.strip()
                if not stripped:
                    continue
                records.append(FeedbackRecord.from_dict(json.loads(stripped)))
        return records

    def _save_all(self, records: list[FeedbackRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record.as_dict(), ensure_ascii=False) + "\n")
        tmp.replace(self.path)

    def triage(
        self,
        feedback_id: str,
        *,
        failure_category: str,
        attribution_kind: str,
        disposition: str = "",
    ) -> FeedbackRecord:
        """Assign PRD §7 bad-case category and PRD §8 attribution kind."""
        if failure_category not in FAILURE_CATEGORIES:
            raise ValueError(
                f"未知坏案例分类 {failure_category!r}；可选：{sorted(FAILURE_CATEGORIES)}"
            )
        if attribution_kind not in ATTRIBUTION_KINDS:
            raise ValueError(
                f"attribution_kind 必须为 {sorted(ATTRIBUTION_KINDS)} 之一"
            )
        if failure_category == "M1" and attribution_kind != "metric_defect":
            raise ValueError("M1（度量口径误判）的 attribution_kind 必须为 metric_defect")

        with self._lock:
            records = self._load_unlocked()
            target: FeedbackRecord | None = None
            for record in records:
                if record.feedback_id == feedback_id:
                    record.triage_status = "triaged"
                    record.failure_category = failure_category
                    record.attribution_kind = attribution_kind
                    record.disposition = str(disposition or "").strip()
                    record.triaged_at = utc_now_iso()
                    target = record
                    break
            if target is None:
                raise KeyError(f"未找到反馈记录：{feedback_id}")
            self._save_all(records)
            return target

    def export_regression_case(self, feedback_id: str, out_path: Path) -> dict[str, Any]:
        """Promote a confirmed bad case into an offline regression JSONL file."""
        with self._lock:
            records = self._load_unlocked()
            target = next((r for r in records if r.feedback_id == feedback_id), None)
            if target is None:
                raise KeyError(f"未找到反馈记录：{feedback_id}")
            case = {
                "case_id": f"reg-{target.feedback_id}",
                "source_feedback_id": target.feedback_id,
                "question_id": target.question_id,
                "question_hash": target.question_hash,
                "question_text": target.question_text,
                "failure_category": target.failure_category,
                "attribution_kind": target.attribution_kind,
                "reason_code": target.reason_code,
                "blamed_evidence_ids": target.blamed_evidence_ids,
                "free_text": target.free_text,
                "snapshot_id": target.snapshot_id,
                "generator_backend": target.generator_backend,
                "observation_tier": "single_observation",
                "exported_at": utc_now_iso(),
            }
            out_path = Path(out_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with out_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(case, ensure_ascii=False) + "\n")
            target.promoted_to_regression = True
            self._save_all(records)
            return case

    def _load_unlocked(self) -> list[FeedbackRecord]:
        if not self.path.is_file():
            return []
        return [
            FeedbackRecord.from_dict(json.loads(raw))
            for raw in self.path.read_text(encoding="utf-8").splitlines()
            if raw.strip()
        ]


def summarize_feedback(
    records: list[FeedbackRecord],
    *,
    total_sessions: int | None = None,
) -> dict[str, Any]:
    """Produce a descriptive summary aligned with PRD §4 & §8 guardrails."""
    total = len(records)
    up_count = sum(1 for r in records if r.verdict == "up")
    down_count = sum(1 for r in records if r.verdict == "down")
    triaged_down = [r for r in records if r.verdict == "down" and r.triage_status == "triaged"]
    pending_down = [r for r in records if r.verdict == "down" and r.triage_status != "triaged"]
    refusal_feedback = sum(1 for r in records if r.was_refusal)

    by_reason: dict[str, int] = {}
    for record in records:
        if record.reason_code:
            by_reason[record.reason_code] = by_reason.get(record.reason_code, 0) + 1

    by_category: dict[str, int] = {}
    by_attribution: dict[str, int] = {}
    for record in triaged_down:
        if record.failure_category:
            by_category[record.failure_category] = by_category.get(record.failure_category, 0) + 1
        if record.attribution_kind:
            by_attribution[record.attribution_kind] = by_attribution.get(record.attribution_kind, 0) + 1

    unique_feedback_sessions = len({r.session_id for r in records if r.session_id})
    submission_rate = (
        round(unique_feedback_sessions / total_sessions, 4)
        if total_sessions and total_sessions > 0
        else None
    )

    warnings: list[str] = []
    if total < MIN_STATISTICAL_SAMPLE_SIZE:
        warnings.append(
            f"样本量 n={total} < {MIN_STATISTICAL_SAMPLE_SIZE}：仅作描述性统计，不输出显著性比率结论（PRD §4）。"
        )
    if submission_rate is not None and total_sessions and total_sessions >= 20 and submission_rate < 0.05:
        warnings.append(
            f"反馈提交率 {submission_rate:.1%} < 5%：按 PRD §8 预登记判据，先怀疑入口设计而非系统质量。"
        )

    return {
        "total_feedback": total,
        "up_count": up_count,
        "down_count": down_count,
        "triaged_bad_cases": len(triaged_down),
        "pending_bad_cases": len(pending_down),
        "refusal_feedback_count": refusal_feedback,
        "unique_feedback_sessions": unique_feedback_sessions,
        "total_sessions": total_sessions,
        "submission_rate": submission_rate,
        "by_reason_code": by_reason,
        "by_failure_category": by_category,
        "by_attribution_kind": by_attribution,
        "descriptive_only": total < MIN_STATISTICAL_SAMPLE_SIZE,
        "warnings": warnings,
    }
