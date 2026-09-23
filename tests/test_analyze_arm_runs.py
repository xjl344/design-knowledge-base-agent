"""The labelling half of `analyze_arm_runs`, which had no test.

The analysis itself is imported from `interleaved_generation_rounds` and is
covered there.  What is *not* covered anywhere is this script's own job:
attaching `case_set` / `arm` / `round` to rows written by a harness that had no
idea it was part of a comparison.

That is the dangerous half.  A mislabelled run produces a confident comparison
of the wrong things, and nothing downstream can tell -- the same shape as the
basename trap and the empty-needle defect: silent, and it looks like a result.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analyze_arm_runs import (  # noqa: E402
    load_labelled,
    parse_pair,
    parse_run,
)


# ---------------------------------------------------------------------------
# Spec parsing.
# ---------------------------------------------------------------------------


def test_a_run_spec_uses_commas_so_windows_paths_survive():
    """A colon separator would split `C:\\...` in half."""
    spec = parse_run(r"case_set=real,arm=all,round=2,path=C:\runs\x.json")
    assert spec["path"] == r"C:\runs\x.json"
    assert spec["round"] == "2"


def test_a_run_spec_missing_a_field_is_rejected():
    """Silently defaulting would label rows with the wrong arm."""
    with pytest.raises(SystemExit, match="缺少字段"):
        parse_run("case_set=real,arm=all,path=x.json")


def test_a_run_field_that_is_not_key_value_is_rejected():
    with pytest.raises(SystemExit, match="key=value"):
        parse_run("case_set=real,arm=all,round=1,nonsense")


def test_a_pair_needs_two_non_empty_arms():
    assert parse_pair("a,b") == ("a", "b")
    with pytest.raises(SystemExit):
        parse_pair("a")
    with pytest.raises(SystemExit):
        parse_pair("a,")


# ---------------------------------------------------------------------------
# Labelling.
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "run.json"
    path.write_text(json.dumps({"rows": rows}), encoding="utf-8")
    return path


def test_labels_are_taken_from_the_spec_not_the_rows(tmp_path):
    """The replay harness writes no labels, so the spec is the only source.

    If a row already carried a label, the spec must still win -- otherwise a
    stale label in the file silently overrides the operator's intent.
    """
    path = _write(tmp_path, [
        {"question_id": "q1", "case_set": "WRONG", "arm": "WRONG", "round": 99,
         "status": "completed"},
    ])
    rows = load_labelled({
        "case_set": "real", "arm": "capall", "round": "2", "path": str(path),
    })
    assert rows[0]["case_set"] == "real"
    assert rows[0]["arm"] == "capall"
    assert rows[0]["round"] == 2
    # The rest of the row is preserved.
    assert rows[0]["question_id"] == "q1"


def test_a_row_without_a_status_falls_back_to_generation_success(tmp_path):
    """The pairing keys on `status`; an aborted row may not carry one.

    `generation_success` is the honest fallback: a row that produced no answer
    is a failed row, not a wrong one.
    """
    path = _write(tmp_path, [
        {"question_id": "ok", "generation_success": True},
        {"question_id": "bad", "generation_success": False},
    ])
    rows = load_labelled({
        "case_set": "s", "arm": "a", "round": "0", "path": str(path),
    })
    assert rows[0]["status"] == "completed"
    assert rows[1]["status"] == "failed"


def test_an_existing_status_is_not_overwritten(tmp_path):
    """A recorded timeout must stay a timeout, not become `completed`."""
    path = _write(tmp_path, [
        {"question_id": "q", "status": "provider_timeout", "generation_success": False},
    ])
    rows = load_labelled({
        "case_set": "s", "arm": "a", "round": "0", "path": str(path),
    })
    assert rows[0]["status"] == "provider_timeout"


def test_a_run_with_no_rows_is_rejected(tmp_path):
    """An empty run would silently contribute nothing to every mean."""
    path = _write(tmp_path, [])
    with pytest.raises(SystemExit, match="没有 rows"):
        load_labelled({
            "case_set": "s", "arm": "a", "round": "0", "path": str(path),
        })


def test_a_missing_file_is_rejected(tmp_path):
    with pytest.raises(SystemExit, match="不存在"):
        load_labelled({
            "case_set": "s", "arm": "a", "round": "0",
            "path": str(tmp_path / "nope.json"),
        })
