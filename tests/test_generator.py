from langchain_core.documents import Document

from src.generator import append_audit_notice, build_fallback_answer, build_sources, format_context


def test_assigns_separate_local_and_web_citation_ids():
    documents = [
        Document(
            page_content="local",
            metadata={"source_type": "local", "source": "guide.md", "title": "Guide"},
        ),
        Document(
            page_content="web",
            metadata={"source_type": "web", "source": "https://example.com", "title": "Web"},
        ),
    ]

    context = format_context(documents)
    sources = build_sources(documents)

    assert "[L1]" in context
    assert "[W1]" in context
    assert [source["id"] for source in sources] == ["L1", "W1"]


def test_context_contains_evidence_metadata_and_audit_notice():
    document = Document(
        page_content="背景资料",
        metadata={
            "source_type": "local",
            "source": "guide.md",
            "title": "Guide",
            "source_category": "reference_handbook",
            "evidence_level": "reference_handbook",
            "population": "unknown",
            "material_grade": "unknown",
            "retrieval_evidence_status": "indirect",
        },
    )
    context = format_context([document])
    assert "检索状态：indirect" in context
    answer = append_audit_notice("答案", {"warnings": [{"status": "indirect_evidence", "reason": "仅为背景"}]})
    assert "证据审计提示" in answer
