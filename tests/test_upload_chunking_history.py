from pathlib import Path

from upload_chunking_history import (
    _infer_strategy,
    _row_metrics,
    load_result_file,
)


def test_row_metrics_accepts_current_and_legacy_shapes():
    current = {
        "source_hit": 0.8,
        "source_level_metrics": {
            "recall_at_5": 0.6,
            "recall_at_10": 0.9,
            "mrr": 0.5,
            "ndcg_at_10": 0.7,
        },
    }
    assert _row_metrics(current)["recall_at_10"] == 0.9

    legacy = {"source_hit": None, "retrieval_metrics": {"mrr": "0.25"}}
    metrics = _row_metrics(legacy)
    assert metrics["source_hit"] == 0.0
    assert metrics["mrr"] == 0.25


def test_strategy_is_read_from_payload_before_filename():
    assert _infer_strategy(Path("unexpected-name.json"), {"strategy": "p"}) == "P"


def test_load_result_file_reads_utf8_results(tmp_path):
    path = tmp_path / "F_retrieval_old.json"
    path.write_text('{"strategy":"F","results":[{"question":"题目"}]}', encoding="utf-8")
    strategy, rows = load_result_file(path)
    assert strategy == "F"
    assert rows[0]["question"] == "题目"


def test_load_result_file_accepts_powershell_utf8_bom(tmp_path):
    path = tmp_path / "R_retrieval_bom.json"
    path.write_bytes(b"\xef\xbb\xbf{\"strategy\":\"R\",\"results\":[]}")
    strategy, rows = load_result_file(path)
    assert strategy == "R"
    assert rows == []
