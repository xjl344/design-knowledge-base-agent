"""Export offline replay fixtures from recorded generation-replay runs.

Why this exists
---------------
The evaluator must be trustworthy independently of the model provider.  A
recorded run contains everything needed to re-derive every audit value: the
frozen evidence pack inputs, the answer the model actually produced, and the
audit it scored.  Freezing those into fixtures lets the scoring layer be
re-tested with no network, no API key and no model.

The fixtures deliberately capture *real* outputs — including provider failures
and malformed answers — rather than hand-written examples, so the regression
suite exercises the shapes the pipeline actually sees.

Usage::

    python export_replay_fixtures.py \
        --runs data/runs/p0_*.json \
        --output tests/fixtures/replay_fixtures.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.frozen_evidence import (  # noqa: E402
    AUDIT_VERSION,
    FALLBACK_ANSWER_WITH_EVIDENCE,
    FALLBACK_ANSWER_WITHOUT_EVIDENCE,
    build_evidence_pack,
    load_cases,
    soft_audit,
)

# Fields copied verbatim from the recorded row so a fixture can be replayed
# and compared field by field.
AUDIT_FIELDS = (
    "citation_status",
    "citation_validity",
    "citation_metric_applicable",
    "citation_id_usage_ratio",
    "span_metric_applicable",
    "expected_answer_span_recall",
    "required_term_recall",
    "required_term_metric_sample_size",
    "source_coverage",
    "unsupported_number_count",
    "refusal_correctness",
    "ambiguity_safety",
    # Multi-hop coverage.  Without these a replay would reproduce every field
    # except the one the v5 rule introduced, so the new rule would have no
    # offline regression coverage at all.
    "hop_recall",
    "hop_metric_applicable",
    # Attribution is scored against the answer's own claims; the v6 rule.
    "numeric_claim_citation_coverage",
    "numeric_citation_metric_applicable",
    "answer_length",
)


def _golden_by_id(evaluation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(case["id"]): case for case in evaluation.get("cases", [])}


# A run where every call succeeds would export a fixture set with no failure
# shape, and the regression suite would silently become a happy-path-only
# check -- exactly what these fixtures exist to prevent.  Once the latency
# ceiling stopped forcing timeouts, that became the normal case.
#
# So failure coverage is *synthesized* rather than waited for.  This is not a
# fabricated baseline: the answer text is the very constant the generator
# substitutes, and the recorded audit is recomputed by the same auditor, so a
# synthesized fixture asserts exactly what a recorded one would.  What it does
# not do is claim provenance it does not have -- ``source_run`` says
# ``synthesized`` and ``synthesized`` is True, so a reader can always tell the
# two apart.
SYNTHESIZED_FAILURE_STATUSES = ("provider_timeout", "provider_error")


def _synthesize_failure_fixtures(
    cases: dict[str, Any], spec_by_id: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build one failure fixture per distinct fallback shape.

    Two shapes exist because the generator distinguishes "had evidence but the
    model was unavailable" from "had no evidence to begin with"; both must be
    represented, since they take different audit branches.
    """
    chosen: list[tuple[str, str]] = []
    with_evidence = next(
        (qid for qid, case in cases.items() if build_evidence_pack(case, max_items=5).items),
        None,
    )
    without_evidence = next(
        (qid for qid, case in cases.items() if not build_evidence_pack(case, max_items=5).items),
        None,
    )
    if with_evidence:
        chosen.append((with_evidence, FALLBACK_ANSWER_WITH_EVIDENCE))
    if without_evidence:
        chosen.append((without_evidence, FALLBACK_ANSWER_WITHOUT_EVIDENCE))
    if not chosen:
        # Every case carries evidence; still emit one so the shape stays
        # covered rather than vanishing.
        first = next(iter(cases))
        chosen.append((first, FALLBACK_ANSWER_WITH_EVIDENCE))

    fixtures: list[dict[str, Any]] = []
    for index, (question_id, answer) in enumerate(chosen):
        status = SYNTHESIZED_FAILURE_STATUSES[index % len(SYNTHESIZED_FAILURE_STATUSES)]
        pack_config = {"max_items": 5, "max_chars_per_item": None}
        pack = build_evidence_pack(
            cases[question_id],
            max_items=pack_config["max_items"],
            max_chars_per_item=pack_config["max_chars_per_item"],
        )
        spec = spec_by_id.get(question_id, {})
        audit = soft_audit(
            answer,
            pack,
            expected_answer_spans=spec.get("expected_answer_spans", []),
            expected_sources=spec.get("expected_sources", []),
            required_terms=spec.get("required_terms", []),
            refusal_requirements=spec.get("refusal_requirements", []),
            ambiguity_requirements=spec.get("ambiguity_requirements", []),
        )
        fixtures.append(
            {
                "fixture_id": f"{question_id}__{status}",
                "question_id": question_id,
                "question": pack.question,
                "status": status,
                "source_run": "synthesized",
                "synthesized": True,
                "raw_answer": answer,
                "answer": answer,
                "sanitization_applied": False,
                "sanitization_rules": [],
                "generation_latency_seconds": None,
                "pack": dict(pack_config),
                "expected": {
                    "allowed_citations": list(pack.allowed_citations),
                    "expected_answer_spans": spec.get("expected_answer_spans", []),
                    "expected_sources": spec.get("expected_sources", []),
                    "required_terms": spec.get("required_terms", []),
                    "refusal_requirements": spec.get("refusal_requirements", []),
                    "ambiguity_requirements": spec.get("ambiguity_requirements", []),
                },
                "recorded_status": status,
                # No model call happened, so there is no generation-side row to
                # copy.  The replay path is the only thing under test here, and
                # its rule is the auditor's: recognise the substitute, emit no
                # behaviour verdict.
                "recorded_audit": {
                    key: audit.get(key) for key in AUDIT_FIELDS if key in audit
                },
            }
        )
    return fixtures


