"""Configurable, deterministic document chunking strategies."""

from __future__ import annotations

import re
import hashlib
from typing import Iterable

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import settings


def _metadata(document: Document, strategy: str, section_path: str = "") -> dict:
    metadata = dict(document.metadata)
    metadata["chunk_strategy"] = strategy
    metadata["chunk_version"] = "v1"
    if section_path:
        metadata["section_path"] = section_path
    return metadata


def _fixed(documents: list[Document]) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        add_start_index=True,
        separators=[""],
    )
    chunks = splitter.split_documents(documents)
    for chunk in chunks:
        chunk.metadata = _metadata(chunk, "F")
    return chunks


def _recursive(documents: list[Document]) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        add_start_index=True,
        separators=["\n\n", "\n", "。", "；", "！", "？", "，", " ", ""],
    )
    chunks = splitter.split_documents(documents)
    for chunk in chunks:
        chunk.metadata = _metadata(chunk, "R")
    return chunks


_HEADING_RE = re.compile(r"^\s{0,3}(?:#{1,6}\s+|第[^\n]{1,40}[章节条款]|[0-9]+(?:\.[0-9]+)*\s+).+")
_TABLE_RE = re.compile(r"^\s*\|.*\|\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _paragraph_blocks(text: str) -> list[tuple[str, str]]:
    """Return (block, kind), preserving fenced blocks and markdown tables."""
    lines = text.replace("\r\n", "\n").split("\n")
    blocks: list[tuple[str, str]] = []
    current: list[str] = []
    in_fence = False
    in_table = False

    def flush(kind: str = "paragraph") -> None:
        nonlocal current
        value = "\n".join(current).strip()
        if value:
            blocks.append((value, kind))
        current = []

    for line in lines:
        stripped = line.strip()
        if _FENCE_RE.match(line):
            if not in_fence:
                flush("paragraph")
                in_fence = True
            current.append(line)
            if len(current) > 1 and stripped.startswith(("```", "~~~")):
                in_fence = False
                flush("code")
            continue
        if in_fence:
            current.append(line)
            continue
        table_line = bool(_TABLE_RE.match(line))
        if table_line and not in_table:
            flush("paragraph")
            in_table = True
        if in_table and not table_line:
            flush("table")
            in_table = False
        if in_table:
            current.append(line)
            continue
        if not stripped:
            flush("paragraph")
        elif _HEADING_RE.match(line):
            flush("paragraph")
            blocks.append((stripped, "heading"))
        else:
            current.append(line)
    flush("code" if in_fence else "table" if in_table else "paragraph")
    return blocks


def _paragraph_semantic(documents: list[Document]) -> list[Document]:
    result: list[Document] = []
    max_size = settings.semantic_max_chunk_size
    min_size = settings.semantic_min_chunk_size
    for document in documents:
        blocks = _paragraph_blocks(document.page_content or "")
        section = ""
        pending: list[str] = []
        start_index = 0
        cursor = 0

        def emit(value: str, start: int) -> None:
            if not value.strip():
                return
            metadata = _metadata(document, "P", section)
            metadata.update({"start_index": start, "end_index": start + len(value)})
            # Keep a lightweight heading path in the embedded text so a query
            # can match section context even when metadata is not searchable.
            rendered = value.strip()
            if section and not rendered.startswith(section):
                rendered = f"{section}\n\n{rendered}"
            result.append(Document(page_content=rendered, metadata=metadata))

        for block, kind in blocks:
            if kind == "heading":
                if pending:
                    emit("\n\n".join(pending), start_index)
                    pending = []
                section = block
                start_index = cursor
                cursor += len(block) + 1
                continue
            candidate = "\n\n".join(pending + [block]) if pending else block
            if pending and len(candidate) > max_size:
                emit("\n\n".join(pending), start_index)
                start_index = cursor
                pending = [block]
            else:
                pending.append(block)
            cursor += len(block) + 2
            if kind in {"table", "code"} or len("\n\n".join(pending)) >= max_size:
                emit("\n\n".join(pending), start_index)
                pending = []
                start_index = cursor
        if pending:
            value = "\n\n".join(pending)
            if result and len(value) < min_size and result[-1].metadata.get("section_path") == section:
                previous = result[-1]
                combined = previous.page_content + "\n\n" + value
                if len(combined) <= max_size:
                    previous.page_content = combined
                    previous.metadata["end_index"] = start_index + len(value)
                    continue
            emit(value, start_index)
    return result


def _split_oversized_semantic_chunks(chunks: list[Document]) -> list[Document]:
    """Bound P chunks while preserving section metadata and source offsets."""
    limit = settings.semantic_max_chunk_size
    result: list[Document] = []
    boundaries = re.compile(r"(?<=[。！？；!?;])\s*|\n{2,}|\n")
    for chunk in chunks:
        text = chunk.page_content or ""
        if len(text) <= limit:
            result.append(chunk)
            continue
        pieces: list[str] = []
        current = ""
        for sentence in (part.strip() for part in boundaries.split(text) if part.strip()):
            if current and len(current) + 1 + len(sentence) > limit:
                pieces.append(current)
                current = sentence
            elif len(sentence) > limit:
                if current:
                    pieces.append(current)
                    current = ""
                pieces.extend(sentence[index:index + limit] for index in range(0, len(sentence), limit))
            else:
                current = f"{current}\n{sentence}" if current else sentence
        if current:
            pieces.append(current)
        base_start = int(chunk.metadata.get("start_index", 0) or 0)
        offset = 0
        for piece in pieces:
            metadata = dict(chunk.metadata)
            metadata["start_index"] = base_start + offset
            metadata["end_index"] = base_start + offset + len(piece)
            result.append(Document(page_content=piece, metadata=metadata))
            offset += len(piece) + 1
    return result


def chunk_statistics(chunks: Iterable[Document]) -> dict[str, float | int]:
    values = [len(chunk.page_content or "") for chunk in chunks]
    if not values:
        return {"chunk_count": 0, "mean_chars": 0.0, "p95_chars": 0}
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))
    return {
        "chunk_count": len(values),
        "mean_chars": round(sum(values) / len(values), 2),
        "p95_chars": ordered[index],
    }


def split_documents(documents: Iterable[Document], strategy: str | None = None) -> list[Document]:
    strategy = (strategy or settings.chunking_strategy).upper()
    if strategy == "V":
        raise NotImplementedError("V vector-semantic chunking is reserved for a later iteration")
    if strategy not in {"F", "R", "P"}:
        raise ValueError("chunking strategy must be F, R, P, or reserved V")
    source = list(documents)
    if strategy == "F":
        chunks = _fixed(source)
    elif strategy == "P":
        chunks = _split_oversized_semantic_chunks(_paragraph_semantic(source))
    else:
        chunks = _recursive(source)
    for chunk in chunks:
        metadata = chunk.metadata
        start = int(metadata.get("start_index", 0) or 0)
        metadata.setdefault("parent_document", metadata.get("source", ""))
        metadata.setdefault("start_index", start)
        metadata.setdefault("end_index", start + len(chunk.page_content or ""))
        identity = "\x1f".join((
            str(metadata.get("source", "")),
            str(metadata.get("page", "")),
            str(metadata.get("chunk_strategy", strategy)),
            str(metadata.get("chunk_version", "v1")),
            str(metadata.get("start_index", "")),
            chunk.page_content or "",
        ))
        metadata.setdefault("chunk_id", hashlib.sha256(identity.encode("utf-8")).hexdigest())
    return chunks
