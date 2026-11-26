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


_MD_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_MD_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}\s*(?:\|\s*:?-{2,}\s*)*\|?\s*$")


def _is_markdown_document(document: Document) -> bool:
    source = str(document.metadata.get("source", "")).lower()
    return source.endswith(".md")


def _split_markdown_table(table_text: str, max_chars: int) -> list[str]:
    """Split an oversized Markdown table by rows while repeating its header."""
    lines = [line for line in table_text.splitlines() if line.strip()]
    if len(lines) <= 2 or len(table_text) <= max_chars:
        return [table_text]
    if len(lines) >= 2 and _MD_TABLE_SEP_RE.match(lines[1]):
        header = lines[:2]
        body = lines[2:]
    else:
        header = lines[:1]
        body = lines[1:]
    header_text = "\n".join(header)
    pieces: list[str] = []
    current = list(header)
    for row in body:
        candidate = "\n".join(current + [row])
        if len(current) > len(header) and len(candidate) > max_chars:
            pieces.append("\n".join(current))
            current = list(header) + [row]
        else:
            current.append(row)
    if len(current) > len(header) or not pieces:
        pieces.append("\n".join(current))
    return pieces


def _split_markdown_contextual(
    document: Document, chunk_size: int, chunk_overlap: int
) -> list[Document]:
    """Split a Markdown document along heading/table boundaries with breadcrumb context.

    Every chunk carries the document's H1 title and active section hierarchy so
    tables and deep subsections stay retrievable in bilingual/technical corpora
    instead of turning into contextless orphans.
    """
    text = (document.page_content or "").replace("\r\n", "\n")
    lines = text.split("\n")
    doc_title = ""
    for line in lines[:30]:
        match = _MD_HEADING_RE.match(line)
        if match and len(match.group(1)) == 1:
            doc_title = match.group(2).strip()
            break

    sections: list[tuple[str, int, list[tuple[str, str]]]] = []
    heading_stack: list[tuple[int, str]] = []
    current_blocks: list[tuple[str, str]] = []
    current_lines: list[str] = []
    in_table = False
    section_start = 0
    cursor = 0

    def active_path() -> str:
        titles: list[str] = []
        if doc_title:
            titles.append(doc_title)
        for _, title in heading_stack:
            if title and title not in titles:
                titles.append(title)
        return " > ".join(titles)

    def flush_block(kind: str) -> None:
        nonlocal current_lines
        body = "\n".join(current_lines).strip()
        if body:
            current_blocks.append((body, kind))
        current_lines = []

    def flush_section() -> None:
        nonlocal current_blocks
        flush_block("table" if in_table else "paragraph")
        if current_blocks:
            sections.append((active_path(), section_start, current_blocks))
            current_blocks = []

    for line in lines:
        line_len = len(line) + 1
        match = _MD_HEADING_RE.match(line)
        if match:
            flush_section()
            in_table = False
            level = len(match.group(1))
            title = match.group(2).strip()
            heading_stack = [(lvl, t) for lvl, t in heading_stack if lvl < level]
            heading_stack.append((level, title))
            section_start = cursor
            cursor += line_len
            continue
        is_table_line = bool(_TABLE_RE.match(line))
        if is_table_line and not in_table:
            flush_block("paragraph")
            in_table = True
        elif in_table and not is_table_line:
            flush_block("table")
            in_table = False
        if not is_table_line and not line.strip():
            flush_block("paragraph")
        else:
            current_lines.append(line)
        cursor += line_len
    flush_section()

    if not sections:
        return []

    body_splitter = RecursiveCharacterTextSplitter(
        chunk_size=max(200, chunk_size),
        chunk_overlap=min(chunk_overlap, max(0, chunk_size // 4)),
        separators=["\n\n", "\n", "。", "；", "！", "？", ". ", "，", " ", ""],
    )

    chunks: list[Document] = []
    for section_path, start_offset, blocks in sections:
        prefix = f"{section_path}\n\n" if section_path else ""
        budget = max(200, chunk_size - len(prefix))

        expanded_blocks: list[str] = []
        for block_text, kind in blocks:
            if len(block_text) <= budget:
                expanded_blocks.append(block_text)
            elif kind == "table":
                expanded_blocks.extend(_split_markdown_table(block_text, budget))
            else:
                local_splitter = RecursiveCharacterTextSplitter(
                    chunk_size=budget,
                    chunk_overlap=min(chunk_overlap, max(0, budget // 4)),
                    separators=["\n\n", "\n", "。", "；", "！", "？", ". ", "，", " ", ""],
                )
                expanded_blocks.extend(local_splitter.split_text(block_text))

        pending: list[str] = []
        piece_offset = start_offset

        def emit_piece(body_text: str) -> None:
            nonlocal piece_offset
            rendered = f"{prefix}{body_text}".strip()
            if not rendered:
                return
            meta = _metadata(document, "R", section_path)
            if doc_title:
                meta["title"] = doc_title
                meta["source_title"] = doc_title
            meta["start_index"] = piece_offset
            meta["end_index"] = piece_offset + len(rendered)
            chunks.append(Document(page_content=rendered, metadata=meta))
            piece_offset += len(body_text) + 2

        for block_text in expanded_blocks:
            candidate = "\n\n".join(pending + [block_text]) if pending else block_text
            if pending and len(candidate) > budget:
                emit_piece("\n\n".join(pending))
                pending = [block_text]
            else:
                pending.append(block_text)
        if pending:
            emit_piece("\n\n".join(pending))

    return chunks


def _recursive(documents: list[Document]) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        add_start_index=True,
        separators=["\n\n", "\n", "。", "；", "！", "？", "，", " ", ""],
    )
    chunks: list[Document] = []
    plain_docs: list[Document] = []
    for doc in documents:
        if _is_markdown_document(doc) and _MD_HEADING_RE.search(doc.page_content or ""):
            chunks.extend(_split_markdown_contextual(doc, settings.chunk_size, settings.chunk_overlap))
        else:
            plain_docs.append(doc)
    if plain_docs:
        plain_chunks = splitter.split_documents(plain_docs)
        for chunk in plain_chunks:
            chunk.metadata = _metadata(chunk, "R")
        chunks.extend(plain_chunks)
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
