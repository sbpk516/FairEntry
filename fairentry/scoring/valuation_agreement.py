"""Shared, replayable valuation-method agreement policy.

The policy is deliberately separate from the production verdict.  Both the
historical challenger and the live board call this module so the displayed
shadow result cannot drift away from the tested definition.
"""
from __future__ import annotations

import math
import statistics


POLICY_VERSION = "valuation_method_agreement_v1"
TARGET_UPSIDE_PCT = 30.0
MIN_ROE_PCT = 8.0
MAX_DISPERSION_PCT = 75.0

BOOK_RELEVANT_SECTORS = {
    "financial services", "financial", "real estate", "utilities",
    "basic materials", "energy",
}
BOOK_RELEVANT_INDUSTRY_TERMS = (
    "bank", "insurance", "reit", "real estate", "mortgage", "asset management",
    "steel", "mining", "metals", "building materials", "homebuilding",
    "auto manufacturer", "farm & heavy", "railroad", "utility", "oil & gas",
)


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def book_is_relevant(sector=None, industry=None) -> bool:
    sector_text = str(sector or "").strip().lower()
    industry_text = str(industry or "").strip().lower()
    return sector_text in BOOK_RELEVANT_SECTORS or any(
        term in industry_text for term in BOOK_RELEVANT_INDUSTRY_TERMS
    )


def evaluate_method_agreement(methods: list[dict], price, *, sector=None,
                              industry=None, roe_pct=None) -> dict:
    """Evaluate the frozen method-agreement challenger without changing value.

    ``methods`` contains the baseline fair-value methods. Information-only
    methods are ignored. Book value is retained only for relevant businesses
    with ROE of at least 8%, exactly as in the point-in-time research policy.
    """
    price = _number(price)
    roe_pct = _number(roe_pct)
    retained = []
    excluded = []

    for method in methods or []:
        if method.get("decision_status", "tested") != "tested":
            continue
        fair = _number(method.get("fair"))
        if fair is None or fair <= 0:
            continue
        key = method.get("key")
        if key == "book":
            if not book_is_relevant(sector, industry):
                excluded.append({
                    "key": key,
                    "name": method.get("name", "Asset / book"),
                    "reason": "P/B is not relevant for this sector or industry.",
                })
                continue
            if roe_pct is None:
                excluded.append({
                    "key": key,
                    "name": method.get("name", "Asset / book"),
                    "reason": "ROE is unavailable; the 8% book-quality check cannot be replayed.",
                })
                continue
            if roe_pct < MIN_ROE_PCT:
                excluded.append({
                    "key": key,
                    "name": method.get("name", "Asset / book"),
                    "reason": f"ROE {roe_pct:.1f}% is below the 8% book-quality requirement.",
                })
                continue
        retained.append({"key": key, "name": method.get("name", key), "fair": fair})

    fair_values = sorted(method["fair"] for method in retained)
    fair_base = statistics.median(fair_values) if fair_values else None
    upside = ((fair_base / price - 1) * 100
              if fair_base is not None and price is not None and price > 0 else None)
    dispersion = ((fair_values[-1] / fair_values[0] - 1) * 100
                  if len(fair_values) >= 2 and fair_values[0] > 0 else None)

    enough_methods = len(retained) >= 2
    controlled_dispersion = bool(
        dispersion is not None and dispersion <= MAX_DISPERSION_PCT
    )
    enough_upside = bool(upside is not None and upside >= TARGET_UPSIDE_PCT)
    passes = enough_methods and controlled_dispersion and enough_upside

    if passes:
        status = "pass"
        label = "Valuation methods agree"
        explanation = (
            "At least two relevant methods support 30% upside and their highest "
            "estimate is no more than 75% above their lowest estimate."
        )
    elif not enough_methods:
        status = "insufficient_evidence"
        label = "Insufficient valuation methods"
        explanation = "Fewer than two relevant, replayable valuation methods are available."
    elif not controlled_dispersion:
        status = "caution"
        label = "Valuation methods disagree"
        explanation = (
            f"The method spread is {dispersion:.1f}%, above the tested 75% limit."
        )
    else:
        status = "caution"
        label = "Agreement lacks 30% upside"
        explanation = (
            "The relevant methods are sufficiently close, but their median does "
            "not support the fixed 30% one-year research objective."
        )

    return {
        "policy_version": POLICY_VERSION,
        "status": status,
        "label": label,
        "passes": passes,
        "explanation": explanation,
        "method_count": len(retained),
        "methods": [method["key"] for method in retained],
        "method_details": [
            {"key": method["key"], "name": method["name"],
             "fair": round(method["fair"], 2)} for method in retained
        ],
        "excluded_methods": excluded,
        "fair_base": round(fair_base, 2) if fair_base is not None else None,
        "upside_pct": round(upside, 1) if upside is not None else None,
        "dispersion_pct": round(dispersion, 1) if dispersion is not None else None,
        "thresholds": {
            "minimum_methods": 2,
            "minimum_upside_pct": TARGET_UPSIDE_PCT,
            "maximum_dispersion_pct": MAX_DISPERSION_PCT,
            "minimum_book_roe_pct": MIN_ROE_PCT,
        },
        "score_effect": 0,
        "verdict_effect": "none",
        "deployment_mode": "shadow",
        "historical_evidence": {
            "full_sample": {"successes": 131, "completed": 267,
                            "success_rate_pct": 49.1, "baseline_pct": 44.3},
            "final_unseen_sample": {"successes": 31, "completed": 68,
                                    "success_rate_pct": 45.6, "baseline_pct": 42.3},
            "validated_as_hard_gate": False,
        },
    }
