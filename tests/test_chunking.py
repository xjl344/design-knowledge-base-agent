from types import SimpleNamespace

from langchain_core.documents import Document

import src.chunking as chunking


def test_oversized_semantic_chunk_is_split_at_sentence_boundaries(monkeypatch):
    monkeypatch.setattr(chunking, "settings", SimpleNamespace(semantic_max_chunk_size=20))
    source = Document(
        page_content="第一句内容很长。第二句内容很长。第三句内容很长。",
        metadata={"source": "a.md", "section_path": "S", "start_index": 0},
    )
    result = chunking._split_oversized_semantic_chunks([source])
    assert len(result) >= 2
    assert all(len(item.page_content) <= 20 for item in result)
    assert all(item.metadata["section_path"] == "S" for item in result)


def test_markdown_recursive_chunking_preserves_doc_title_and_section_breadcrumbs():
    md = (
        "# CNC 铣削设计准则（Design Guidelines for CNC Milling）\n\n"
        "- 来源：Protolabs\n\n"
        "## Size\n\n"
        "| Material | Metric |\n"
        "| --- | --- |\n"
        "| Aluminum | 559mm x 356mm x 95.3mm |\n\n"
        "## Draft\n\n"
        "| VERTICAL FACES | 0.5° |\n"
        "| --- | --- |\n"
        "| MOST SITUATIONS | 2° |\n"
    )
    doc = Document(page_content=md, metadata={"source": "05_制造工艺/CNC_Milling.md"})
    chunks = chunking.split_documents([doc], strategy="R")

    size_chunk = next(c for c in chunks if "559mm x 356mm x 95.3mm" in c.page_content)
    draft_chunk = next(c for c in chunks if "VERTICAL FACES" in c.page_content)

    assert "CNC 铣削设计准则" in size_chunk.page_content
    assert "Size" in size_chunk.metadata["section_path"]
    assert "VERTICAL FACES" not in size_chunk.page_content

    assert "CNC 铣削设计准则" in draft_chunk.page_content
    assert "Draft" in draft_chunk.metadata["section_path"]
    assert "559mm" not in draft_chunk.page_content


def test_oversized_markdown_table_repeats_header_when_split():
    rows = "\n".join(f"| Material_{i} | {i}.0mm - {i+1}.0mm |" for i in range(40))
    md = f"# 注塑指南\n\n## Wall Thickness\n\n| MATERIAL | WALL |\n| --- | --- |\n{rows}\n"
    doc = Document(page_content=md, metadata={"source": "guide.md"})
    chunks = chunking.split_documents([doc], strategy="R")

    assert len(chunks) >= 2
    for piece in chunks:
        assert "注塑指南 > Wall Thickness" in piece.page_content
        assert "| MATERIAL | WALL |" in piece.page_content