def export(
    run_paths: list[Path],
    *,
    snapshots: list[Path],
    evaluations: list[Path],
    ensure_failure_coverage: bool = True,
) -> dict[str, Any]:
    # Cases and their expectations can live in more than one file: the original
    # single-hop set and the composite multi-hop set are separate snapshots
    # *and* separate contracts, and a fixture exported from one cannot cover the
    # other.  Loading only the first silently dropped every multi-hop row, which
    # would have left the v5 hop rule with no offline regression coverage.
    cases: dict[str, Any] = {}
    for snapshot in snapshots:
        for question_id, case in load_cases(snapshot).items():
            cases.setdefault(question_id, case)
    spec_by_id: dict[str, dict[str, Any]] = {}
    for evaluation in evaluations:
        for question_id, spec in _golden_by_id(
            json.loads(evaluation.read_text(encoding="utf-8"))
        ).items():
            spec_by_id.setdefault(question_id, spec)
    fixtures: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    skipped: list[tuple[str, str]] = []

    for run_path in run_paths:
        run = json.loads(run_path.read_text(encoding="utf-8-sig"))
        # A fixture is only a valid regression baseline if its recorded values
        # were produced by the *current* scoring rules.  Runs recorded before
        # the rules changed carry either no version or a different one, and
        # their audits cannot be reproduced by this code -- replaying them
        # reports a drift that is really just a stale baseline.  Refuse the
        # whole run rather than silently mixing rule generations.
        run_version = run.get("audit_version")
        if run_version != AUDIT_VERSION:
            skipped.append((run_path.name, str(run_version)))
            continue
        # The pack the run actually scored must be the pack the replay rebuilds.
        # This used to be hardcoded to max_items=5 with no character budget, so
        # a fixture exported from a compressed run replayed against a *different*
        # (larger) pack and reported drift in unsupported_number_count and
        # citation_status that was really just a different context.
        pack_config = {
            "max_items": int(run.get("max_evidence") or 5),
            "max_chars_per_item": run.get("max_chars_per_item"),
        }
        for row in run.get("rows", []):
            question_id = str(row.get("question_id"))
            status = str(row.get("status"))
            # One fixture per (question, status) keeps the file small while
            # still covering every observed failure shape.
            if (question_id, status) in seen:
                continue
            seen.add((question_id, status))
            if question_id not in cases:
                continue
            spec = spec_by_id.get(question_id, {})
            pack = build_evidence_pack(
                cases[question_id],
                max_items=pack_config["max_items"],
                max_chars_per_item=pack_config["max_chars_per_item"],
            )
            recorded_audit = row.get("audit") or {}
            fixtures.append(
                {
                    "fixture_id": f"{question_id}__{status}",
                    "question_id": question_id,
                    "question": pack.question,
                    "status": status,
                    "source_run": run_path.name,
                    "raw_answer": row.get("raw_answer"),
                    "answer": row.get("answer"),
                    "sanitization_applied": row.get("sanitization_applied"),
                    "sanitization_rules": row.get("sanitization_rules") or [],
                    "generation_latency_seconds": row.get("generation_latency_seconds"),
                    # How to rebuild the evidence this row was scored against.
                    "pack": dict(pack_config),
                    "expected": {
                        "allowed_citations": list(pack.allowed_citations),
                        "expected_answer_spans": spec.get("expected_answer_spans", []),
                        "expected_sources": spec.get("expected_sources", []),
                        "required_terms": spec.get("required_terms", []),
                        "refusal_requirements": spec.get("refusal_requirements", []),
                        "ambiguity_requirements": spec.get("ambiguity_requirements", []),
                        # Carried so the replay reproduces hop_recall.  Absent
                        # for single-hop contracts, where an empty list is the
                        # correct input and yields no hop verdict.
                        "required_hops": spec.get("required_hops", []),
                    },
                    # Recorded audit values act as the regression baseline. A
                    # change here means the evaluator changed behaviour.
                    "recorded_audit": {
                        key: recorded_audit.get(key)
                        for key in AUDIT_FIELDS
                        if key in recorded_audit
                    },
                    "synthesized": False,
                    "recorded_status": status,
                }
            )

    if skipped:
        print(
            json.dumps(
                {
                    "warning": "跳过 audit_version 不匹配的运行",
                    "current_audit_version": AUDIT_VERSION,
                    "skipped": [{"run": name, "audit_version": version} for name, version in skipped],
                },
                ensure_ascii=False,
            )
        )
    # The guard has to run *before* coverage synthesis.  Synthesizing first
    # would make a run set that contributed nothing at all still produce a
    # fixture file, and the "wrong audit version" error would turn into a
    # silent success built entirely from synthesized rows.
    if not fixtures:
        raise SystemExit(
            f"没有任何运行匹配当前 AUDIT_VERSION={AUDIT_VERSION}。"
            "请用当前口径重新跑一次回放，再导出 fixtures。"
        )

    # Keep failure coverage even when every recorded call succeeded.  Without
    # this the suite quietly degenerates into a happy-path check the moment the
    # provider is healthy, which is precisely when nobody notices.
    #
    # Opt-out exists for callers that assert on an exact fixture set (the unit
    # tests feed one hand-written row and check it round-trips); it must stay
    # True on the real CLI path, which is what the regression suite reads.
    if ensure_failure_coverage and not any(
        fixture["status"] != "completed" for fixture in fixtures
    ):
        fixtures.extend(_synthesize_failure_fixtures(cases, spec_by_id))

    return {
        "fixture_version": 1,
        "audit_version": AUDIT_VERSION,
        "source_runs": [str(path.resolve()) for path in run_paths],
        "skipped_runs": [name for name, _ in skipped],
        "snapshots": [str(path.resolve()) for path in snapshots],
        "evaluations": [str(path.resolve()) for path in evaluations],
        "fixture_count": len(fixtures),
        "fixtures": fixtures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="从回放运行导出离线评测 fixtures")
    parser.add_argument("--runs", nargs="+", type=Path, required=True)
    parser.add_argument(
        "--snapshot",
        nargs="+",
        type=Path,
        # Single-hop and composite multi-hop cases live in separate files; pass
        # both to cover both, or the multi-hop rows are silently dropped.
        default=[
            ROOT / "data" / "frozen_retrieval_cases.jsonl",
            ROOT / "data" / "frozen_multihop_cases.jsonl",
        ],
    )
    parser.add_argument(
        "--evaluation",
        nargs="+",
        type=Path,
        # v2 is the contract that actually carries required_terms /
        # refusal_requirements / ambiguity_requirements. The older
        # generation_eval.json lacks them, which silently turns
        # required_term_recall into None instead of failing loudly.
        default=[
            ROOT / "data" / "generation_eval.v2.json",
            ROOT / "data" / "generation_eval.multihop.v1.json",
        ],
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    snapshots = [path for path in args.snapshot if path.exists()]
    evaluations = [path for path in args.evaluation if path.exists()]
    if not snapshots or not evaluations:
        raise SystemExit(f"快照或契约文件不存在：{args.snapshot} / {args.evaluation}")

    payload = export(args.runs, snapshots=snapshots, evaluations=evaluations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {"output": str(args.output.resolve()), "fixtures": payload["fixture_count"]},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
