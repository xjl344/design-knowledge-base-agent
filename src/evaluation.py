"""Quality metrics for claim-grounded design decision answers."""

from __future__ import annotations

from typing import Any


def decision_quality_metrics(audit: dict[str, Any]) -> dict[str, float]:
    claims = audit.get("claims", [])
    total = len(claims)
    supported = sum(claim.get("evidence_status") == "supported" for claim in claims)
    unsupported = len(audit.get("unsupported_claims", []))
    recommendations = audit.get("recommendation_conditions", [])
    traceable = sum(
        bool(item.get("conditions")) and item.get("evidence_status") == "supported"
        for item in recommendations
    )
    return {
        "claim_support_rate": round(supported / total, 3) if total else 1.0,
        "unsupported_claim_rate": round(unsupported / total, 3) if total else 0.0,
        "recommendation_traceability": round(traceable / len(recommendations), 3) if recommendations else 1.0,
        "conflict_count": float(len(audit.get("conflicts", []))),
        "scope_mismatch_count": float(sum(
            claim.get("evidence_status") == "scope_mismatch" for claim in claims
        )),
    }
