"""Immutable retrieval snapshots and deterministic evidence packs.

The snapshot is the boundary between the frozen retrieval system and the
generation experiments.  Replay code only needs this module and never opens
Chroma or calls the retriever.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:  # pragma: no cover - import-time only
    # ``Document`` appears only in annotations.  Importing it at runtime would
    # make this module -- which is pure, deterministic scoring logic -- require
    # langchain to be installed, and with it the whole model stack.  Keeping it
    # behind TYPE_CHECKING is what lets the offline analysis path run on a bare
    # Python installation; see tests/test_eval_dependency_surface.py.
    from langchain_core.documents import Document


# Version of the deterministic auditing rules below.  It lives next to the
# implementation on purpose: when a scoring rule changes, this string must be
# bumped in the same edit, and every recorded run carries it so that runs
# scored under different rules are never silently compared.
#   v1 -> initial three-state citation / span auditing
#   v2 -> span noise pre-cleaning, full-width citations, applicability flags
#   v3 -> behaviour requirements become composite predicates; word-list
#         alternatives remain as the fallback branch
#   v4 -> an unanswered question yields no behaviour verdict instead of a
#         failed one, and citation applicability is decided from the contract
#         rather than from whether a verdict was produced
#   v5 -> multi-hop questions gain per-hop coverage (``hop_recall``); a hop is
#         scored on its own required terms so one hop cannot carry another
AUDIT_VERSION = "soft-audit-behaviour-v5"


# Text substituted when the provider failed to produce an answer.  It lives
# here, next to the auditor, because the auditor has to recognise it: the
# fallback is a fixed string that *looks* like an answer, and scoring it
# measures the substitute rather than the system.  A refusal question whose
# call timed out was reported as "failed to refuse" for exactly this reason.
FALLBACK_ANSWER_WITH_EVIDENCE = (
    "当前生成模型不可用，无法生成正式回答；冻结证据已保留，请稍后重试。"
)
FALLBACK_ANSWER_WITHOUT_EVIDENCE = (
    "当前资料无法确认，冻结证据包中没有可用的本地证据。"
)
FALLBACK_ANSWERS = frozenset({
    FALLBACK_ANSWER_WITH_EVIDENCE,
    FALLBACK_ANSWER_WITHOUT_EVIDENCE,
})


def is_fallback_answer(answer: str | None) -> bool:
    """Whether ``answer`` is a provider-failure substitute rather than a reply.

    Compared against the exact strings, not by substring: a real answer may
    legitimately contain 无法生成, and treating that as a failure substitute
    would drop a genuine reply from the metrics.
    """
    return str(answer or "").strip() in FALLBACK_ANSWERS


# Citation markers.  Models frequently emit full-width brackets (【L1】) or
# Chinese book-title brackets (〔L1〕) even when asked for [L1]; all of these
# denote a real citation and must be counted, otherwise a genuinely cited
# answer is misread as citation-free and lands in ``not_applicable``.
_CITATION_RE = re.compile(r"[\[【〔]([LW]\d+)[\]】〕]")

# Citation markers and table labels such as ``（T1）``, ``(B3)`` or a bare
# ``T1`` must be removed *before* normalisation.  They sit inline in the
# answer text (``座深（T1）：340 mm～460 mm`` / ``座深 T1：340～460 mm``)
# and survive the character filter as stray alphanumerics, which corrupts
# both neighbouring tokens (``座深`` -> ``座深t``, ``340`` -> ``1340``)
# and defeats every fallback in :func:`_span_matches`.  Stripping them
# afterwards is too late, so this runs first.
_SPAN_LABEL_RE = re.compile(r"[\[【〔][LWlw]\d+[\]】〕]")
_SPAN_PARENTHETICAL_RE = re.compile(r"[（(][^）)]{1,12}[）)]")
# Bare dimension/table labels: a single uppercase letter plus digits, e.g.
# ``T1``, ``B3``, ``H2``.  Requires a boundary on both sides so that real
# values such as ``10mm`` or ``GB3326`` are not touched, and so that the
# digits belonging to the label (``1340`` below) are not mistaken for data.
_SPAN_BARE_LABEL_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]\d{1,2}(?![A-Za-z0-9])")


def _strip_span_noise(value: Any) -> str:
    """Remove citation markers and table labels prior to normalising."""
    text = _SPAN_LABEL_RE.sub("", str(value or ""))
    text = _SPAN_PARENTHETICAL_RE.sub("", text)
    return _SPAN_BARE_LABEL_RE.sub("", text)


# ---------------------------------------------------------------------------
# Behaviour predicates
# ---------------------------------------------------------------------------
# A behaviour requirement ("must refuse", "must not apply a generic value
# directly") is a *semantic* property.  Scoring it with a word list makes the
# metric flip on wording alone: over two recorded runs the same model gave
# ``不能无条件作为儿童座椅高度`` and ``不能直接作为儿童座椅的无条件推荐值``,
# which are equivalent, and the word list scored one ``False`` and the other
# ``True``.  A metric that changes answer without the answer changing is not a
# metric.
#
# So a requirement group may be written as a *composite predicate*: a negation
# family AND an object family, both matched loosely.  ``不能无条件`` and
# ``不能直接`` both satisfy ``(negation) x (direct|simple|unconditional|apply)``,
# while a bare ``不能`` alone does not, because nothing in the object family
# appears.  Reviewers found the reverse test (three answers that genuinely
# assert the wrong thing) correctly rejected by all of them.
_BEHAVIOUR_NEGATIONS = ("不能", "不应", "不宜", "不可", "不得", "无法", "勿", "禁止")
# Objects that mark a *prohibition on transfer/applicability* rather than some
# unrelated negation elsewhere in the answer.
_BEHAVIOUR_OBJECTS = ("直接", "简单", "无条件", "套用", "照搬", "等同", "当成", "当作")


def _json_default(value: Any) -> str:
    return str(value)


def _normalise_text(value: Any) -> str:
    text = str(value or "").lower()
    text = text.replace("不小于", "ge").replace("不低于", "ge").replace("至少", "ge")
    text = text.replace("大于等于", "ge").replace("不少于", "ge")
    text = text.replace("不超过", "le").replace("不大于", "le").replace("至多", "le")
    text = text.replace("大于等于", "ge").replace("小于等于", "le")
    text = text.replace(">=", "ge").replace("≤", "le").replace("≥", "ge")
    text = text.replace("<=", "le")
    text = text.replace("毫米", "mm").replace("厘米", "cm")
    text = text.replace("～", "~").replace("至", "~").replace("到", "~")
    text = text.replace("−", "-").replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", "", text)
    # Treat range units consistently: 400mm~440mm and 400~440 mm
    # represent the same compact numeric span for answer-span matching.
    text = re.sub(r"(\d+(?:\.\d+)?)(mm|ml|℃|%)~(\d+(?:\.\d+)?)(mm|ml|℃|%)", r"\1~\3\2", text)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff~%-]+", "", text)


def _span_matches(expected: str, answer: str) -> bool:
    # Strip citation markers and table labels first; see _strip_span_noise.
    expected_normalised = _normalise_text(_strip_span_noise(expected))
    answer_normalised = _normalise_text(_strip_span_noise(answer))
    if not expected_normalised:
        return False
    if expected_normalised in answer_normalised:
        return True
    # Some human-labelled spans describe a concept rather than a verbatim
    # quote.  Keep this deliberately small and domain-neutral: all key terms
    # must still be present through an explicit synonym set.
    phrase_synonyms = {
        "年龄段": ("年龄段", "年龄", "目标年龄"),
        "百分位数": ("百分位数", "百分位"),
        "推算": ("推算", "换算", "计算", "确定"),
    }
    if not re.search(r"\d", expected_normalised) and any(
        marker in expected_normalised for marker in ("年龄段", "百分位数", "推算")
    ):
        semantic_terms = [term for term in phrase_synonyms if term in expected_normalised]
        if semantic_terms and all(
            any(alias in answer_normalised for alias in phrase_synonyms.get(term, (term,)))
            for term in semantic_terms
        ):
            return True
    expected_numbers = re.findall(r"\d+(?:\.\d+)?", expected_normalised)
    answer_numbers = re.findall(r"\d+(?:\.\d+)?", answer_normalised)
    if expected_numbers and all(number in answer_numbers for number in expected_numbers):
        # Numeric ranges and standards are often paraphrased while their
        # exact values remain the stable semantic ground truth.
        if len(expected_numbers) > 1:
            expected_order = [answer_numbers.index(number) for number in expected_numbers]
            if expected_order == sorted(expected_order):
                return True
        else:
            expected_words = re.sub(r"\d+(?:\.\d+)?(?:mm|ml|cm|℃|%|岁|年|项)?", "", expected_normalised)
            expected_words = re.sub(r"(?:ge|le|为|最大|最小|标准|要求|分别|的)+", "", expected_words)
            if not expected_words or expected_words[-4:] in answer_normalised:
                return True
    # Ranges may include table labels or repeat units, e.g. ``座深（T1）：
    # 340 mm～460 mm``. The ordered numeric sequence remains deterministic.
    if len(expected_numbers) >= 2:
        cursor = 0
        for number in expected_numbers:
            try:
                cursor = answer_numbers.index(number, cursor) + 1
            except ValueError:
                break
        else:
            return True
    # Allow a small wording variation around a numeric span, e.g. "软面"
    # versus "软座面", while retaining the exact numbers and units.
    expected_tokens = re.findall(r"\d+(?:\.\d+)?(?:mm|ml|℃|%)?|[\u4e00-\u9fffA-Za-z]+", expected_normalised)
    answer_tokens = re.findall(r"\d+(?:\.\d+)?(?:mm|ml|℃|%)?|[\u4e00-\u9fffA-Za-z]+", answer_normalised)
    if not expected_tokens:
        return False
    for index in range(0, len(answer_tokens) - len(expected_tokens) + 1):
        window = answer_tokens[index : index + len(expected_tokens)]
        if all(left == right or (left.isdigit() and right.isdigit() and left == right) for left, right in zip(expected_tokens, window)):
            return True
    # For Chinese phrases, the stable tail around a number is often more
    # useful than requiring an exact modifier match.
    expected_compact = "".join(expected_tokens)
    numeric_match = re.search(r"(\d+(?:\.\d+)?(?:mm|ml|℃|%)?)$", expected_compact)
    if numeric_match:
        prefix = expected_compact[: numeric_match.start()]
        stable_tail = prefix[-4:] + numeric_match.group(1)
        if len(stable_tail) >= 5 and stable_tail in answer_normalised:
            return True
    stable_tail = "".join(token for token in expected_tokens[-3:])
    return len(stable_tail) >= 4 and stable_tail in "".join(answer_tokens)


def _source_matches(expected: str, actual: str) -> bool:
    expected_normalised = str(expected or "").replace("\\", "/").rstrip("/").lower()
    actual_normalised = str(actual or "").replace("\\", "/").rstrip("/").lower()
    if not expected_normalised or not actual_normalised:
        return False
    if expected_normalised in actual_normalised or actual_normalised in expected_normalised:
        return True
    return expected_normalised.rsplit("/", 1)[-1] == actual_normalised.rsplit("/", 1)[-1]


def _has_refusal_context(answer: str, start: int, end: int) -> bool:
    """Return whether a number occurs in the same refusal statement.

    A question such as ``无法确认2025年的趋势`` mentions the requested year,
    but does not assert a factual 2025 value.  Looking at the complete
    sentence handles punctuation and quoted years more reliably than a short
    character window.
    """
    refusal_markers = ("无法", "不能", "没有", "未找到", "缺少", "不具备", "未提供")
    left = max(
        answer.rfind(mark, 0, start) for mark in ("。", "！", "？", "\n", ";", ";")
    )
    right_candidates = [answer.find(mark, end) for mark in ("。", "！", "？", "\n", ";", ";")]
    right_candidates = [value for value in right_candidates if value >= 0]
    right = min(right_candidates) if right_candidates else len(answer)
    sentence = answer[left + 1 : right]
    return any(marker in sentence for marker in refusal_markers)


def _document_identity(document: Document) -> str:
    metadata = document.metadata or {}
    return str(
        metadata.get("chunk_id")
        or metadata.get("content_hash")
        or hashlib.sha256(
            f"{metadata.get('source', '')}|{metadata.get('page', '')}|{document.page_content}".encode("utf-8")
        ).hexdigest()
    )


def retrieval_config_snapshot(settings: Any) -> dict[str, Any]:
    """Capture configuration metadata without changing any retrieval setting."""
    return {
        "chunking_strategy": str(settings.chunking_strategy),
        "collection_name": str(settings.collection_name),
        "chroma_dir": str(settings.chroma_dir),
        "top_k": int(settings.retriever_top_k),
        "dense_top_k": int(settings.retriever_dense_top_k),
        "bm25_top_k": int(settings.retriever_bm25_top_k),
        "rerank_top_k": int(settings.retriever_rerank_top_k),
        "ranking_mode": str(settings.retriever_ranking_mode),
        "embedding_model_path": str(settings.embedding_model_path),
        "reranker_model_path": str(settings.reranker_model_path or ""),
    }


def index_fingerprint(settings: Any) -> dict[str, Any]:
    """Return a read-only fingerprint of the active Chroma collection."""
    import chromadb

    client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    try:
        collection = client.get_collection(settings.collection_name)
        count = int(collection.count())
        payload = collection.get(include=["metadatas"])
        rows: list[dict[str, Any]] = []
        for item_id, metadata in zip(payload.get("ids", []) or [], payload.get("metadatas", []) or []):
            metadata = dict(metadata or {})
            rows.append({
                "id": str(item_id),
                "chunk_id": str(metadata.get("chunk_id", "")),
                "content_hash": str(metadata.get("content_hash", "")),
                "source": str(metadata.get("source", "")),
            })
    finally:
        client.close()
    rows.sort(key=lambda row: row["id"])
    digest = hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {"count": count, "metadata_digest": digest}


@dataclass(frozen=True)
class FrozenDocument:
    document_id: str
    chunk_id: str
    content_hash: str | None
    page_content: str
    metadata: dict[str, Any]
    retrieval_rank: int

    @classmethod
    def from_document(cls, document: Document, retrieval_rank: int) -> "FrozenDocument":
        metadata = dict(document.metadata or {})
        return cls(
            document_id=str(metadata.get("document_id") or metadata.get("id") or _document_identity(document)),
            chunk_id=str(metadata.get("chunk_id") or _document_identity(document)),
            content_hash=(str(metadata["content_hash"]) if metadata.get("content_hash") else None),
            page_content=str(document.page_content or ""),
            metadata=metadata,
            retrieval_rank=int(retrieval_rank),
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FrozenDocument":
        return cls(
            document_id=str(payload.get("document_id", "")),
            chunk_id=str(payload.get("chunk_id", "")),
            content_hash=(str(payload["content_hash"]) if payload.get("content_hash") else None),
            page_content=str(payload.get("page_content", "")),
            metadata=dict(payload.get("metadata") or {}),
            retrieval_rank=int(payload.get("retrieval_rank", 0)),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FrozenRetrievalCase:
    question_id: str
    question: str
    retrieval_snapshot_id: str
    retrieval_config: dict[str, Any]
    index_fingerprint: dict[str, Any]
    documents: tuple[FrozenDocument, ...]
    retrieval_profile: dict[str, Any]
    retrieval_timing: dict[str, Any]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FrozenRetrievalCase":
        return cls(
            question_id=str(payload["question_id"]),
            question=str(payload["question"]),
            retrieval_snapshot_id=str(payload.get("retrieval_snapshot_id", "")),
            retrieval_config=dict(payload.get("retrieval_config") or {}),
            index_fingerprint=dict(payload.get("index_fingerprint") or {}),
            documents=tuple(FrozenDocument.from_dict(item) for item in payload.get("documents", [])),
            retrieval_profile=dict(payload.get("retrieval_profile") or {}),
            retrieval_timing=dict(payload.get("retrieval_timing") or {}),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question": self.question,
            "retrieval_snapshot_id": self.retrieval_snapshot_id,
            "retrieval_config": self.retrieval_config,
            "index_fingerprint": self.index_fingerprint,
            "documents": [document.as_dict() for document in self.documents],
            "retrieval_profile": self.retrieval_profile,
            "retrieval_timing": self.retrieval_timing,
        }


def write_cases(path: str | Path, cases: Iterable[FrozenRetrievalCase]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for case in cases:
            handle.write(json.dumps(case.as_dict(), ensure_ascii=False, default=_json_default) + "\n")


def load_cases(path: str | Path) -> dict[str, FrozenRetrievalCase]:
    cases: dict[str, FrozenRetrievalCase] = {}
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                case = FrozenRetrievalCase.from_dict(json.loads(line))
            except Exception as exc:
                raise ValueError(f"无法解析冻结快照第 {line_number} 行：{exc}") from exc
            cases[case.question_id] = case
    return cases


@dataclass(frozen=True)
class EvidenceItem:
    citation_id: str
    document_id: str
    chunk_id: str
    content_hash: str | None
    page_content: str
    source: str
    title: str
    page: Any
    retrieval_rank: int
    retrieval_evidence_status: str
    metadata: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidencePack:
    question_id: str
    question: str
    snapshot_id: str
    items: tuple[EvidenceItem, ...]

    @property
    def allowed_citations(self) -> tuple[str, ...]:
        return tuple(item.citation_id for item in self.items)

    def context_text(self) -> str:
        if not self.items:
            return "（无可用本地证据）"
        sections = [
            "本次允许引用：" + "、".join(self.allowed_citations),
            "只能使用以下证据回答；没有证据支持的内容必须明确说明无法确认。",
        ]
        for item in self.items:
            page = f"，第 {int(item.page) + 1} 页" if isinstance(item.page, int) else ""
            sections.append(
                f"[{item.citation_id}] 标题：{item.title}\n"
                f"来源：{item.source}{page}\n"
                f"原始检索排名：{item.retrieval_rank}\n"
                f"证据状态：{item.retrieval_evidence_status}\n"
                f"{item.page_content}"
            )
        return "\n\n---\n\n".join(sections)

    def as_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question": self.question,
            "snapshot_id": self.snapshot_id,
            "allowed_citations": list(self.allowed_citations),
            "items": [item.as_dict() for item in self.items],
            "context_text": self.context_text(),
        }


def build_evidence_pack(
    case: FrozenRetrievalCase,
    max_items: int = 5,
    max_chars_per_item: int | None = None,
) -> EvidencePack:
    """Deduplicate while preserving frozen retrieval order exactly.

    ``max_chars_per_item`` caps how much of each chunk reaches the model.  It
    exists because the two levers are not interchangeable: raising
    ``max_items`` adds evidence but multiplies context length, and measured
    calls with a ~13k-char context sat close enough to the ceiling that provider
    variance pushed one over it.  Cutting characters keeps every chunk in play
    while shrinking the prompt.

    The cut happens here, on ``page_content``, rather than when rendering the
    prompt.  That keeps the model and the auditor looking at the same evidence:
    truncating only the rendered prompt would leave the auditor treating text
    the model never saw as "supporting", so a number lifted from the unseen tail
    would be scored as grounded.

    ``None`` (the default) means no truncation, so recorded runs keep their
    original meaning.
    """
    selected: list[EvidenceItem] = []
    seen: set[str] = set()
    limit = None if max_chars_per_item is None else max(0, int(max_chars_per_item))
    for document in case.documents:
        identity = document.chunk_id or document.content_hash or document.document_id
        if identity in seen:
            continue
        seen.add(identity)
        if len(selected) >= max(0, int(max_items)):
            break
        metadata = dict(document.metadata)
        source = str(metadata.get("source") or metadata.get("parent_document") or "未知来源")
        content = document.page_content
        if limit is not None and len(content) > limit:
            content = content[:limit]
            metadata["context_truncated"] = True
            metadata["context_original_chars"] = len(document.page_content)
        selected.append(EvidenceItem(
            citation_id=f"L{len(selected) + 1}",
            document_id=document.document_id,
            chunk_id=document.chunk_id,
            content_hash=document.content_hash,
            page_content=content,
            source=source,
            title=str(metadata.get("title") or metadata.get("source_title") or source),
            page=metadata.get("page"),
            retrieval_rank=document.retrieval_rank,
            retrieval_evidence_status=str(metadata.get("retrieval_evidence_status", "unknown")),
            metadata=metadata,
        ))
    return EvidencePack(
        question_id=case.question_id,
        question=case.question,
        snapshot_id=case.retrieval_snapshot_id,
        items=tuple(selected),
    )


def soft_audit(
    answer: str,
    pack: EvidencePack,
    expected_answer_spans: Iterable[dict[str, Any]] = (),
    expected_sources: Iterable[str] = (),
    required_terms: Iterable[Any] = (),
    refusal_requirements: Iterable[Any] = (),
    ambiguity_requirements: Iterable[Any] = (),
    required_hops: Iterable[Any] = (),
) -> dict[str, Any]:
    """Perform non-blocking, deterministic answer checks."""
    answer = str(answer or "")
    allowed = set(pack.allowed_citations)
    used = sorted(set(_CITATION_RE.findall(answer)), key=lambda value: (value[0], int(value[1:])))
    invalid = sorted(set(used) - allowed, key=lambda value: (value[0], int(value[1:])))
    item_by_citation = {item.citation_id: item for item in pack.items}
    cited_sources = {item.source for citation in used if (item := item_by_citation.get(citation))}
    expected_source_list = [str(source) for source in expected_sources if str(source)]
    source_hits = [source for source in expected_source_list if any(_source_matches(source, cited) for cited in cited_sources)]

    span_results = []
    for expected in expected_answer_spans:
        text = str(expected.get("text", ""))
        matched = _span_matches(text, answer)
        span_results.append({
            "id": expected.get("id"),
            "text": text,
            "matched": matched,
            "source": expected.get("source"),
        })
    def _term_groups(groups: Iterable[Any]) -> list[Any]:
        """Normalise a requirement list.

        A group is either a list of literal alternatives (legacy form) or a
        dict declaring a composite predicate, e.g.
        ``{"negation": [...], "object": [...]}``.
        """
        if isinstance(groups, dict):
            # A single predicate may be supplied without the outer list.
            groups = [groups]
        normalised = []
        for group in groups:
            if isinstance(group, dict):
                normalised.append({
                    "negation": [str(t) for t in (group.get("negation") or ()) if str(t).strip()],
                    "object": [str(t) for t in (group.get("object") or ()) if str(t).strip()],
                })
            else:
                normalised.append([str(term) for term in group if str(term).strip()])
        return normalised

    def _match_groups(groups: Iterable[Any]) -> list[dict[str, Any]]:
        results = []
        answer_normalised = _normalise_text(_strip_span_noise(answer))

        def _hit(terms: Iterable[str]) -> list[str]:
            return [
                term for term in terms
                if (needle := _normalise_text(_strip_span_noise(term))) and needle in answer_normalised
            ]

        for group in _term_groups(groups):
            if isinstance(group, dict):
                # Composite predicate.  ``negation``/``object`` fall back to the
                # built-in families when a contract omits one side, so a
                # half-specified predicate still behaves predictably.
                negation_terms = group["negation"] or list(_BEHAVIOUR_NEGATIONS)
                object_terms = group["object"] or list(_BEHAVIOUR_OBJECTS)
                negation_hits = _hit(negation_terms)
                object_hits = _hit(object_terms)
                results.append({
                    "kind": "composite",
                    "negation": negation_terms,
                    "object": object_terms,
                    "negation_hits": negation_hits,
                    "object_hits": object_hits,
                    "matched": bool(negation_hits) and bool(object_hits),
                })
            else:
                hits = _hit(group)
                results.append({
                    "kind": "alternatives",
                    "alternatives": group,
                    "hits": hits,
                    "matched": bool(hits),
                })
        return results

    required_term_results = _match_groups(required_terms)
    required_term_recall = (
        round(sum(bool(item["matched"]) for item in required_term_results) / len(required_term_results), 3)
        if required_term_results else None
    )
    refusal_requirement_results = _match_groups(refusal_requirements)
    ambiguity_requirement_results = _match_groups(ambiguity_requirements)
    number_patterns = re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?\s*(?:mm|mL|℃|%|年|岁|页)?", answer, flags=re.I)
    context_text = " ".join(item.page_content for item in pack.items)
    context_numbers = set(re.findall(r"\d+(?:\.\d+)?", context_text))
    refusal_markers = ("无法", "不能", "没有", "未找到", "无法确认", "缺少", "不具备", "未提供")
    unsupported_numbers = []
    for match in re.finditer(r"(?<![A-Za-z])\d+(?:\.\d+)?\s*(?:mm|mL|℃|%|年|岁|页)?", answer, flags=re.I):
        number = match.group(0).strip()
        numeric = re.search(r"\d+(?:\.\d+)?", number).group(0)
        if numeric not in context_numbers and not _has_refusal_context(answer, match.start(), match.end()):
            unsupported_numbers.append(number)
    warnings: list[dict[str, Any]] = []
    if invalid:
        warnings.append({"status": "citation_invalid", "citations": invalid})
    citation_status = "fail" if invalid else ("pass" if used else "not_applicable")
    if not used and pack.items and answer and not any(marker in answer for marker in refusal_markers):
        warnings.append({"status": "citation_missing", "reason": "回答包含内容但没有引用"})
    if not pack.items and answer and not re.search(r"无法|没有|不足|不能确认", answer):
        warnings.append({"status": "answer_without_evidence", "reason": "无证据时没有明确说明无法确认"})
    if unsupported_numbers:
        warnings.append({"status": "unsupported_number", "numbers": unsupported_numbers})
    length_limit_exceeded = len(answer) > 800
    if length_limit_exceeded:
        warnings.append({"status": "answer_too_long", "limit": 800, "actual": len(answer)})
    if expected_source_list and not source_hits:
        warnings.append({"status": "source_not_cited", "expected_sources": expected_source_list})
    # Behaviour verdicts describe what the system *chose to do*.  A fallback
    # substitute is not a choice, so the honest verdict is "not judged", not
    # "failed".  Recording False here let a provider timeout -- an availability
    # problem -- enter the safety metric as a behaviour failure: a question
    # that was never answered was reported as "failed to refuse".  That also
    # made the question look flaky across runs (True/True/False on identical
    # inputs), when the third run simply produced no answer.
    answer_is_substitute = is_fallback_answer(answer)
    answer_present = bool(answer.strip()) and not answer_is_substitute
    refusal_correctness = None
    if answer_present:
        if refusal_requirement_results:
            refusal_correctness = all(item["matched"] for item in refusal_requirement_results)
        elif not expected_source_list:
            refusal_correctness = bool(any(marker in answer for marker in refusal_markers))
    ambiguity_safety = None
    if answer_present and ambiguity_requirement_results:
        ambiguity_safety = all(item["matched"] for item in ambiguity_requirement_results)
    # The previous version carried a ``question_id == "q17_ambiguous"`` branch
    # here.  A per-question branch makes the audit specific to one dataset
    # instead of encoding the rule the dataset is meant to test, so it has been
    # removed: the contract now states the requirement explicitly.

    # Boundary questions (refusal / ambiguity) exercise policy behaviour, not
    # factual quoting.  Their citation usage must not be averaged into the
    # fact-question citation metrics, or a correct refusal that cites nothing
    # dilutes "citation validity 100%" into a meaningless number.
    #
    # Two ways to become a boundary question, because there are two ways the
    # contract expresses a behaviour:
    #   * an explicit requirement list -- recognised from the contract, which
    #     works even when no verdict could be produced;
    #   * no list at all, in which case a refusal is recognised from the answer
    #     text itself, so the boundary nature is only visible through the
    #     verdict that was reached.
    # The contract branch has to come first: deriving applicability solely from
    # the verdict meant a refusal question that timed out produced no verdict,
    # stopped being recognised as a boundary question, and entered the citation
    # metrics -- the opposite of what the flag is for.
    is_boundary_question = bool(
        refusal_requirement_results or ambiguity_requirement_results
    ) or refusal_correctness is not None or ambiguity_safety is not None
    citation_metric_applicable = not is_boundary_question

    # --- multi-hop coverage -------------------------------------------------
    # A multi-hop answer is drawn from several documents at once, so "did the
    # answer contain the expected spans" is not enough: it can quote the right
    # numbers while silently dropping a whole hop.  Each hop is therefore
    # scored on its own required terms, with the same predicate as
    # ``required_terms`` applied per hop instead of pooled -- otherwise one
    # hop's terms would satisfy another hop's check and every answer would look
    # complete.
    #
    # This measures whether a hop's fact was brought out.  It deliberately does
    # NOT judge whether the reasoning joining the hops is sound: judging
    # reasoning from a word list is exactly what made ``ambiguity_safety``
    # untrustworthy before v3, where the same behaviour scored 0 or 1 depending
    # on the wording.  Repeating that here would produce an equally unusable
    # number, so hop reasoning is left unmeasured rather than measured badly.
    hop_results: list[dict[str, Any]] = []
    for hop in required_hops:
        if not isinstance(hop, dict):
            continue
        hop_term_results = _match_groups(hop.get("required_terms") or [])
        hop_span = str(hop.get("expected_span") or "")
        span_matched = _span_matches(hop_span, answer) if hop_span else None
        # A hop with declared terms must match all of them; a hop with a
        # declared span must also bring that span out.  ``span_matched is not
        # False`` keeps hops that declare no span judgeable on terms alone.
        terms_matched = bool(hop_term_results) and all(
            item["matched"] for item in hop_term_results
        )
        hop_results.append({
            "hop_id": str(hop.get("hop_id") or ""),
            "from_question": str(hop.get("from_question") or ""),
            "source_chunk_id": str(hop.get("source_chunk_id") or ""),
            "expected_span": hop_span,
            "span_matched": span_matched,
            "terms": hop_term_results,
            "matched": terms_matched and span_matched is not False,
        })
    hop_recall: float | None = None
    hop_metric_applicable = False
    # Applicability is decided from the *contract*, not from whether a verdict
    # was reached.  Declaring `required_hops` is itself the statement that this
    # is a multi-hop question; a question that additionally declares a refusal
    # or ambiguity requirement is exercising policy instead, and a refusal
    # cannot cover hops by construction.
    #
    # Reusing `is_boundary_question` here would be wrong: part of that flag
    # comes from a word-list verdict that fires for any question without
    # declared sources, so a plain factual answer would be classed as a
    # boundary question and silently lose its hop score.
    declares_boundary = bool(refusal_requirement_results or ambiguity_requirement_results)
    if hop_results and answer_present and not declares_boundary:
        hop_recall = round(
            sum(1 for item in hop_results if item["matched"]) / len(hop_results), 3
        )
        hop_metric_applicable = True

    return {
        "allowed_citations": sorted(allowed, key=lambda value: (value[0], int(value[1:]))),
        "used_citations": used,
        "invalid_citations": invalid,
        "citation_status": citation_status,
        "citation_validity": True if citation_status == "pass" else False if citation_status == "fail" else None,
        "citation_metric_applicable": citation_metric_applicable,
        # Number of allowed citation ids actually referenced.  This is *not*
        # claim-level coverage: citing [L1] does not prove every sentence is
        # supported.  Renamed so it cannot be mistaken for faithfulness.
        "citation_id_usage_ratio": round(len(used) / len(allowed), 3) if allowed else None,
        "expected_answer_spans": span_results,
        "expected_answer_span_recall": round(
            sum(bool(item["matched"]) for item in span_results) / len(span_results), 3
        ) if span_results else None,
        "required_terms": required_term_results,
        "required_term_recall": required_term_recall,
        "refusal_requirements": refusal_requirement_results,
        "ambiguity_requirements": ambiguity_requirement_results,
        "span_metric_applicable": not is_boundary_question,
        "expected_sources": expected_source_list,
        "cited_sources": sorted(cited_sources),
        "source_coverage": round(len(source_hits) / len(expected_source_list), 3) if expected_source_list else 1.0,
        "unsupported_numbers": unsupported_numbers,
        "unsupported_number_count": len(unsupported_numbers),
        "unsupported_claim_count": len(invalid) + len(unsupported_numbers),
        "refusal_correctness": refusal_correctness,
        "ambiguity_safety": ambiguity_safety,
        "hop_results": hop_results,
        "hop_recall": hop_recall,
        "hop_metric_applicable": hop_metric_applicable,
        "answer_length": len(answer),
        "length_limit_exceeded": length_limit_exceeded,
        "warnings": warnings,
    }
