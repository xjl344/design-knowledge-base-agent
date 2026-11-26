"""Shared helpers and compatibility bridge for the modular retrieval package."""

from __future__ import annotations

import sys
from typing import Any, TypeVar

from langchain_core.documents import Document

import config as _config

T = TypeVar("T")


def active_settings() -> Any:
    """Return the active settings object, honouring monkeypatches on ``src.retriever``."""
    facade = sys.modules.get("src.retriever")
    if facade is not None and hasattr(facade, "settings"):
        return getattr(facade, "settings")
    return _config.settings


def facade_attr(name: str, default: T) -> T:
    """Resolve an attribute through ``src.retriever`` when loaded so test patches work."""
    facade = sys.modules.get("src.retriever")
    if facade is not None and hasattr(facade, name):
        return getattr(facade, name)
    return default


def document_key(document: Document) -> str:
    return str(
        document.metadata.get("chunk_id")
        or "|".join(
            (
                str(document.metadata.get("source", "")),
                str(document.metadata.get("page", "")),
                document.page_content[:200],
            )
        )
    )


def clone_documents(documents: list[Document]) -> list[Document]:
    return [
        Document(page_content=doc.page_content, metadata=dict(doc.metadata))
        for doc in documents
    ]
