"""Unvalidated valuation context that never changes the tested decision."""
from __future__ import annotations

import math


VERSION = "valuation_context_v1"
RECURRING_HIGH = (
    "software", "subscription", "telecom", "wireless", "insurance",
    "data processing", "information technology services", "internet content",
)
RECURRING_MEDIUM = (
    "media", "entertainment", "advertising", "financial data", "health information",
)
RECURRING_LOW = (
    "restaurant", "apparel", "auto manufacturer", "consumer electronics",
    "home improvement", "travel", "leisure", "retail",
)


def _number(metrics, key):
    value = metrics.get(key, {})
    value = value.get("value") if isinstance(value, dict) else value
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


def _clamp(value, low=0.0, high=100.0):
    return max(low, min(high, value))


def _financing_proxy(metrics):
    beta, debt = _number(metrics, "beta"), _number(metrics, "debt_eq")
    if beta is None or debt is None:
        return None
    beta_component = _clamp((2.0 - beta) / 1.2 * 100)
    debt_component = _clamp((2.0 - debt) / 1.7 * 100)
    return round((beta_component + debt_component) / 2, 1)


def _recurring_proxy(industry):
    value = str(industry or "").lower()
    if any(term in value for term in RECURRING_HIGH):
        return 80.0, "Industry description commonly indicates repeat or subscription-like revenue."
    if any(term in value for term in RECURRING_MEDIUM):
        return 55.0, "Industry description suggests a mixture of recurring and transactional revenue."
    if any(term in value for term in RECURRING_LOW):
        return 30.0, "Industry description is generally transactional or economically cyclical."
    return None, "No standardized recurring-revenue history is available for this industry description."


def build_valuation_context(security: dict, metrics: dict) -> dict:
    financing = _financing_proxy(metrics)
    recurring, recurring_reason = _recurring_proxy(security.get("industry"))
    available = [value for value in (financing, recurring) if value is not None]
    score = round(sum(available) / len(available), 1) if available else None
    label = ("favorable context" if score is not None and score >= 70 else
             "mixed context" if score is not None and score >= 45 else
             "cautionary context" if score is not None else "unavailable")
    beta, debt = _number(metrics, "beta"), _number(metrics, "debt_eq")
    return {
        "version": VERSION,
        "label": "Experimental valuation context — NOT BACKTESTED",
        "experimental_context_score": score,
        "experimental_context_label": label,
        "backtestable": False,
        "decision_status": "information_only",
        "score_effect": 0,
        "verdict_effect": "none",
        "automatic_trade_effect": "none",
        "warning": "This separate experimental score is not the FairEntry score and must not be used to issue or upgrade a Buy recommendation.",
        "factors": [
            {
                "id": "full_wacc",
                "label": "Full WACC valuation",
                "value": None,
                "status": "not backtestable",
                "experimental_score": None,
                "reason": "The warehouse lacks a complete historical risk-free-rate and equity-risk-premium series. No WACC value is calculated.",
                "source": "Required historical series unavailable",
            },
            {
                "id": "financing_sensitivity_proxy",
                "label": "Financing-cost sensitivity proxy",
                "value": f"beta {beta:g}; debt/equity {debt:g}" if None not in (beta, debt) else None,
                "status": "experimental — not validated",
                "experimental_score": financing,
                "reason": "A transparent beta/debt sensitivity proxy; it is not WACC and live-provider beta is not identical to the historical calculated series.",
                "formula": "average(clamp((2.0−beta)/1.2×100), clamp((2.0−debt/equity)/1.7×100))",
                "source": "Current beta and debt/equity metrics",
            },
            {
                "id": "recurring_revenue_quality",
                "label": "Recurring-revenue quality",
                "value": security.get("industry") or None,
                "status": "subjective proxy — not backtestable",
                "experimental_score": recurring,
                "reason": recurring_reason,
                "formula": "Low-confidence industry-description heuristic; no company-specific recurring-revenue history",
                "source": "Current industry classification",
            },
        ],
    }
