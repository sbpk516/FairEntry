"""Contracts for information-only evidence.

Qualitative observations deliberately never contribute points.  These helpers
make their direction, materiality and uncertainty consistent enough for a
human to decide what deserves attention without disguising opinion as a
backtested score.
"""
from __future__ import annotations


DIRECTIONS = {"positive", "negative", "mixed", "uncertain"}
IMPACTS = {"low", "medium", "high", "critical"}
CONFIDENCES = {"low", "medium", "high"}
STATUSES = {"rumor", "proposed", "announced", "confirmed", "occurring", "unknown"}


def attention_level(direction: str, impact: str, confidence: str) -> str:
    """Return an action-oriented label, never a numeric score."""
    if direction not in {"negative", "mixed"}:
        return "note"
    if impact == "critical":
        return "immediate_review"
    if impact == "high":
        return {"high": "action_required", "medium": "reassess", "low": "closely_monitor"}[confidence]
    if impact == "medium":
        return "investigate" if confidence in {"medium", "high"} else "monitor"
    return "note"


def normalize_observation(row: dict, *, category: str, subcategory: str) -> dict:
    """Normalize provider evidence into the stable information-only schema."""
    direction = str(row.get("direction") or _direction_from_status(row.get("status"))).lower()
    impact = str(row.get("impact") or "unknown").lower()
    confidence = str(row.get("confidence") or "low").lower()
    event_status = str(row.get("event_status") or "unknown").lower()
    direction = direction if direction in DIRECTIONS else "uncertain"
    # Unknown impact stays explicit rather than being silently promoted to low.
    impact_for_attention = impact if impact in IMPACTS else "low"
    confidence = confidence if confidence in CONFIDENCES else "low"
    event_status = event_status if event_status in STATUSES else "unknown"
    return {
        "direction": direction,
        "impact": impact if impact in IMPACTS else "unknown",
        "confidence": confidence,
        "time_horizon": row.get("time_horizon") or "unknown",
        "event_status": event_status,
        "affected_area": row.get("affected_area") or "unknown",
        "attention_level": attention_level(direction, impact_for_attention, confidence),
        "recommended_response": row.get("recommended_response") or _response(
            attention_level(direction, impact_for_attention, confidence)
        ),
        "category": category,
        "subcategory": subcategory,
        "quantifiable": False,
        "score_effect": 0,
        "verdict_effect": "none",
    }


def _direction_from_status(status) -> str:
    return {
        "satisfied": "positive", "partial": "mixed", "failed": "negative",
        "contradicted": "negative", "unknown": "uncertain",
    }.get(str(status or "unknown").lower(), "uncertain")


def _response(level: str) -> str:
    return {
        "immediate_review": "Pause and review before acting",
        "action_required": "Require manual review before Buy",
        "reassess": "Reassess the investment thesis",
        "closely_monitor": "Closely monitor and verify",
        "investigate": "Investigate before acting",
        "monitor": "Monitor for confirmation",
        "note": "Note; no action from this item alone",
    }[level]
