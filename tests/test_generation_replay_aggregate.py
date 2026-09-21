import json

import pytest

from aggregate_generation_replays import aggregate


def _run(path, repetition, *, status="completed", statuses=None, latency=1.0, timeout_ceiling=60):
    """Write one replay run file.

    ``statuses`` lets a caller emit several rows in one file so that failure
    *rates* can be exercised, not just individual failure classes.
    """
    row_statuses = statuses if statuses is not None else [status]
    payload = {
        "model": "gpt-5.6",
        "snapshot_sha256": "snapshot",
        "evaluation_sha256": "evaluation",
        "prompt_version": "p0",
        "audit_version": "audit-v2",
        "repetition_index": repetition,
        "llm_timeout_seconds": timeout_ceiling,
        "retrieval_calls": 0,
        "dry_run": False,
        "rows": [
            {
                "question_id": f"q{index:02d}_hit",
                "status": row_status,
                "generation_latency_seconds": latency,
                "audit": {
                    "required_term_recall": 1.0,
                    "citation_validity": True,
                    "citation_metric_applicable": True,
                    "source_coverage": 1.0,
                    "unsupported_number_count": 0,
                    "answer_length": 20,
                },
            }
            for index, row_status in enumerate(row_statuses, start=1)
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_aggregate_preserves_all_repetitions_and_applies_provider_gate(tmp_path):
    paths = [_run(tmp_path / f"r{i}.json", i) for i in range(1, 4)]
    result = aggregate(paths)
    assert result["run_count"] == 3
    assert result["total_question_runs"] == 3
    assert result["provider_stability_gate_passed"] is True
    assert result["required_term_recall_mean"] == 1.0


def test_timeouts_are_separated_from_hard_errors(tmp_path):
    # The regression this guards: an earlier version counted timeouts into
    # provider_error_rate and produced a single number that could not tell a
    # tight latency ceiling apart from a broken service.
    path = _run(
        tmp_path / "r1.json",
        1,
        statuses=["completed"] * 8 + ["provider_timeout"] * 2,
    )
    result = aggregate([path])
    assert result["provider_timeout_rate"] == 0.2
    assert result["provider_hard_error_rate"] == 0.0
    assert result["provider_failure_rate"] == 0.2
    # Only the timeout gate may fail; the service is demonstrably healthy.
    assert result["provider_timeout_gate_passed"] is False
    assert result["provider_hard_error_gate_passed"] is True
    assert result["provider_stability_gate_passed"] is False


def test_hard_error_gate_fails_independently_of_timeouts(tmp_path):
    path = _run(
        tmp_path / "r1.json",
        1,
        statuses=["completed"] * 8 + ["provider_error"] * 2,
    )
    result = aggregate([path])
    assert result["provider_timeout_rate"] == 0.0
    assert result["provider_hard_error_rate"] == 0.2
    assert result["provider_timeout_gate_passed"] is True
    assert result["provider_hard_error_gate_passed"] is False


def test_rate_limit_counts_as_hard_error_not_timeout(tmp_path):
    path = _run(tmp_path / "r1.json", 1, statuses=["provider_rate_limit"] * 4)
    result = aggregate([path])
    assert result["provider_rate_limit_rate"] == 1.0
    assert result["provider_hard_error_rate"] == 1.0
    assert result["provider_timeout_rate"] == 0.0


def test_latency_headroom_exposes_a_tight_ceiling(tmp_path):
    path = _run(tmp_path / "r1.json", 1, statuses=["completed", "completed"], latency=55.0, timeout_ceiling=60)
    result = aggregate([path])
    assert result["latency_ceiling_seconds"] == 60
    assert result["latency_headroom_seconds"] == 5.0


def test_latency_headroom_is_none_without_a_ceiling(tmp_path):
    # Runs recorded before the ceiling field existed must not fabricate one.
    path = _run(tmp_path / "r1.json", 1)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("llm_timeout_seconds")
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = aggregate([path])
    assert result["latency_ceiling_seconds"] is None
    assert result["latency_headroom_seconds"] is None


def test_quality_metrics_ignore_provider_failures(tmp_path):
    # A timeout row carries no audit, so it must not be averaged into quality.
    path = _run(tmp_path / "r1.json", 1, statuses=["completed", "provider_timeout"])
    result = aggregate([path])
    assert result["generation_success_rate"] == 0.5
    assert result["required_term_recall_mean"] == 1.0
    assert result["completed_question_runs"] == 1



def test_aggregate_rejects_mixed_evaluation_hashes(tmp_path):
    first = _run(tmp_path / "r1.json", 1)
    second = _run(tmp_path / "r2.json", 2)
    payload = json.loads(second.read_text(encoding="utf-8"))
    payload["evaluation_sha256"] = "different"
    second.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="evaluation_sha256"):
        aggregate([first, second])


def test_aggregate_rejects_mixed_retry_policies(tmp_path):
    # A run with retries on has a different success distribution than one with
    # retries off, so averaging them would produce a meaningless number.
    first = _run(tmp_path / "r1.json", 1)
    second = _run(tmp_path / "r2.json", 2)
    payload = json.loads(second.read_text(encoding="utf-8"))
    payload["generation_max_retries"] = 1
    first_payload = json.loads(first.read_text(encoding="utf-8"))
    first_payload["generation_max_retries"] = 0
    first.write_text(json.dumps(first_payload), encoding="utf-8")
    second.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="generation_max_retries"):
        aggregate([first, second])


