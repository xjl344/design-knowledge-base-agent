from pathlib import Path

import chromadb
from langchain_core.embeddings import Embeddings
import pytest

import ingest
from ingest import (
    _cjk_ratio,
    _file_sha256,
    _looks_garbled,
    _plan_incremental,
    discover_files,
    load_manifest,
    load_documents,
    make_chunk_id,
    rebuild_vectorstore,
    save_manifest,
    split_documents,
)
from src.document_metadata import build_document_metadata
from src.chunking import chunk_statistics, split_documents as split_with_strategy


FIXTURES = Path(__file__).parent / "fixtures" / "documents"


def test_document_metadata_is_conservative_and_traceable():
    metadata = build_document_metadata(
        "02_材料数据/PC/PC_TDS.pdf",
        "PC_TDS",
        "Technical data sheet. No explicit application scope.",
        page=2,
    )

    assert metadata["source_category"] == "material_tds"
    assert metadata["evidence_level"] == "supplier_data"
    assert metadata["material_grade"] == "PC"
    assert metadata["page"] == 2
    assert metadata["applicability"] == "unknown"


class FakeEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    @staticmethod
    def _embed(text: str) -> list[float]:
        return [float(len(text)), 1.0, 0.5]


def test_discovers_only_supported_documents(tmp_path):
    (tmp_path / "a.md").write_text("A", encoding="utf-8")
    (tmp_path / "b.docx").write_text("B", encoding="utf-8")

    assert [path.name for path in discover_files(tmp_path)] == ["a.md"]


def test_loads_and_splits_fixture_with_stable_ids():
    documents = load_documents(FIXTURES)
    first_chunks = split_documents(documents)
    second_chunks = split_documents(documents)

    assert documents[0].metadata["source"] == "test_design_notes.md"
    assert documents[0].metadata["source_type"] == "local"
    assert first_chunks
    assert [make_chunk_id(chunk) for chunk in first_chunks] == [
        make_chunk_id(chunk) for chunk in second_chunks
    ]
    assert len({chunk.metadata["chunk_id"] for chunk in first_chunks}) == len(
        first_chunks
    )


def test_chunking_strategies_preserve_metadata_and_are_distinct(monkeypatch):
    documents = load_documents(FIXTURES)
    paragraph_chunks = split_with_strategy(documents, "P")
    assert paragraph_chunks
    assert all(chunk.metadata["chunk_strategy"] == "P" for chunk in paragraph_chunks)
    assert all("chunk_version" in chunk.metadata for chunk in paragraph_chunks)
    stats = chunk_statistics(paragraph_chunks)
    assert stats["chunk_count"] == len(paragraph_chunks)
    assert stats["mean_chars"] > 0

    recursive_chunks = split_with_strategy(documents, "R")
    assert recursive_chunks
    assert all(chunk.metadata["chunk_strategy"] == "R" for chunk in recursive_chunks)
    assert [make_chunk_id(c) for c in paragraph_chunks] != [make_chunk_id(c) for c in recursive_chunks]


def test_rebuild_uses_requested_collection_and_replaces_old_store(
    tmp_path, monkeypatch
):
    persist_dir = tmp_path / "chroma_db"
    persist_dir.mkdir()
    (persist_dir / "old-index-marker").write_text("old", encoding="utf-8")
    chunks = split_documents(load_documents(FIXTURES))
    monkeypatch.setattr(ingest, "get_embeddings", lambda show_progress=False: FakeEmbeddings())

    count = rebuild_vectorstore(chunks, persist_dir, "custom_collection")

    assert count == len(chunks)
    assert not (persist_dir / "old-index-marker").exists()
    with chromadb.PersistentClient(path=str(persist_dir)) as client:
        assert [collection.name for collection in client.list_collections()] == [
            "custom_collection"
        ]
        assert client.get_collection("custom_collection").count() == len(chunks)


def test_failed_rebuild_preserves_existing_store(tmp_path, monkeypatch):
    persist_dir = tmp_path / "chroma_db"
    persist_dir.mkdir()
    marker = persist_dir / "old-index-marker"
    marker.write_text("old", encoding="utf-8")
    chunks = split_documents(load_documents(FIXTURES))

    def fail_build(chunks, staging_dir, collection_name):
        del chunks, collection_name
        staging_dir.mkdir()
        (staging_dir / "partial-index").write_text("partial", encoding="utf-8")
        raise RuntimeError("embedding interrupted")

    monkeypatch.setattr(ingest, "_build_vectorstore", fail_build)

    with pytest.raises(RuntimeError, match="embedding interrupted"):
        rebuild_vectorstore(chunks, persist_dir, "custom_collection")

    assert marker.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob(".chroma_db.rebuild-*"))


def test_cjk_ratio():
    assert _cjk_ratio("中华人民共和国国家标准") == 1.0
    assert _cjk_ratio("GB/T26158—2010 中国未成年人人体尺寸") > 0
    assert _cjk_ratio("abc def 123") == 0.0
    assert _cjk_ratio("") == 0.0


