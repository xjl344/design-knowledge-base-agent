"""The offline gates: invariants that must hold without a provider.

These are the checks that decide whether a run may be aggregated and averaged at
all.  They are cheap, deterministic and need no network -- which is exactly why
they should be gates rather than things someone remembers to look at.

The interpreter/subset contract for running them is in `docs/offline-gates.md`;
the short version is that these tests must pass under **either** interpreter,
because they must not depend on a model client being installed.
"""

from __future__ import annotations

import json

import pytest

from aggregate_generation_replays import aggregate


def _run(
    path,
    repetition,
    *,
    statuses=("completed",),
    retrieval_calls=0,
    dry_run=False,
    evaluation_sha256="evaluation",
    snapshot_sha256="snapshot",
    audit_version="audit-v2",
):
    payload = {
        "model": "gpt-5.6",
        "snapshot_sha256": snapshot_sha256,
        "evaluation_sha256": evaluation_sha256,
        "prompt_version": "p0",
        "audit_version": audit_version,
        "repetition_index": repetition,
        "llm_timeout_seconds": 180,
        "retrieval_calls": retrieval_calls,
        "dry_run": dry_run,
        "rows": [
            {
                "question_id": f"q{index:02d}_hit",
                "status": row_status,
                "generation_latency_seconds": 1.0,
                "audit": {
                    "required_term_recall": 1.0,
                    "citation_validity": True,
                    "citation_metric_applicable": True,
                    "source_coverage": 1.0,
                    "unsupported_number_count": 0,
                    "answer_length": 20,
                },
            }
            for index, row_status in enumerate(statuses, start=1)
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The freeze invariant.
# ---------------------------------------------------------------------------


def test_aggregate_rejects_a_run_that_called_retrieval(tmp_path):
    """`retrieval_calls == 0` is the freeze, and it has to be enforced here.

    Every arm comparison in this project assumes the evidence is identical
    across runs.  A run that retrieved would be comparing retrieval quality
    while reading the difference as generation quality.
    """
    paths = [
        _run(tmp_path / "r1.json", 1),
        _run(tmp_path / "r2.json", 2, retrieval_calls=3),
    ]
    with pytest.raises(ValueError, match="retrieval_calls"):
        aggregate(paths)


def test_aggregate_rejects_a_dry_run(tmp_path):
    """A dry run has no model answers, so averaging it in is averaging nothing."""
    paths = [_run(tmp_path / "r1.json", 1), _run(tmp_path / "r2.json", 2, dry_run=True)]
    with pytest.raises(ValueError):
        aggregate(paths)


# ---------------------------------------------------------------------------
# Determinism.
# ---------------------------------------------------------------------------


def test_aggregating_the_same_runs_twice_is_identical(tmp_path):
    """A difference between two aggregations must mean the inputs differ.

    Without this, a reported change cannot be distinguished from the aggregator
    being non-deterministic -- and the whole point of the offline path is that
    the same inputs give the same numbers.
    """
    paths = [
        _run(tmp_path / "r1.json", 1, statuses=("completed", "completed")),
        _run(tmp_path / "r2.json", 2, statuses=("completed", "provider_timeout")),
    ]
    first = aggregate(paths)
    second = aggregate(paths)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


# ---------------------------------------------------------------------------
# Availability must not be read as quality.
# ---------------------------------------------------------------------------


def test_provider_failures_stay_out_of_the_quality_denominator(tmp_path):
    """A missing row and a wrong row are different findings.

    The fallback text a failure produces is a fixed non-empty string, so it
    looks like an answer to anything that does not check the status.
    """
    paths = [
        _run(tmp_path / "r1.json", 1, statuses=("completed",)),
        _run(tmp_path / "r2.json", 2, statuses=("provider_timeout",)),
    ]
    result = aggregate(paths)
    assert result["completed_question_runs"] == 1
    assert result["required_term_metric_sample_size"] == 1
    # The quality mean is computed over the one completed row, not diluted by
    # the timeout row.
    assert result["required_term_recall_mean"] == 1.0
    assert result["provider_timeout_rate"] == 0.5


# ---------------------------------------------------------------------------
# Question sets must never be averaged together.
# ---------------------------------------------------------------------------


def test_runs_from_different_contracts_cannot_be_averaged(tmp_path):
    """Complete and partial questions are scored on different amounts of need.

    A partial question is credited on a subset of its own requirements, so it is
    inherently easier.  Pooling them makes set difficulty move with the
    admission ratio, which is the same class of error as a metric whose
    denominator is its own input.
    """
    paths = [
        _run(tmp_path / "complete.json", 1, evaluation_sha256="complete-v2"),
        _run(tmp_path / "partial.json", 2, evaluation_sha256="partial-v2"),
    ]
    with pytest.raises(ValueError, match="evaluation_sha256"):
        aggregate(paths)


def test_runs_from_different_audit_versions_cannot_be_averaged(tmp_path):
    """A v6 span score and a v7 one are not the same measurement."""
    paths = [
        _run(tmp_path / "v6.json", 1, audit_version="soft-audit-behaviour-v6"),
        _run(tmp_path / "v7.json", 2, audit_version="soft-audit-behaviour-v7"),
    ]
    with pytest.raises(ValueError, match="audit_version"):
        aggregate(paths)
