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
