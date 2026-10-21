"""Translate between live retrieval output and the frozen evidence pack.

Why this exists
---------------
The app generates from ``list[Document]`` -- that is the signature of the legacy
chain (``src/generator.py::generate_answer``).  The offline evaluation generates
from an ``EvidencePack`` (``src/generator_v2.py::generate_from_pack``).

To compare the two chains you must show them **the same evidence**, otherwise a
difference in the score could be a difference in what they were shown rather
than in how they answered.  That requires a translation, in both directions:

* ``pack_from_documents`` -- live retrieval into a pack, so the app can run the
  v2 chain without the evaluation machinery losing its meaning;
* ``documents_from_pack`` -- a recorded pack back into Documents, so a recorded
  run can be replayed through the legacy chain;
* ``legacy_context`` -- the pack rendered the way the legacy chain renders
  retrieved documents, which is *not* the same string as
  ``EvidencePack.context_text()``.

Nothing here touches the retriever, the index, the frozen snapshot, or any
``RETRIEVER_*`` setting.  It is a pure data-shape adapter.
"""

from __future__ import annotations

from typing import Any, Iterable

from langchain_core.documents import Document

from src.frozen_evidence import (
    EvidencePack,
    FrozenDocument,
    FrozenRetrievalCase,
    build_evidence_pack,
)


def documents_from_pack(pack: EvidencePack) -> list[Document]:
    """Render a recorded pack as the Documents the legacy chain expects.

    ``EvidenceItem`` keeps the *whole* original metadata dict, so the fields the
    legacy renderer reads (``section``, ``source_category``, ``evidence_level``,
    ``population``, ``material_grade``, ``applicability``,
    ``retrieval_evidence_reason``) survive the round trip.  The fields that
    ``build_evidence_pack`` lifted out of that dict are written back, because a
    live document carries them in metadata and the legacy renderer reads them
    from there.

    Fresh ``Document`` objects are returned on every call: the legacy renderer
    assigns citation ids by *mutating* metadata, so handing it a shared object
    would make the second call see the first call's numbering.
    """
    documents: list[Document] = []
    for item in pack.items:
        metadata: dict[str, Any] = dict(item.metadata)
        metadata.update({
            "document_id": item.document_id,
            "chunk_id": item.chunk_id,
            "source": item.source,
            "title": item.title,
            "page": item.page,
            "retrieval_rank": item.retrieval_rank,
            "retrieval_evidence_status": item.retrieval_evidence_status,
        })
        if item.content_hash:
            metadata["content_hash"] = item.content_hash
        # The legacy renderer numbers citations by source type: local -> L,
        # web -> W.  Frozen evidence is always local, but the key must exist so
        # the numbering does not depend on a missing field's default.
        metadata.setdefault("source_type", "local")
        documents.append(Document(page_content=item.page_content, metadata=metadata))
    return documents


def pack_from_documents(
    question: str,
    documents: Iterable[Document],
    *,
    max_items: int = 5,
    max_chars_per_item: int | None = None,
    question_id: str = "",
    snapshot_id: str = "live",
) -> EvidencePack:
    """Build a pack from live retrieval, so the app can use the v2 chain.

    ``retrieval_config``/``index_fingerprint``/``retrieval_profile``/
    ``retrieval_timing`` are left empty on purpose.  A pack built from live
    retrieval is **not** a frozen snapshot and must not be mistaken for one:
    those four fields are what makes a recorded run reproducible, and inventing
    plausible values here would let an unreproducible pack masquerade as a
    recorded one.
    """
    frozen = tuple(
        FrozenDocument.from_document(document, rank)
        for rank, document in enumerate(documents, 1)
    )
    case = FrozenRetrievalCase(
        question_id=str(question_id),
        question=str(question),
        retrieval_snapshot_id=str(snapshot_id),
        retrieval_config={},
        index_fingerprint={},
        documents=frozen,
        retrieval_profile={},
        retrieval_timing={},
    )
    return build_evidence_pack(
        case, max_items=max_items, max_chars_per_item=max_chars_per_item
    )


def legacy_context(pack: EvidencePack) -> str:
    """Render a pack the way the legacy chain renders retrieved documents.

    Imported lazily: ``src/generator`` pulls in the chat model client, and this
    module is also used on paths that only need the data shapes.
    """
    from src.generator import format_context

    return format_context(documents_from_pack(pack))
