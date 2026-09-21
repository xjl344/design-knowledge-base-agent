"""Offline tests for the LangSmith upload helper logic.

Nothing here touches the network: the upload path itself needs an API key and a
live workspace, so what is testable is the pure part -- which questions a run is
allowed to describe, what per-question detail is assembled, and above all that
the dataset name is *stable* across arms.  If the name moved per upload, each
arm would land in its own dataset and LangSmith's side-by-side comparison would
silently stop working, which is the failure this refactor exists to prevent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import upload_generation_replay_langsmith as uploader  # noqa: E402


def _run(rows: list[dict], **extra) -> dict:
    return {"rows": rows, "model": "gpt-5.6", "retrieval_calls": 0, **extra}


def _row(question_id: str) -> dict:
    return {"question_id": question_id, "question": f"{question_id} 的问题？", "status": "completed"}


def _contract(cases: list[dict], family: str | None = "multihop.v1") -> dict:
    payload = {"version": 1, "cases": cases}
    if family is not None:
        payload["family"] = family
    return payload


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# --- question-set validation ---------------------------------------------


def test_load_inputs_accepts_any_question_count(tmp_path):
    """The old guard hardcoded 12, which is a property of one set, not of validity."""
    cases = [{"id": "mh01"}, {"id": "mh02"}, {"id": "mh03"}]
    result = _write(tmp_path / "r.json", _run([_row("mh01"), _row("mh02"), _row("mh03")]))
    evaluation = _write(tmp_path / "e.json", _contract(cases))

    run, by_id = uploader.load_inputs(result, evaluation)
    assert set(by_id) == {"mh01", "mh02", "mh03"}
    assert len(run["rows"]) == 3


def test_load_inputs_rejects_a_row_without_a_contract_entry(tmp_path):
    """A missing expectation would score the row against nothing."""
    result = _write(tmp_path / "r.json", _run([_row("mh01"), _row("mh99")]))
    evaluation = _write(tmp_path / "e.json", _contract([{"id": "mh01"}]))

    with pytest.raises(ValueError, match="不一致"):
        uploader.load_inputs(result, evaluation)


def test_load_inputs_rejects_a_contract_entry_without_a_row(tmp_path):
    result = _write(tmp_path / "r.json", _run([_row("mh01")]))
    evaluation = _write(tmp_path / "e.json", _contract([{"id": "mh01"}, {"id": "mh02"}]))

    with pytest.raises(ValueError, match="不一致"):
        uploader.load_inputs(result, evaluation)


def test_load_inputs_rejects_an_empty_run(tmp_path):
    result = _write(tmp_path / "r.json", _run([]))
    evaluation = _write(tmp_path / "e.json", _contract([{"id": "mh01"}]))
    with pytest.raises(ValueError, match="rows"):
        uploader.load_inputs(result, evaluation)


# --- dataset identity -----------------------------------------------------


def test_dataset_name_is_stable_across_arms(tmp_path):
    """Two arms of one question set must resolve to one dataset.

    This is the whole point of the refactor: a per-run dataset name would put
    the two arms in separate datasets, where LangSmith cannot compare them.
    """
    cases = [{"id": f"mh{i:02d}"} for i in range(1, 7)]
    evaluation = _contract(cases)
    by_id = {case["id"]: case for case in cases}

    first = uploader._dataset_name(evaluation, by_id)
    second = uploader._dataset_name(evaluation, by_id)
    assert first == second
    assert first == "design-kb-multihop-6"


def test_dataset_name_does_not_embed_the_run_or_time():
    cases = [{"id": "mh01"}]
    evaluation = _contract(cases)
    name = uploader._dataset_name(evaluation, {case["id"]: case for case in cases})
    assert "2026" not in name and "T" not in name


def test_family_falls_back_when_absent():
    assert uploader._family({}) == "generation"


def test_family_is_name_safe():
    assert uploader._family({"family": "multihop.v1"}) == "multihop"
    assert uploader._family({"family": "my family/set"}) == "my-family-set"


def test_experiment_name_distinguishes_arms():
    result = {"model": "gpt-5.6"}
    first = uploader._experiment_name(result, "evidence5")
    second = uploader._experiment_name(result, "evidence-all")
    assert "evidence5" in first
    assert "evidence-all" in second
    assert first != second


# --- per-question detail --------------------------------------------------


def test_metric_values_include_hop_recall_and_its_applicability():
    row = {
        "generation_success": True,
        "audit": {"hop_recall": 0.667, "hop_metric_applicable": True},
    }
    values = uploader._metric_values(row)
    assert values["hop_recall"] == 0.667
    assert values["hop_metric_applicable"] is True


def test_metric_values_report_none_when_hop_recall_is_not_applicable():
    """A single-hop row must not contribute a hop score."""
    row = {"audit": {"hop_recall": None, "hop_metric_applicable": False}}
    values = uploader._metric_values(row)
    assert values["hop_recall"] is None
    # And None must not be turned into a score.
    assert uploader._numeric(values["hop_recall"]) is None


def test_hop_inputs_carry_the_source_of_each_hop():
    case = {
        "required_hops": [
            {"hop_id": "h1", "from_question": "q01_hit", "source_chunk_id": "abc", "position": 1},
            {"hop_id": "h2", "from_question": "q06_hit", "source_chunk_id": "def", "position": 8},
        ]
    }
    hops = uploader._hop_inputs(case)
    assert [hop["hop_id"] for hop in hops] == ["h1", "h2"]
    assert hops[1]["from_question"] == "q06_hit"
    # The position is what explains a truncation-caused miss, so it must survive.
    assert hops[1]["position"] == 8


def test_hop_inputs_are_empty_for_a_single_hop_contract():
    assert uploader._hop_inputs({}) == []


def test_hop_verdicts_expose_which_hop_was_missed():
    audit = {
        "hop_results": [
            {"hop_id": "h1", "matched": True, "span_matched": True, "terms": [{"matched": True, "kind": "alternatives"}]},
            {"hop_id": "h2", "matched": False, "span_matched": False, "terms": [{"matched": False, "kind": "alternatives"}]},
        ]
    }
    verdicts = uploader._hop_verdicts(audit)
    assert [item["hop_id"] for item in verdicts] == ["h1", "h2"]
    assert verdicts[1]["matched"] is False
    assert verdicts[1]["term_matches"] == [{"matched": False, "kind": "alternatives"}]


def test_hop_verdicts_are_empty_when_the_audit_has_none():
    assert uploader._hop_verdicts({}) == []


def test_example_payload_carries_hops_on_both_sides():
    """The example must show what was expected and what the system did."""
    case = {
        "required_hops": [{"hop_id": "h1", "from_question": "q01_hit", "source_chunk_id": "abc"}],
        "expected_answer_spans": [{"id": "s1", "text": "680~760"}],
        "expected_sources": ["标准/甲.pdf"],
        "case_type": "multi_hop",
    }
    row = {
        "question_id": "mh01",
        "question": "问题？",
        "answer": "答案",
        "audit": {"hop_results": [{"hop_id": "h1", "matched": True}]},
    }
    payload = uploader._example_payload(row, case)
    assert payload["inputs"]["hops"][0]["hop_id"] == "h1"
    assert payload["outputs"]["hop_results"][0]["matched"] is True
    assert payload["metadata"]["case_type"] == "multi_hop"


# --- upload-time guards ---------------------------------------------------


def test_upload_refuses_a_dry_run_result(tmp_path, monkeypatch):
    """A dry run has no answers, so uploading one would publish empty results."""
    monkeypatch.setenv("LANGCHAIN_API_KEY", "test-key")
    result = _run([_row("mh01")], dry_run=True)
    with pytest.raises(ValueError, match="dry_run"):
        uploader.upload(result, {"mh01": {}}, {}, tmp_path / "r.json", tmp_path / "e.json")


def test_upload_refuses_a_run_that_called_retrieval(tmp_path, monkeypatch):
    """The frozen-retrieval guarantee is what makes these runs comparable."""
    monkeypatch.setenv("LANGCHAIN_API_KEY", "test-key")
    result = _run([_row("mh01")], retrieval_calls=7)
    with pytest.raises(ValueError, match="retrieval_calls"):
        uploader.upload(result, {"mh01": {}}, {}, tmp_path / "r.json", tmp_path / "e.json")


def test_upload_refuses_a_result_without_a_model_name(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGCHAIN_API_KEY", "test-key")
    result = _run([_row("mh01")])
    result.pop("model")
    with pytest.raises(ValueError, match="模型"):
        uploader.upload(result, {"mh01": {}}, {}, tmp_path / "r.json", tmp_path / "e.json")