def test_latency_headroom_uses_per_call_latency_not_retry_total(tmp_path):
    # A retried row's total includes the failed attempt's full timeout, which
    # must not be mistaken for the cost of a successful call.
    path = tmp_path / "r1.json"
    payload = {
        "model": "gpt-5.6",
        "snapshot_sha256": "snapshot",
        "evaluation_sha256": "evaluation",
        "prompt_version": "p0",
        "audit_version": "audit-v2",
        "repetition_index": 1,
        "llm_timeout_seconds": 60,
        "generation_max_retries": 1,
        "retrieval_calls": 0,
        "dry_run": False,
        "rows": [
            {
                "question_id": "q17_ambiguous",
                "status": "completed",
                # Row total is large because attempt 1 burned a full timeout.
                "generation_latency_seconds": 102.7,
                "audit": {
                    "attempts": [
                        {"attempt": 1, "status": "provider_timeout", "latency_seconds": 60.0},
                        {"attempt": 2, "status": "completed", "latency_seconds": 42.7},
                    ],
                    "failed_attempt_count": 1,
                    "citation_metric_applicable": False,
                },
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = aggregate([path])
    assert result["per_attempt_latency_seconds"]["max"] == 42.7
    # Headroom must be positive: the successful call finished well inside 60s.
    assert result["latency_headroom_seconds"] == 17.3
    assert result["retried_success_count"] == 1


def test_aggregate_reports_retry_dependence(tmp_path):
    path = _run(tmp_path / "r1.json", 1, statuses=["completed"] * 3)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["generation_max_retries"] = 1
    for row in payload["rows"]:
        row["audit"]["attempts"] = [
            {"attempt": 1, "status": "completed", "latency_seconds": 1.0}
        ]
        row["audit"]["failed_attempt_count"] = 0
    payload["rows"][0]["audit"]["failed_attempt_count"] = 1
    payload["rows"][0]["audit"]["attempts"] = [
        {"attempt": 1, "status": "provider_timeout", "latency_seconds": 60.0},
        {"attempt": 2, "status": "completed", "latency_seconds": 2.0},
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = aggregate([path])
    assert result["first_try_success_count"] == 2
    assert result["retried_success_count"] == 1
    assert result["retried_success_question_ids"] == ["q01_hit"]
