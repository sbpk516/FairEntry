"""Information-only High-Conviction Research overlay."""
from __future__ import annotations


VERSION = "high_conviction_research_v1"


def _requirement(key, label, state, reason, *, backtestable):
    return {"id": key, "label": label, "state": state, "passes": state == "pass",
            "reason": reason, "backtestable": backtestable, "score_effect": 0,
            "verdict_effect": "none"}


def _qualitative_rows(context):
    categories = (context or {}).get("categories") or {}
    rows = []
    for factors in categories.values():
        rows.extend(factors or [])
    return rows


def build_high_conviction_research(*, verdict, price, vetoes, valuation_agreement,
                                   business_durability, stress_resilience,
                                   entry_exit_evidence, qualitative_context) -> dict:
    """Combine independent evidence without touching the official decision."""
    requirements = []
    requirements.append(_requirement(
        "official_buy", "Official FairEntry Buy", "pass" if verdict == "Buy" else "fail",
        f"Official deterministic verdict is {verdict or 'unavailable'}.", backtestable=True))
    fresh = isinstance(price, (int, float)) and price > 0
    requirements.append(_requirement(
        "fresh_price", "Fresh usable price", "pass" if fresh else "fail",
        "The active board contains a usable price inside its freshness policy." if fresh else "Price is unavailable or stale.",
        backtestable=True))
    no_veto = not bool(vetoes)
    requirements.append(_requirement(
        "no_hard_veto", "No hard veto", "pass" if no_veto else "fail",
        "No tested hard veto is present." if no_veto else "A tested hard veto is present.", backtestable=True))

    valuation_pass = bool((valuation_agreement or {}).get("passes"))
    valuation_known = bool((valuation_agreement or {}).get("status"))
    requirements.append(_requirement(
        "valuation_agreement", "Valuation-method agreement", "pass" if valuation_pass else "fail" if valuation_known else "unknown",
        (valuation_agreement or {}).get("explanation", "Valuation-agreement evidence is unavailable."), backtestable=True))

    durability_status = (business_durability or {}).get("status")
    requirements.append(_requirement(
        "business_durability", "Strong or stable business durability",
        "pass" if durability_status in {"strong", "stable"} else "fail" if durability_status == "weak" else "unknown",
        (business_durability or {}).get("label", "Business-durability evidence is incomplete."), backtestable=True))

    stress_status = (stress_resilience or {}).get("status")
    requirements.append(_requirement(
        "stress_resilience", "Strong or acceptable stress resilience",
        "pass" if stress_status in {"strong", "acceptable"} else "fail" if stress_status == "weak" else "unknown",
        (stress_resilience or {}).get("label", "Stress-recovery evidence is incomplete."), backtestable=True))

    entry = (entry_exit_evidence or {}).get("entry_alignment")
    requirements.append(_requirement(
        "market_evidence", "Supportive or constructive market evidence",
        "pass" if entry in {"supportive", "constructive"} else "fail" if entry == "cautionary" else "unknown",
        f"Entry evidence is {entry or 'unavailable'}; it remains context rather than a prediction.", backtestable=True))

    qualitative = _qualitative_rows(qualitative_context)
    critical_ids = {"management_execution", "policy_impact"}
    critical = {row.get("id"): row for row in qualitative if row.get("id") in critical_ids}
    high_negatives = [row for row in qualitative
                      if row.get("direction") == "negative" and row.get("impact") == "high"
                      and row.get("confidence") in {"medium", "high"}]
    requirements.append(_requirement(
        "no_high_impact_qualitative_negative", "No known high-impact qualitative negative",
        "fail" if high_negatives else "pass" if qualitative else "unknown",
        ("High-impact negative: " + "; ".join(row.get("label", row.get("id", "risk")) for row in high_negatives))
        if high_negatives else "No sourced high-impact negative was found in the available qualitative review."
        if qualitative else "Qualitative research has not been completed.", backtestable=False))
    completed_critical = all(
        critical.get(key) and critical[key].get("status") != "unknown"
        and critical[key].get("source") not in {None, "", "-", "AI review pending"}
        and critical[key].get("observed_at") not in {None, ""}
        for key in critical_ids)
    requirements.append(_requirement(
        "critical_research_complete", "Management and policy research complete",
        "pass" if completed_critical else "unknown",
        "Both management execution and government-policy exposure have sourced, dated findings."
        if completed_critical else "Management and/or policy research remains unknown or lacks a specific source.",
        backtestable=False))

    quantitative = [row for row in requirements if row["backtestable"]]
    qualitative_reqs = [row for row in requirements if not row["backtestable"]]
    quant_pass = all(row["state"] == "pass" for row in quantitative)
    any_fail = any(row["state"] == "fail" for row in requirements)
    full_complete = all(row["state"] != "unknown" for row in requirements)
    if quant_pass and full_complete and not any_fail:
        status, label = "candidate", "High-Conviction Research Candidate"
    elif quant_pass and not any(row["state"] == "fail" for row in qualitative_reqs):
        status, label = "quantitative_core_pass_research_incomplete", "Quantitative core passes; research incomplete"
    else:
        status, label = "not_qualified", "Does not qualify for High-Conviction Research"
    completed = sum(row["state"] != "unknown" for row in requirements)
    return {
        "version": VERSION,
        "status": status,
        "label": label,
        "requirements": requirements,
        "quantitative_core_passes": quant_pass,
        "research_completeness": {"completed": completed, "required": len(requirements),
                                  "pct": round(completed / len(requirements) * 100, 1)},
        "backtestability": {
            "quantitative_core": "Replayable, but the combined rule is not yet validated for production.",
            "full_label": "Not currently backtestable because complete point-in-time management and government-policy histories are unavailable.",
        },
        "score_effect": 0,
        "verdict_effect": "none",
        "automatic_trade_effect": "none",
        "validated_for_score": False,
        "policy": "Additional research label only. It never changes the seven-category score, Buy/Watch/Avoid verdict, position size, or trade action.",
    }
