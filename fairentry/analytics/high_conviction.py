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


def _durability_reason(evidence):
    agreement = (evidence or {}).get("agreement") or {}
    available = agreement.get("available")
    supportive = agreement.get("supportive")
    cautionary = agreement.get("cautionary")
    if all(isinstance(value, (int, float)) for value in (available, supportive, cautionary)):
        return (
            f"{supportive:g} of {available:g} available business-health groups are supportive "
            f"and {cautionary:g} are cautionary."
        )
    return (evidence or {}).get("label", "Business-health evidence is incomplete.")


def _stress_reason(evidence):
    summary = (evidence or {}).get("summary") or {}
    events = (evidence or {}).get("event_count")
    recovery = summary.get("recovered_within_one_year_pct")
    days = summary.get("median_recovery_days")
    protection = summary.get("median_relative_protection_pp")
    if isinstance(events, (int, float)) and isinstance(recovery, (int, float)):
        details = f"Across {events:g} past sector declines, {recovery:g}% recovered within one year"
        if isinstance(days, (int, float)):
            details += f"; the typical recovery took {days:g} trading days"
        if isinstance(protection, (int, float)):
            details += f", and the stock fell {protection:g} percentage points less than its sector on average"
        return details + ". Past results do not guarantee the next recovery."
    return (evidence or {}).get("label", "Past-stress evidence is incomplete.")


def build_high_conviction_research(*, verdict, price, price_is_fresh=None,
                                   price_freshness_limit_hours=None, vetoes, valuation_agreement,
                                   business_durability, stress_resilience,
                                   entry_exit_evidence, qualitative_context) -> dict:
    """Combine independent evidence without touching the official decision."""
    requirements = []
    requirements.append(_requirement(
        "official_buy", "Current FairEntry recommendation is Buy", "pass" if verdict == "Buy" else "fail",
        f"Today's FairEntry recommendation is {verdict or 'unavailable'}.", backtestable=True))
    valid_price = isinstance(price, (int, float)) and price > 0
    fresh = valid_price and price_is_fresh is True
    fresh_state = "pass" if fresh else "fail" if price_is_fresh is False or not valid_price else "unknown"
    requirements.append(_requirement(
        "fresh_price", "Current price is recent enough", fresh_state,
        (f"The price has a valid timestamp and is not older than {price_freshness_limit_hours:g} hours."
         if fresh and isinstance(price_freshness_limit_hours, (int, float)) else
         "The price and its timestamp passed the live board's freshness check.") if fresh else
        "The price is missing or too old." if fresh_state == "fail" else
        "A price is available, but its timestamp was not checked here.",
        backtestable=True))
    no_veto = not bool(vetoes)
    requirements.append(_requirement(
        "no_hard_veto", "No automatic disqualifier", "pass" if no_veto else "fail",
        "No tested rule automatically disqualifies this stock." if no_veto else "A tested rule automatically disqualifies this stock.", backtestable=True))

    valuation_pass = bool((valuation_agreement or {}).get("passes"))
    valuation_known = bool((valuation_agreement or {}).get("status"))
    requirements.append(_requirement(
        "valuation_agreement", "Fair-value estimates agree", "pass" if valuation_pass else "fail" if valuation_known else "unknown",
        (valuation_agreement or {}).get("explanation", "Valuation-agreement evidence is unavailable."), backtestable=True))

    durability_status = (business_durability or {}).get("status")
    requirements.append(_requirement(
        "business_durability", "Business performance is durable",
        "pass" if durability_status in {"strong", "stable"} else "fail" if durability_status == "weak" else "unknown",
        _durability_reason(business_durability), backtestable=True))

    stress_status = (stress_resilience or {}).get("status")
    requirements.append(_requirement(
        "stress_resilience", "Recovered acceptably from past market stress",
        "pass" if stress_status in {"strong", "acceptable"} else "fail" if stress_status == "weak" else "unknown",
        _stress_reason(stress_resilience), backtestable=True))

    entry = (entry_exit_evidence or {}).get("entry_alignment")
    requirements.append(_requirement(
        "market_evidence", "Price and volume evidence is supportive",
        "pass" if entry in {"supportive", "constructive"} else "fail" if entry == "cautionary" else "unknown",
        f"Entry evidence is {entry or 'unavailable'}; it remains context rather than a prediction.", backtestable=True))

    qualitative = _qualitative_rows(qualitative_context)
    critical_ids = {"management_execution", "policy_impact"}
    critical = {row.get("id"): row for row in qualitative if row.get("id") in critical_ids}
    completed_qualitative = [
        row for row in qualitative
        if row.get("direction") in {"positive", "negative", "mixed"}
        and row.get("observed_at") not in {None, ""}
        and str(row.get("source") or "").lower()
        not in {"", "ai review pending", "not available", "-"}
    ]
    high_negatives = [row for row in completed_qualitative
                      if row.get("direction") == "negative" and row.get("impact") == "high"
                      and row.get("confidence") in {"medium", "high"}]
    requirements.append(_requirement(
        "no_high_impact_qualitative_negative", "No major researched warning is known",
        "fail" if high_negatives else "pass" if completed_qualitative else "unknown",
        ("High-impact negative: " + "; ".join(row.get("label", row.get("id", "risk")) for row in high_negatives))
        if high_negatives else "No sourced high-impact negative was found in the completed qualitative review."
        if completed_qualitative else
        "No completed, dated qualitative review is available, so missing research is not treated as a Pass.",
        backtestable=False))
    completed_critical = all(
        critical.get(key) and critical[key].get("status") != "unknown"
        and critical[key].get("source") not in {None, "", "-", "AI review pending"}
        and critical[key].get("observed_at") not in {None, ""}
        for key in critical_ids)
    requirements.append(_requirement(
        "critical_research_complete", "Management and government-policy research is complete",
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
            "quantitative_core": "The numerical checks can be recalculated on old decision dates. However, we have not yet locked the final version of all nine checks and tested that unchanged version on later dates that were not used when choosing the rules.",
            "full_label": "A fair test of the complete label is not possible yet because most old decision dates do not have dated management and government-policy research.",
        },
        "score_effect": 0,
        "verdict_effect": "none",
        "automatic_trade_effect": "none",
        "validated_for_score": False,
        "policy": "This check is deliberately outside the official formula. It cannot add or subtract points, change Buy / Watch / Avoid, place a trade, or change position size.",
    }
