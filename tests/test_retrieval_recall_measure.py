"""The recall measurement's arithmetic, on a fake retriever.

This script decides whether an entire workstream (retrieval depth) is worth
doing, and its core logic is exactly where a mistake is invisible: a wrong
comparison yields an all-zero curve, which reads as "retrieval finds nothing"
rather than "the comparison is wrong".  That already happened once -- comparing
`expected_sources` (file names) against index metadata (relative paths) without
taking the basename.

So the checks here are about the *matching*, not about the retriever.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.measure_retrieval_recall as measure  # noqa: E402


class _Doc:
    def __init__(self, source: str):
        self.metadata = {"source": source}
        self.page_content = ""


@pytest.fixture()
def fake_retriever(monkeypatch):
    """Replace the retriever and the question list with fixed inputs."""

    def install(sources_by_question: dict[str, list[str]], questions: list[dict]):
        monkeypatch.setattr(
            "src.retriever.retrieve_documents",
            lambda question, top_k=None: [
                _Doc(source) for source in sources_by_question[question]
            ],
        )
        monkeypatch.setattr(
            measure, "QUESTIONS", _FakeQuestions(questions)
        )

    return install


class _FakeQuestions:
    def __init__(self, questions):
        self._questions = questions

    def read_text(self, encoding="utf-8"):
        import json

        return json.dumps({"questions": self._questions})


def test_a_relative_path_matches_a_bare_filename(fake_retriever):
    """The trap that produced an all-zero result the first time.

    `expected_sources` holds `a.pdf`; the index metadata holds
    `docs/standards/a.pdf`.  Comparing them raw finds nothing.
    """
    fake_retriever(
        {"q": ["docs/standards/a.pdf"]},
        [{"id": "c1", "question": "q", "expected_sources": ["a.pdf"]}],
    )
    result = measure.measure(top_k=10, progress=False)
    assert result["expected_sources_found"] == 1
    assert result["source_recall_at"]["5"] == 1.0


def test_windows_separators_are_normalised(fake_retriever):
    """The index is built on Windows, so metadata may carry backslashes."""
    fake_retriever(
        {"q": [r"docs\standards\a.pdf"]},
        [{"id": "c1", "question": "q", "expected_sources": ["a.pdf"]}],
    )
    result = measure.measure(top_k=10, progress=False)
    assert result["expected_sources_found"] == 1


def test_the_rank_is_the_first_occurrence(fake_retriever):
    """A source appearing twice must be credited at its earliest rank."""
    fake_retriever(
        {"q": ["x.pdf", "y.pdf", "a.pdf", "a.pdf"]},
        [{"id": "c1", "question": "q", "expected_sources": ["a.pdf"]}],
    )
    result = measure.measure(top_k=10, progress=False)
    assert result["per_question"][0]["ranks"]["a.pdf"] == 3
    assert result["source_recall_at"]["5"] == 1.0


def test_recall_at_is_monotonic_in_the_cutoff(fake_retriever):
    """A source at rank 12 counts for @20 but not for @10.

    This is the shape the whole workstream decision rests on: if @10 and @20
    were equal there would be no headroom.
    """
    sources = [f"s{i}.pdf" for i in range(1, 21)]
    fake_retriever(
        {"q": sources},
        [{"id": "c1", "question": "q", "expected_sources": ["s12.pdf", "s3.pdf"]}],
    )
    result = measure.measure(top_k=20, progress=False)
    assert result["source_recall_at"]["5"] == 0.5   # only s3
    assert result["source_recall_at"]["10"] == 0.5
    assert result["source_recall_at"]["20"] == 1.0


def test_a_source_that_never_appears_is_reported_as_missing(fake_retriever):
    """The distinction the plan turns on: capped away vs not retrieved at all."""
    fake_retriever(
        {"q": ["x.pdf"]},
        [{"id": "c1", "question": "q", "expected_sources": ["absent.pdf"]}],
    )
    result = measure.measure(top_k=20, progress=False)
    assert result["expected_sources_found"] == 0
    assert result["per_question"][0]["missing"] == ["absent.pdf"]


def test_only_filters_the_question_set(fake_retriever):
    """`--only` exists to halve a 75-minute run; it must not silently no-op."""
    fake_retriever(
        {"q1": ["a.pdf"], "q2": ["b.pdf"]},
        [
            {"id": "c1", "question": "q1", "expected_sources": ["a.pdf"]},
            {"id": "c2", "question": "q2", "expected_sources": ["b.pdf"]},
        ],
    )
    result = measure.measure(top_k=10, progress=False, only={"c2"})
    assert result["questions"] == 1
    assert result["per_question"][0]["id"] == "c2"


def test_progress_goes_to_stderr_so_stdout_stays_parseable(fake_retriever, capsys):
    """`--json` output must not be polluted by progress lines.

    Progress on stdout would be interleaved with the payload, so piping into a
    JSON reader would fail on a perfectly good run -- and the failure would look
    like a broken measurement rather than a stray print.
    """
    fake_retriever(
        {"q": ["a.pdf"]},
        [{"id": "c1", "question": "q", "expected_sources": ["a.pdf"]}],
    )
    measure.measure(top_k=10, progress=True)
    captured = capsys.readouterr()
    assert "1/1" in captured.err, "进度应写到 stderr"
    assert captured.out == "", f"stdout 应保持干净，实际有 {captured.out!r}"


def test_quiet_suppresses_progress_entirely(fake_retriever, capsys):
    fake_retriever(
        {"q": ["a.pdf"]},
        [{"id": "c1", "question": "q", "expected_sources": ["a.pdf"]}],
    )
    measure.measure(top_k=10, progress=False)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