@pytest.mark.parametrize(
    "text,expected",
    [
        # real clean pages from the knowledge base
        (
            "中华人民共和国国家标准 GB/T26158—2010 中国未成年人人体尺寸 "
            "Human dimensions of Chinese minors",
            False,
        ),
        (
            "表2 7岁~10岁未成年男子人体尺寸百分位数 单位为毫米 测量项目 "
            "百分位数 P1 P2.5 P5 P10 P25 P50 P75 P90 P95 P97",
            False,
        ),
        (
            "家具 桌、椅、凳类主要尺寸 1 范围 本标准规定了桌、椅、凳类家具的主要尺寸。",
            False,
        ),
        # real mojibake extracted from GBT+10000-2023 / GBT+16252-2023
        ('!"#!"!#$ ""# $ %& ! " # $ % & \' \' ( ) * %&!\'!$$$$"%$%"', True),
        (
            "d !!!)e!(\"e'(U0VW)*+,fJgP \"2# 5678 ²Ä= 4! 4% 4!- 4%- 4(- 4(% 4((",
            True,
        ),
        # English prose should be kept
        (
            "The design thinking handbook provides a comprehensive introduction "
            "to design thinking methods.",
            False,
        ),
        # edge cases
        ("", False),
        ("1", False),
    ],
)
def test_looks_garbled(text: str, expected: bool):
    assert _looks_garbled(text) is expected


class _FakePage:
    def __init__(self, text: str):
        self._text = text

    def get_text(self) -> str:
        return self._text


class _FakePDF:
    def __init__(self, pages):
        self._pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._pages)


def test_load_documents_skips_garbled_pdf_pages(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"not a real pdf")
    pages = [
        _FakePage("中华人民共和国国家标准 家具 桌、椅、凳类主要尺寸"),
        _FakePage('!"#!"!#$ ""# $ %& ! " # $ % & \' \' ( ) * %&!\'!$$$$"%$%"'),
    ]
    monkeypatch.setattr(ingest.pymupdf, "open", lambda path: _FakePDF(pages))
    monkeypatch.setattr(ingest, "_ocr_page", lambda page: None)

    with pytest.warns(RuntimeWarning, match="garbled page"):
        documents = load_documents(tmp_path)

    assert [document.metadata["page"] for document in documents] == [0]
    assert documents[0].page_content == "中华人民共和国国家标准 家具 桌、椅、凳类主要尺寸"


def test_load_documents_recovers_garbled_page_via_ocr(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"not a real pdf")
    pages = [_FakePage('!"#!"!#$ ""# $ %& ! " # $ % & \' \' ( ) * %&!\'!$$$$"%$%"')]
    monkeypatch.setattr(ingest.pymupdf, "open", lambda path: _FakePDF(pages))
    monkeypatch.setattr(
        ingest, "_ocr_page", lambda page: "中华人民共和国国家标准 中国成年人人体尺寸"
    )

    with pytest.warns(RuntimeWarning, match="via OCR"):
        documents = load_documents(tmp_path)

    assert [document.metadata["page"] for document in documents] == [0]
    assert documents[0].metadata["ocr"] is True
    assert documents[0].page_content == "中华人民共和国国家标准 中国成年人人体尺寸"


def test_load_documents_skips_page_when_ocr_unavailable(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"not a real pdf")
    pages = [_FakePage('!"#!"!#$ ""# $ %& ! " # $ % & \' \' ( ) * %&!\'!$$$$"%$%"')]
    monkeypatch.setattr(ingest.pymupdf, "open", lambda path: _FakePDF(pages))
    monkeypatch.setattr(ingest, "_ocr_page", lambda page: None)

    with pytest.warns(RuntimeWarning, match="Skipped 1 garbled page"):
        documents = load_documents(tmp_path)

    assert documents == []


def test_plan_incremental():
    previous = {"a.md": "h1", "b.md": "h2", "gone.md": "h3"}
    current = {"a.md": "h1", "b.md": "hX", "c.md": "h4"}

    removed, changed = _plan_incremental(current, previous)

    assert removed == ["gone.md"]
    assert changed == ["b.md", "c.md"]


def test_manifest_roundtrip(tmp_path):
    path = tmp_path / "ingest_manifest.json"
    save_manifest(path, {"a.md": "h1", "子目录/b.pdf": "h2"})

    assert load_manifest(path) == {"files": {"a.md": "h1", "子目录/b.pdf": "h2"}}


def test_manifest_missing_returns_empty(tmp_path):
    assert load_manifest(tmp_path / "nope.json") == {"files": {}}


def test_file_sha256_detects_content_change(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("hello", encoding="utf-8")
    first = _file_sha256(f)
    assert _file_sha256(f) == first  # stable across reads

    f.write_text("hello!", encoding="utf-8")
    assert _file_sha256(f) != first
