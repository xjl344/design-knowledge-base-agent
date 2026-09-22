"""The admission gate must fire, and must show its numbers when it passes.

A gate that never fires and a gate that always passes produce the same summary
line.  These tests make the gate fail on purpose, so the passing case means
something.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.interleaved_generation_rounds import PROVIDER_GATE, provider_gate  # noqa: E402


def _rows(rounds: int, per_round: int, status: str = "completed"):
    rows = []
    for index in range(rounds):
        for slot in range(per_round):
            rows.append({"round": index, "status": status, "question_id": f"q{slot}"})
    return rows


def test_a_healthy_run_passes_and_still_reports_its_numbers():
    """Passing must not mean "nothing to see".

    The observed values are reported whether or not the gate fires, because a
    summary that only says `passed` cannot be distinguished from one that never
    ran the check.
    """
    gate = provider_gate(_rows(3, 22), rounds=3, paired=60, cells=66)
    assert gate["passed"] is True
    assert gate["failed"] == []
    assert gate["observed"]["provider_error_rate"] == 0.0
    assert gate["observed"]["paired_cell_share"] == round(60 / 66, 3)
    assert gate["thresholds"] == PROVIDER_GATE


def test_a_round_with_no_completions_fails_the_gate():
    """The round-3 collapse of the first interleaved run.

    It contributed no quality data at all, and the run looked unremarkable in
    aggregate -- which is why it is checked on its own.
    """
    rows = _rows(2, 22) + [
        {"round": 2, "status": "provider_error", "question_id": f"q{i}"}
        for i in range(22)
    ]
    gate = provider_gate(rows, rounds=3, paired=11, cells=33)
    assert gate["empty_rounds"] == [2]
    assert "rounds_with_no_completions" in gate["failed"]
    assert gate["passed"] is False


def test_a_half_failed_run_fails_on_the_error_rate():
    """The measured shape of the first interleaved run: ~50% refused."""
    rows = (
        _rows(1, 11)
        + [{"round": 1, "status": "provider_error", "question_id": f"q{i}"} for i in range(11)]
        + [{"round": 2, "status": "provider_error", "question_id": f"q{i}"} for i in range(11)]
    )
    gate = provider_gate(rows, rounds=3, paired=11, cells=33)
    assert gate["observed"]["provider_error_rate"] > PROVIDER_GATE["provider_error_rate"]
    assert "provider_error_rate" in gate["failed"]
    assert gate["passed"] is False


def test_too_few_paired_cells_fails_even_when_the_provider_is_healthy():
    """Availability and comparability are different things.

    A run can complete every call and still not support an arm comparison if the
    pairs do not line up, so this is gated separately rather than inferred.
    """
    gate = provider_gate(_rows(3, 22), rounds=3, paired=10, cells=66)
    assert gate["observed"]["provider_error_rate"] == 0.0
    assert gate["observed"]["rounds_with_no_completions"] == 0
    assert gate["failed"] == ["paired_cell_share"]
    assert gate["passed"] is False


def test_the_thresholds_are_stated_not_implied():
    """A reader must be able to see what the gate demanded."""
    assert PROVIDER_GATE["provider_error_rate"] == 0.10
    assert PROVIDER_GATE["provider_timeout_rate"] == 0.10
    assert PROVIDER_GATE["rounds_with_no_completions"] == 0
    assert PROVIDER_GATE["paired_cell_share"] == 0.60


# ---------------------------------------------------------------------------
# One arm is allowed, and is the right shape for the partial set.
# ---------------------------------------------------------------------------


def test_a_single_arm_run_is_allowed_and_declares_no_pairing(tmp_path):
    """Five of the partial set's seven questions have every hop beyond position 5.

    The evidence-5 arm sees *zero* relevant evidence there, not less of it, so
    pairing would compare a number against a structural zero.  A single-arm run
    must therefore be expressible, and must say the pairing is not applicable
    instead of reporting an empty comparison as one.
    """
    import json
    import subprocess
    import sys

    output = tmp_path / "single.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "interleaved_generation_rounds.py"),
            "--case-set",
            "name=real_partial,"
            f"snapshot={ROOT / 'data' / 'frozen_multihop_real_partial.v2.jsonl'},"
            f"evaluation={ROOT / 'data' / 'generation_eval.multihop.real.partial.v2.json'}",
            "--arm", "evidenceall=99:none",
            "--rounds", "1",
            "--dry-run",
            "--output", str(output),
        ],
        capture_output=True,
        text=True,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 and "ModuleNotFoundError" in combined:
        pytest.skip("运行脚本需要模型依赖，离线解释器里跑不了")

    assert result.returncode == 0, combined[-800:]
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["summary"]["paired"]["comparable_pairs"] == 0
    assert "单臂" in payload["summary"]["paired"]["definition"]
    # The gate still judges availability: with one arm the cell count is how
    # many (question, round) cells produced a row.
    assert "paired_cell_share" in payload["summary"]["provider_gate"]["observed"]


def test_three_arms_are_rejected(tmp_path):
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "interleaved_generation_rounds.py"),
            "--case-set",
            "name=s,snapshot=x,evaluation=y",
            "--arm", "a=1:none", "--arm", "b=2:none", "--arm", "c=3:none",
            "--rounds", "1",
            "--dry-run",
            "--output", str(tmp_path / "x.json"),
        ],
        capture_output=True,
        text=True,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    if "ModuleNotFoundError" in combined:
        pytest.skip("运行脚本需要模型依赖")
    assert result.returncode != 0
    assert "1 或 2 个臂" in combined


# ---------------------------------------------------------------------------
# One arm is allowed, and is the right shape for the partial set.
# ---------------------------------------------------------------------------


def test_a_single_arm_run_is_allowed_and_declares_no_pairing(tmp_path):
    """Five of the partial set's seven questions have every hop beyond position 5.

    The evidence-5 arm sees *zero* relevant evidence there, not less of it, so
    pairing would compare a number against a structural zero.  A single-arm run
    must therefore be expressible, and must say the pairing is not applicable
    instead of reporting an empty comparison as one.
    """
    import json
    import subprocess
    import sys

    output = tmp_path / "single.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "interleaved_generation_rounds.py"),
            "--case-set",
            "name=real_partial,"
            f"snapshot={ROOT / 'data' / 'frozen_multihop_real_partial.v2.jsonl'},"
            f"evaluation={ROOT / 'data' / 'generation_eval.multihop.real.partial.v2.json'}",
            "--arm", "evidenceall=99:none",
            "--rounds", "1",
            "--dry-run",
            "--output", str(output),
        ],
        capture_output=True,
        text=True,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 and "ModuleNotFoundError" in combined:
        pytest.skip("运行脚本需要模型依赖，离线解释器里跑不了")

    assert result.returncode == 0, combined[-800:]
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["summary"]["paired"]["comparable_pairs"] == 0
    assert "单臂" in payload["summary"]["paired"]["definition"]
    # The gate still judges availability: with one arm the cell count is how
    # many (question, round) cells produced a row.
    assert "paired_cell_share" in payload["summary"]["provider_gate"]["observed"]


def test_three_arms_are_rejected(tmp_path):
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "interleaved_generation_rounds.py"),
            "--case-set",
            "name=s,snapshot=x,evaluation=y",
            "--arm", "a=1:none", "--arm", "b=2:none", "--arm", "c=3:none",
            "--rounds", "1",
            "--dry-run",
            "--output", str(tmp_path / "x.json"),
        ],
        capture_output=True,
        text=True,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    if "ModuleNotFoundError" in combined:
        pytest.skip("运行脚本需要模型依赖")
    assert result.returncode != 0
    assert "1 或 2 个臂" in combined
