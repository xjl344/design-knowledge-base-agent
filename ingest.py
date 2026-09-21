"""Load PDF/TXT/MD documents and maintain the local Chroma collection.

Default mode is incremental: only new/changed files are embedded and chunks
of removed files are dropped, tracked by a JSON manifest (ingest_manifest.json
next to the Chroma store). Use --full to wipe and rebuild everything.

Optional: garbled PDF text layers are recovered via OCR (rapidocr_onnxruntime).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import warnings
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from config import ensure_runtime_dirs, settings
from langchain_chroma import Chroma
from langchain_core.documents import Document
import pymupdf
from tqdm.auto import tqdm

from src.embeddings import get_embeddings, resolve_embedding_device
from src.document_metadata import build_document_metadata
from src.chunking import chunk_statistics, split_documents as split_documents_with_strategy


SUPPORTED_SUFFIXES = {".pdf", ".txt", ".md"}
COLLECTION_CONFIGURATION = {
    "hnsw": {
        "space": "cosine",
        # This MVP has only a few thousand chunks. Keeping them in Chroma's
        # brute-force buffer avoids the broken native HNSW persistence path on
        # Windows while remaining fast enough for local retrieval.
        "batch_size": 10_000,
        "sync_threshold": 100_000,
    }
}

# Garbled-text guard --------------------------------------------------------
# Some third-party PDF downloads (e.g. the "GBT+..." files) embed fonts
# without a usable ToUnicode CMap, so PyMuPDF extracts their pages as
# ASCII/Latin-1 symbol soup ('!"#!"!#$', '4! 4% 4!-') instead of Chinese.
# Such pages are worthless for retrieval and must not enter the index.
GARBLED_MIN_TEXT_LEN = 20  # below this length, do not judge a page
GARBLED_MIN_CJK_COUNT = 1  # any real CJK content means the page is not mojibake
ENGLISH_LETTER_RATIO_KEEP = 0.55  # mostly-ASCII-letter pages count as English


def _cjk_ratio(text: str) -> float:
    """Share of CJK unified ideographs in *text* (0.0 for empty/garbage)."""
    if not text:
        return 0.0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk / len(text)


def _looks_garbled(text: str) -> bool:
    """True when extracted page text is mojibake rather than real content."""
    if len(text) < GARBLED_MIN_TEXT_LEN:
        return False
    if sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff") >= GARBLED_MIN_CJK_COUNT:
        return False
    # Genuinely English pages are mostly ASCII letters; mojibake is not.
    letters = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    return letters / len(text) < ENGLISH_LETTER_RATIO_KEEP


# OCR fallback --------------------------------------------------------------
# Pages whose text layer is unusable (garbled) are rendered to an image and
# OCR'd with RapidOCR (bundled PP-OCR Chinese models, ONNX runtime). Only when
# OCR is unavailable or returns nothing usable is the page skipped.
# Install the engine with:  pip install rapidocr_onnxruntime
OCR_FALLBACK_ENABLED = True  # set False to disable OCR and only skip pages
OCR_DPI = 200  # render resolution for OCR (higher = slower but more accurate)
OCR_MIN_TEXT_LEN = 20  # same floor as the garbled guard
# Set True to also OCR pages with NO text layer at all (scanned books).
# This recovers e.g. 设计思维手册/工业设计基础, at the cost of extra OCR
# time for every empty page of every PDF during ingestion.
OCR_EMPTY_PAGES = False

_ocr_engine: object | None | bool = None  # None = untried, False = unavailable


def _get_ocr_engine():
    """Return a lazily-initialized RapidOCR engine, or None if unavailable."""
    global _ocr_engine
    if _ocr_engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR

            _ocr_engine = RapidOCR()
        except Exception:
            _ocr_engine = False
    return _ocr_engine or None


def _ocr_page(page) -> str | None:
    """Render *page* to an image, OCR it, and return the text (or None)."""
    if not OCR_FALLBACK_ENABLED:
        return None
    engine = _get_ocr_engine()
    if engine is None:
        return None
    try:
        pix = page.get_pixmap(dpi=OCR_DPI)
        out = engine(pix.tobytes("png"))
        result = out[0] if isinstance(out, tuple) else out
    except Exception:
        return None
    if not result:
        return None
    text = "\n".join(item[1] for item in result).strip()
    return text if len(text) >= OCR_MIN_TEXT_LEN else None


def discover_files(source_dir: Path) -> list[Path]:
    if not source_dir.is_dir():
        return []
    return sorted(
        path
        for path in source_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )


def _load_one(path: Path, source_dir: Path) -> list[Document]:
    """Load one file into page-level documents (with metadata attached)."""
    relative_source = path.relative_to(source_dir).as_posix()
    if path.suffix.lower() == ".pdf":
        loaded = []
        skipped_garbled = 0
        recovered_ocr = 0
        with pymupdf.open(path) as pdf:
            for page_number, page in enumerate(pdf):
                text = page.get_text().strip()
                if not text:
                    if OCR_EMPTY_PAGES:
                        ocr_text = _ocr_page(page)
                        if ocr_text and not _looks_garbled(ocr_text):
                            recovered_ocr += 1
                            loaded.append(
                                Document(
                                    page_content=ocr_text,
                                    metadata={"page": page_number, "ocr": True},
                                )
                            )
                            continue
                    continue
                if _looks_garbled(text):
                    ocr_text = _ocr_page(page)
                    if ocr_text and not _looks_garbled(ocr_text):
                        recovered_ocr += 1
                        loaded.append(
                            Document(
                                page_content=ocr_text,
                                metadata={"page": page_number, "ocr": True},
                            )
                        )
                        continue
                    skipped_garbled += 1
                    continue
                loaded.append(
                    Document(page_content=text, metadata={"page": page_number})
                )
        if recovered_ocr:
            warnings.warn(
                f"Recovered {recovered_ocr} page(s) in {path.name} via OCR "
                f"(text layer was unusable).",
                RuntimeWarning,
                stacklevel=2,
            )
        if skipped_garbled:
            warnings.warn(
                f"Skipped {skipped_garbled} garbled page(s) in {path.name}: "
                f"the PDF text layer has no usable CJK mapping (fewer than "
                f"{GARBLED_MIN_CJK_COUNT} CJK chars/page) and OCR was "
                f"unavailable or returned nothing usable. Re-download the "
                f"official PDF or OCR the pages to recover the content.",
                RuntimeWarning,
                stacklevel=2,
            )
    else:
        loaded = [Document(page_content=_read_text(path))]

    for document in loaded:
        page = document.metadata.get("page")
        inferred = build_document_metadata(
            relative_source,
            path.stem,
            document.page_content,
            page if isinstance(page, int) else None,
        )
        inferred.update(document.metadata)
        inferred["source"] = relative_source
        inferred["source_type"] = "local"
        document.metadata = inferred
    return loaded


def load_documents(source_dir: Path) -> list[Document]:
    documents: list[Document] = []
    for path in tqdm(discover_files(source_dir), desc="Loading files", unit="file"):
        documents.extend(_load_one(path, source_dir))
    return documents


def _read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeError(f"Unable to decode text file: {path}")


def split_documents(documents: Iterable[Document]) -> list[Document]:
    settings.validate_chunking()
    chunks = split_documents_with_strategy(documents)
    for chunk in tqdm(chunks, desc="Preparing chunks", unit="chunk"):
        chunk.metadata["chunk_id"] = make_chunk_id(chunk)
    return chunks


def make_chunk_id(document: Document) -> str:
    identity = "\x1f".join(
        (
            str(document.metadata.get("source", "")),
            str(document.metadata.get("page", "")),
            str(document.metadata.get("chunk_strategy", settings.chunking_strategy)),
            str(document.metadata.get("chunk_version", "v1")),
            str(document.metadata.get("start_index", "")),
            document.page_content,
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def rebuild_vectorstore(
    chunks: list[Document],
    persist_dir: Path,
    collection_name: str,
) -> int:
    if not chunks:
        raise ValueError("No chunks to ingest; the existing collection was not changed")

    persist_dir = persist_dir.resolve()
    persist_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = persist_dir.with_name(
        f".{persist_dir.name}.rebuild-{uuid4().hex}"
    )

    try:
        count = _build_vectorstore(chunks, staging_dir, collection_name)
        _validate_vectorstore(staging_dir, collection_name, len(chunks))
        _activate_rebuilt_store(staging_dir, persist_dir)
        return count
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)


def _build_vectorstore(
    chunks: list[Document],
    persist_dir: Path,
    collection_name: str,
) -> int:
    import chromadb

    persist_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(persist_dir))
    try:
        vectorstore = Chroma(
            client=client,
            collection_name=collection_name,
            embedding_function=get_embeddings(show_progress=True),
            collection_configuration=COLLECTION_CONFIGURATION,
        )
        ids = [str(chunk.metadata["chunk_id"]) for chunk in chunks]
        vectorstore.add_documents(chunks, ids=ids)
        count = vectorstore._collection.count()
    finally:
        client.close()

    if count != len(chunks):
        raise RuntimeError(
            f"Chroma stored {count} chunks, but {len(chunks)} were expected"
        )
    return count


def _validate_vectorstore(
    persist_dir: Path,
    collection_name: str,
    expected_count: int,
) -> None:
    """Reopen the store and query it so a broken persisted HNSW index is rejected."""
    import chromadb

    with chromadb.PersistentClient(path=str(persist_dir)) as client:
        collection = client.get_collection(collection_name)
        count = collection.count()
        if count != expected_count:
            raise RuntimeError(
                f"Chroma validation found {count} chunks; expected {expected_count}"
            )

        sample = collection.get(limit=1, include=["embeddings"])
        embeddings = sample.get("embeddings")
        if embeddings is None or len(embeddings) == 0:
            raise RuntimeError("Chroma validation could not read a stored embedding")

        query_embedding = embeddings[0]
        if hasattr(query_embedding, "tolist"):
            query_embedding = query_embedding.tolist()
        result = collection.query(
            query_embeddings=[query_embedding],
            n_results=1,
            include=["distances"],
        )
        if not result.get("ids") or not result["ids"][0]:
            raise RuntimeError("Chroma validation query returned no result")


def _activate_rebuilt_store(staging_dir: Path, persist_dir: Path) -> None:
    backup_dir = persist_dir.with_name(
        f".{persist_dir.name}.backup-{uuid4().hex}"
    )
    original_moved = False

    try:
        if persist_dir.exists():
            persist_dir.replace(backup_dir)
            original_moved = True
        staging_dir.replace(persist_dir)
    except OSError as exc:
        if original_moved and not persist_dir.exists():
            try:
                backup_dir.replace(persist_dir)
            except OSError as rollback_exc:
                raise RuntimeError(
                    "Failed to activate the rebuilt Chroma database and failed to "
                    f"restore the previous database from {backup_dir}"
                ) from rollback_exc
        raise RuntimeError(
            "Unable to replace the Chroma database. Stop the running app and retry "
            "the ingestion command."
        ) from exc

    if backup_dir.exists():
        try:
            shutil.rmtree(backup_dir)
        except OSError as exc:
            warnings.warn(
                f"The rebuilt database is active, but the old backup could not be "
                f"removed: {backup_dir} ({exc})",
                RuntimeWarning,
                stacklevel=2,
            )


# Incremental ingestion -----------------------------------------------------
# Tracks per-file content hashes in a JSON manifest next to the Chroma store,
# so a normal run only embeds new/changed files and drops chunks of files
# that were removed from the documents directory.
MANIFEST_FILENAME = "ingest_manifest.json"


def _manifest_path(persist_dir: Path) -> Path:
    """Return the manifest path local to one persisted index."""
    return persist_dir.resolve() / MANIFEST_FILENAME


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {"files": {}}
    files = data.get("files", {})
    return {"files": files if isinstance(files, dict) else {}}


def save_manifest(path: Path, files: dict[str, str]) -> None:
    payload = {"version": 2, "chunking_strategy": settings.chunking_strategy, "files": files}
    tmp_path = path.with_name(path.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def _plan_incremental(
    current_hashes: dict[str, str], previous: dict[str, str]
) -> tuple[list[str], list[str]]:
    """Return (removed_relative_paths, new_or_changed_relative_paths)."""
    removed = sorted(set(previous) - set(current_hashes))
    changed = sorted(
        rel for rel, digest in current_hashes.items() if previous.get(rel) != digest
    )
    return removed, changed


def update_vectorstore_incremental(
    source_dir: Path,
    persist_dir: Path,
    collection_name: str,
) -> tuple[int, int, int]:
    """Ingest only new/changed files; drop chunks of removed files.

    Returns (new_or_changed, removed, unchanged) file counts.
    """
    files = {
        path.relative_to(source_dir).as_posix(): path
        for path in discover_files(source_dir)
    }
    current_hashes = {rel: _file_sha256(path) for rel, path in files.items()}

    manifest_path = _manifest_path(persist_dir)
    manifest = load_manifest(manifest_path)
    # Existing F/R/P stores predate per-index manifests and share a parent
    # manifest. Use it only as a read-only baseline for this first update;
    # all writes below go to the index-local path.
    if not manifest["files"]:
        legacy_path = persist_dir.resolve().parent / MANIFEST_FILENAME
        if legacy_path != manifest_path:
            manifest = load_manifest(legacy_path)
    previous = manifest["files"]
    removed, changed = _plan_incremental(current_hashes, previous)
    unchanged = len(files) - len(changed)

    if not removed and not changed:
        print(
            f"No changes detected; nothing to ingest "
            f"({unchanged} file(s) unchanged)."
        )
        return (0, 0, unchanged)

    import chromadb

    persist_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(persist_dir))
    try:
        vectorstore = Chroma(
            client=client,
            collection_name=collection_name,
            embedding_function=get_embeddings(show_progress=True),
            collection_configuration=COLLECTION_CONFIGURATION,
        )
        collection = vectorstore._collection

        for rel in removed:
            collection.delete(where={"source": rel})
            previous.pop(rel, None)
            print(f"Removed chunks of deleted file: {rel}")

        for rel in changed:
            chunks = split_documents(_load_one(files[rel], source_dir))
            # Delete any pre-existing chunks of this file first: this is safe
            # for changed files and for a first run with a missing manifest.
            collection.delete(where={"source": rel})
            if chunks:
                ids = [str(chunk.metadata["chunk_id"]) for chunk in chunks]
                vectorstore.add_documents(chunks, ids=ids)
            previous[rel] = current_hashes[rel]
            print(f"Ingested {len(chunks)} chunk(s) from {rel}")

        total = collection.count()
        print(f"Total chunks in collection: {total}")
    finally:
        client.close()

    save_manifest(manifest_path, previous)
    return (len(changed), len(removed), unchanged)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=settings.documents_dir,
        help="Directory containing PDF, TXT, and Markdown files",
    )
    parser.add_argument(
        "--persist-dir",
        type=Path,
        default=settings.chroma_dir,
        help="Chroma persistence directory",
    )
    parser.add_argument(
        "--collection-name",
        default=settings.collection_name,
        help="Chroma collection name",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Wipe and rebuild the entire collection from scratch "
        "(default is incremental: only new/changed files are ingested)",
    )
    parser.add_argument(
        "--metadata-output",
        type=Path,
        default=None,
        help="Write JSON build metadata after a successful full rebuild",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    # Windows PowerShell may expose a GBK stdout stream. File names in the
    # knowledge base can contain symbols such as "™", so keep ingestion logs
    # printable instead of failing while reporting a path.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ensure_runtime_dirs()

    if not args.full:
        added, removed, unchanged = update_vectorstore_incremental(
            args.source_dir, args.persist_dir, args.collection_name
        )
        print(
            f"Incremental ingest done: {added} new/changed file(s), "
            f"{removed} removed, {unchanged} unchanged."
        )
        return 0

    documents = load_documents(args.source_dir)
    if not documents:
        print(f"No supported documents found in: {args.source_dir}")
        print("The existing vector database was not changed.")
        return 1

    print(f"Loaded {len(documents)} source pages/files.")
    chunks = split_documents(documents)
    print(f"Created {len(chunks)} chunks.")
    print(
        f"Embedding device (ingest): {resolve_embedding_device()} | "
        f"retrieval device: {settings.retrieval_device} | "
        f"batch size: {settings.embedding_batch_size}"
    )
    count = rebuild_vectorstore(chunks, args.persist_dir, args.collection_name)
    print(f"Ingestion complete. Collection contains {count} chunks: {args.persist_dir}")

    # Record the current files so later incremental runs only embed what changed.
    files = {
        path.relative_to(args.source_dir).as_posix(): _file_sha256(path)
        for path in discover_files(args.source_dir)
    }
    save_manifest(_manifest_path(args.persist_dir), files)
    print(f"Manifest refreshed with {len(files)} file(s).")
    if args.metadata_output:
        metadata = {
            "strategy": settings.chunking_strategy,
            "collection": args.collection_name,
            "source_dir": str(args.source_dir.resolve()),
            "persist_dir": str(args.persist_dir.resolve()),
            "document_count": len(documents),
            "file_count": len(files),
            "chunk_statistics": chunk_statistics(chunks),
        }
        args.metadata_output.parent.mkdir(parents=True, exist_ok=True)
        args.metadata_output.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Build metadata written: {args.metadata_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
