"""Transparent, information-only evidence about business durability.

The inputs are already stored point-in-time fundamentals, so the proposed
rules can be replayed.  They are deliberately *not* part of the production
score until an out-of-time backtest validates the complete rule.
"""
from __future__ import annotations

import math
import statistics


VERSION = "business_durability_research_v1"


def _number(value):
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _values(values):
    return [number for number in (_number(value) for value in values or [])
            if number is not None]


def _trend(values, *, tolerance=0.5, lower_is_better=False):
    values = _values(values)[-6:]
    if len(values) < 2:
        return None
    delta = values[-1] - values[0]
    if lower_is_better:
        delta = -delta
    return round(delta, 2), ("supportive" if delta >= tolerance else
                             "cautionary" if delta <= -tolerance else "mixed")


def _check(check_id, label, state, actual, rule, source, *, available=True):
    return {
        "id": check_id,
        "label": label,
        "state": state if available else "unavailable",
        "actual": actual if available else "Unavailable",
        "rule": rule,
        "source": source,
        "backtestable": True,
        "score_effect": 0,
        "verdict_effect": "none",
    }


def build_business_durability(metrics: dict, histories: dict[str, list[float]]) -> dict:
    """Return a replayable candidate durability classification.

    The classification is a frozen research hypothesis, not a validated Buy
    gate.  Every check is retained so the UI can show the exact value and rule.
    """
    revenue = _values(histories.get("rev_growth_qoq"))[-6:]
    gross = _values(histories.get("gross_margin"))[-6:]
    operating = _values(histories.get("oper_margin"))[-6:]
    pfcf = _values(histories.get("pfcf_ratio"))[-6:]

    checks = []
    if len(revenue) >= 3:
        positive_pct = round(sum(value > 0 for value in revenue) / len(revenue) * 100, 1)
        change = round(revenue[-1] - revenue[0], 1)
        volatility = round(statistics.pstdev(revenue), 1)
        revenue_state = ("supportive" if positive_pct >= 67 and change >= -10 and volatility <= 25
                         else "cautionary" if positive_pct < 50 or change < -20
                         else "mixed")
        actual = (f"{positive_pct:.0f}% positive; {change:+.1f} pp change; "
                  f"{volatility:.1f} pp variability across {len(revenue)} observations")
        checks.append(_check(
            "revenue_stability", "Revenue-growth stability", revenue_state, actual,
            "Supportive: at least 67% positive, no >10 pp decay, and variability at most 25 pp; cautionary below 50% positive or >20 pp decay.",
            "stored point-in-time revenue-growth history"))
    else:
        checks.append(_check("revenue_stability", "Revenue-growth stability", "unavailable", None,
                             "Requires at least 3 stored observations.",
                             "stored point-in-time revenue-growth history", available=False))

    margin_states = []
    margin_actual = []
    for name, values in (("gross", gross), ("operating", operating)):
        result = _trend(values)
        if result:
            delta, state = result
            margin_states.append(state)
            margin_actual.append(f"{name} {delta:+.1f} pp")
    margin_state = ("supportive" if margin_states and "cautionary" not in margin_states
                    and "supportive" in margin_states else
                    "cautionary" if margin_states.count("cautionary") >= 2 else
                    "mixed" if margin_states else "unavailable")
    checks.append(_check(
        "margin_direction", "Margin direction", margin_state,
        ", ".join(margin_actual) if margin_actual else None,
        "Supportive when at least one stored margin improves and neither deteriorates; cautionary when both deteriorate.",
        "stored point-in-time gross- and operating-margin history",
        available=bool(margin_states)))

    current_pfcf = _number(metrics.get("pfcf_ratio"))
    positive_cash = current_pfcf is not None and current_pfcf > 0
    pfcf_trend = _trend(pfcf, tolerance=1.0, lower_is_better=True)
    cash_state = ("supportive" if positive_cash and (pfcf_trend is None or pfcf_trend[1] != "cautionary")
                  else "cautionary" if current_pfcf is not None and current_pfcf <= 0
                  else "mixed" if current_pfcf is not None else "unavailable")
    cash_actual = (f"P/FCF {current_pfcf:.1f}x" if current_pfcf is not None else None)
    if pfcf_trend:
        cash_actual += f"; multiple trend {pfcf_trend[0]:+.1f}x"
    checks.append(_check(
        "cash_flow_quality", "Cash-flow support", cash_state, cash_actual,
        "Supportive when free cash flow is positive (positive P/FCF) and its multiple is not worsening materially. This is a cash-availability proxy, not an earnings-quality proof.",
        "current and stored point-in-time P/FCF", available=current_pfcf is not None))

    roic = _number(metrics.get("roic"))
    oper_now = _number(metrics.get("oper_margin"))
    profitability_state = ("supportive" if (roic is not None and roic >= 10)
                           and (oper_now is None or oper_now > 0) else
                           "cautionary" if (roic is not None and roic < 0)
                           or (oper_now is not None and oper_now < 0) else
                           "mixed" if roic is not None or oper_now is not None else "unavailable")
    checks.append(_check(
        "profitability", "Profitable reinvestment", profitability_state,
        f"ROIC {roic if roic is not None else 'n/a'}%; operating margin {oper_now if oper_now is not None else 'n/a'}%",
        "Supportive when ROIC is at least 10% and operating margin, when available, is positive; cautionary for negative ROIC or operating margin.",
        "current point-in-time fundamentals", available=roic is not None or oper_now is not None))

    debt = _number(metrics.get("debt_eq"))
    debt_change = _number(metrics.get("debt_to_assets_change_yoy_pp"))
    current_ratio = _number(metrics.get("current_ratio"))
    altman = _number(metrics.get("altman_z"))
    dilution = _number(metrics.get("share_count_yoy"))
    known = [value for value in (debt, debt_change, current_ratio, altman, dilution)
             if value is not None]
    protection_support = sum((debt is not None and debt <= .7,
                              debt_change is not None and debt_change <= 0,
                              current_ratio is not None and current_ratio >= 1.2,
                              altman is not None and altman >= 2.6,
                              dilution is not None and dilution <= 2))
    protection_risks = sum((debt is not None and debt > 2,
                            debt_change is not None and debt_change > 8,
                            current_ratio is not None and current_ratio < .8,
                            altman is not None and altman < 1.8,
                            dilution is not None and dilution > 10))
    protection_state = ("supportive" if len(known) >= 3 and protection_support >= 3 and protection_risks == 0
                        else "cautionary" if protection_risks >= 2
                        else "mixed" if known else "unavailable")
    checks.append(_check(
        "financial_protection", "Financial protection", protection_state,
        f"D/E {debt if debt is not None else 'n/a'}; debt change {debt_change if debt_change is not None else 'n/a'} pp; current ratio {current_ratio if current_ratio is not None else 'n/a'}; Altman-Z {altman if altman is not None else 'n/a'}; dilution {dilution if dilution is not None else 'n/a'}%",
        "Supportive with at least 3 protective observations and no severe risk; cautionary with at least 2 severe risks. Exact component thresholds are displayed in the evidence definition.",
        "current point-in-time balance-sheet and share-count fundamentals",
        available=bool(known)))

    available_checks = [row for row in checks if row["state"] != "unavailable"]
    supportive = sum(row["state"] == "supportive" for row in available_checks)
    cautionary = sum(row["state"] == "cautionary" for row in available_checks)
    if len(available_checks) < 4:
        status = "insufficient_data"
    elif supportive >= 4 and cautionary == 0:
        status = "strong"
    elif supportive >= 2 and cautionary <= 1:
        status = "stable"
    else:
        status = "weak"
    return {
        "version": VERSION,
        "status": status,
        "label": {"strong": "Strong durability evidence", "stable": "Stable durability evidence",
                  "weak": "Weak durability evidence", "insufficient_data": "Durability data incomplete"}[status],
        "checks": checks,
        "agreement": {"supportive": supportive, "cautionary": cautionary,
                      "available": len(available_checks), "required_available": 4},
        "backtestable": True,
        "validated_for_score": False,
        "score_effect": 0,
        "verdict_effect": "none",
        "policy": "Replayable research hypothesis only. It cannot change the official score or verdict unless chronological out-of-time validation later passes.",
    }
