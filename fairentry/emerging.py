"""Shared Emerging Research policy used by live screening and historical replay.

The evaluator is deliberately pure: it reads a dated input snapshot and returns
research labels and audit checks. It never writes scores, verdicts, positions,
or alerts.
"""
from __future__ import annotations

import math

POLICY_VERSION = "emerging_research_v2"
VARIANT_ORDER = ("broad", "balanced", "selective")


def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def liquidity_band(avg_dollar_volume) -> str:
    value = number(avg_dollar_volume)
    if value is None:
        return "unavailable"
    if value < 5_000_000:
        return "below_5m"
    if value < 10_000_000:
        return "5m_to_10m"
    if value < 20_000_000:
        return "10m_to_20m"
    return "20m_plus"


def _check(check_id, label, value, *, expected, passes, required=True):
    return {"id": check_id, "label": label, "value": value,
            "expected": expected, "passes": bool(passes), "required": required}


def _threshold(policy, variant, key, default):
    return ((policy.get("variants") or {}).get(variant) or {}).get(key, default)


def _minimum_check(check_id, label, value, minimum):
    numeric = number(value)
    return _check(check_id, label, numeric, expected=f">= {minimum:g}",
                  passes=numeric is not None and numeric >= minimum)


def evaluate_emerging_candidate(inputs: dict, policy: dict | None = None) -> dict:
    """Evaluate all three nested research variants from one as-of snapshot."""
    policy = policy or {}
    liquidity = number(inputs.get("avg_dollar_volume"))
    quality = number(inputs.get("business_quality"))
    financial = number(inputs.get("financial_strength"))
    growth = number(inputs.get("growth"))
    upside = number(inputs.get("valuation_upside_pct"))
    method_count = number(inputs.get("valuation_method_count"))
    agreement = inputs.get("valuation_agreement")
    alignment = inputs.get("entry_alignment")
    shadow_verdict = inputs.get("shadow_verdict")
    no_veto = bool(inputs.get("no_hard_veto"))
    discovery_min = number(policy.get("discovery_avg_dollar_volume_min")) or 5_000_000

    common = [
        _check("liquidity", "Average daily dollar volume", liquidity,
               expected=f">= ${discovery_min:,.0f}",
               passes=liquidity is not None and liquidity >= discovery_min),
        _check("no_hard_veto", "No hard veto", "none" if no_veto else "present",
               expected="none", passes=no_veto),
    ]
    definitions = {
        "broad": {
            "label": "Emerging · Basic Match",
            "verdicts": {"Buy", "Watch"},
            "quality": _threshold(policy, "broad", "minimum_business_quality", 50),
            "financial": _threshold(policy, "broad", "minimum_financial_strength", 60),
            "growth": _threshold(policy, "broad", "minimum_growth", 50),
            "upside": _threshold(policy, "broad", "minimum_valuation_upside_pct", 20),
        },
        "balanced": {
            "label": "Emerging · Strong Match",
            "verdicts": {"Buy", "Watch"},
            "quality": _threshold(policy, "balanced", "minimum_business_quality", 60),
            "financial": _threshold(policy, "balanced", "minimum_financial_strength", 60),
            "growth": _threshold(policy, "balanced", "minimum_growth", 50),
            "upside": _threshold(policy, "balanced", "minimum_valuation_upside_pct", 30),
            "methods": _threshold(policy, "balanced", "minimum_valuation_methods", 2),
            "alignments": {"supportive", "constructive", "mixed"},
        },
        "selective": {
            "label": "Emerging · Strict Match",
            "verdicts": {"Buy"},
            "quality": _threshold(policy, "selective", "minimum_business_quality", 70),
            "financial": _threshold(policy, "selective", "minimum_financial_strength", 70),
            "growth": _threshold(policy, "selective", "minimum_growth", 60),
            "upside": _threshold(policy, "selective", "minimum_valuation_upside_pct", 30),
            "methods": _threshold(policy, "selective", "minimum_valuation_methods", 2),
            "alignments": {"supportive", "constructive"},
            "agreement": "pass",
        },
    }
    variants = {}
    for variant in VARIANT_ORDER:
        definition = definitions[variant]
        checks = list(common) + [
            _check("shadow_verdict", "Shadow model evidence", shadow_verdict,
                   expected=" or ".join(sorted(definition["verdicts"])),
                   passes=shadow_verdict in definition["verdicts"]),
            _minimum_check("business_quality", "Business Quality", quality,
                           definition["quality"]),
            _minimum_check("financial_strength", "Financial Strength", financial,
                           definition["financial"]),
            _minimum_check("growth", "Growth Score", growth, definition["growth"]),
            _minimum_check("valuation_upside", "Median valuation upside", upside,
                           definition["upside"]),
        ]
        if "methods" in definition:
            checks.append(_minimum_check(
                "valuation_methods", "Relevant valuation methods", method_count,
                definition["methods"]))
        if "agreement" in definition:
            checks.append(_check(
                "valuation_agreement", "Valuation-method agreement", agreement,
                expected=definition["agreement"], passes=agreement == definition["agreement"]))
        if "alignments" in definition:
            checks.append(_check(
                "market_evidence", "Entry market evidence", alignment,
                expected=" or ".join(sorted(definition["alignments"])),
                passes=alignment in definition["alignments"]))
        variants[variant] = {
            "id": variant, "label": definition["label"],
            "passes": all(check["passes"] for check in checks),
            "checks": checks,
            "failed_checks": [check["label"] for check in checks if not check["passes"]],
        }

    matched = [variant for variant in VARIANT_ORDER if variants[variant]["passes"]]
    highest = matched[-1] if matched else None
    return {
        "qualifies": bool(matched),
        "highest_variant": highest,
        "matched_variants": matched,
        "variants": variants,
        "liquidity_band": liquidity_band(liquidity),
        "inputs": {
            "avg_dollar_volume": liquidity,
            "business_quality": quality,
            "financial_strength": financial,
            "growth": growth,
            "valuation_upside_pct": upside,
            "valuation_method_count": method_count,
            "valuation_agreement": agreement,
            "entry_alignment": alignment,
            "shadow_verdict": shadow_verdict,
            "no_hard_veto": no_veto,
        },
        "policy_version": policy.get("policy_version", POLICY_VERSION),
        "information_only": True,
        "not_validated": True,
        "score_effect": 0,
        "verdict_effect": "none",
    }
