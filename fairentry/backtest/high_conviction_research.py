"""Chronological research for the replayable High-Conviction subset.

This report intentionally stops short of validating the full live label.  The
warehouse has the dated fundamentals, valuation and market inputs used below,
but not standardized historical management/policy research.  Prior stress
events also require a date-aligned recovery attachment before the complete
quantitative core can be promoted.
"""
from __future__ import annotations

import math

from fairentry.backtest.research_cycle import _partition, _split_dates, _summary
from fairentry.backtest.sfa_tune import _episode_roots
from fairentry.backtest.valuation_research import POLICIES, evaluate_policy


VERSION = "high_conviction_research_v1"
AGREEMENT_POLICY = next(policy for policy in POLICIES if policy["id"] == "method_agreement")


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _factor(row, key):
    return _number((row.get("research_factors") or {}).get(key))


def evaluate_replayable_subset(row: dict) -> dict:
    """Evaluate only Buy-date inputs already attached by the SFA replay."""
    valuation = evaluate_policy(row, AGREEMENT_POLICY)
    revenue_positive = _factor(row, "revenue_growth_positive_quarters_pct")
    growth_change = _factor(row, "revenue_growth_change_pp")
    growth_volatility = _factor(row, "revenue_growth_volatility_pct")
    eps_positive = _factor(row, "positive_eps_quarters_pct")
    margin_change = _factor(row, "operating_margin_change_qoq_pp")
    fcf_margin = _factor(row, "fcf_margin_pct")
    dilution = _factor(row, "share_count_change_yoy_pct")
    debt_change = _factor(row, "debt_to_assets_change_yoy_pp")

    business_checks = {
        "revenue_stability": (None if None in (revenue_positive, growth_change, growth_volatility)
                              else revenue_positive >= 67 and growth_change >= -10
                              and growth_volatility <= 25),
        "earnings_consistency": None if eps_positive is None else eps_positive >= 75,
        "margin_direction": None if margin_change is None else margin_change >= -.5,
        "cash_flow_quality": None if fcf_margin is None else fcf_margin > 0,
        "debt_and_dilution_control": (None if debt_change is None and dilution is None else
                                      (debt_change is None or debt_change <= 5)
                                      and (dilution is None or dilution <= 5)),
    }
    known_business = [value for value in business_checks.values() if value is not None]
    business_pass = len(known_business) >= 4 and sum(known_business) >= 4

    price_200 = _factor(row, "price_above_sma200_pct")
    sector_trend = _factor(row, "sector_trend_score")
    market_trend = _factor(row, "market_trend_score")
    market_checks = {
        "price_above_200dma": None if price_200 is None else price_200 >= 0,
        "sector_trend": None if sector_trend is None else sector_trend >= 67,
        "market_trend": None if market_trend is None else market_trend >= 67,
    }
    known_market = [value for value in market_checks.values() if value is not None]
    market_pass = len(known_market) >= 2 and sum(known_market) >= 2
    no_veto = not bool(row.get("vetoes"))
    passes = bool(valuation["passes"] and business_pass and market_pass and no_veto)
    return {
        "passes": passes,
        "valuation_agreement": valuation["passes"],
        "business_durability": business_pass,
        "market_confirmation": market_pass,
        "no_hard_veto": no_veto,
        "business_checks": business_checks,
        "market_checks": market_checks,
        "stress_resilience_included": False,
        "qualitative_research_included": False,
    }


def _episodes(rows, step_days):
    max_gap = max(step_days + 1, int(math.ceil(step_days * 1.5)))
    return _episode_roots(rows, {}, {"buy": 0, "watch": 0}, max_gap,
                          use_recorded_verdict=True)


def run_high_conviction_research(observations: list[dict], *, step_days=30) -> dict:
    buy_rows = [row for row in observations if row.get("verdict") == "Buy"]
    baseline_episodes = _episodes(buy_rows, step_days)
    split_dates = _split_dates(baseline_episodes, {
        "development_share": .60, "validation_share": .20,
    })
    if not split_dates:
        return {"ok": False, "reason": "fewer than three historical Buy dates"}
    evaluated = [(row, evaluate_replayable_subset(row)) for row in buy_rows]
    selected_rows = [row for row, result in evaluated if result["passes"]]
    selected_episodes = _episodes(selected_rows, step_days)

    def split_summary(episodes):
        result = {name: _summary(_partition(episodes, dates))
                  for name, dates in split_dates.items()}
        result["all"] = _summary(episodes)
        return result

    baseline = split_summary(baseline_episodes)
    selected = split_summary(selected_episodes)
    baseline_test = baseline["test"]["primary"]
    selected_test = selected["test"]["primary"]
    improvement = None
    if baseline_test.get("success_rate_pct") is not None and selected_test.get("success_rate_pct") is not None:
        improvement = round(selected_test["success_rate_pct"] - baseline_test["success_rate_pct"], 1)
    return {
        "ok": True,
        "version": VERSION,
        "objective": "Measure whether the replayable pre-stress subset separates +30%-within-one-year Buy outcomes.",
        "baseline": baseline,
        "replayable_subset": selected,
        "observations_evaluated": len(evaluated),
        "observations_selected": len(selected_rows),
        "final_test_improvement_pp": improvement,
        "rules": {
            "valuation": "Two relevant methods, at least 30% median upside, no more than 75% dispersion.",
            "business": "At least 4 of 5 available durability families pass, with at least 4 available.",
            "market": "At least 2 of price above 200DMA, supportive sector trend and supportive market trend.",
            "veto": "No historical hard veto.",
        },
        "full_rule_validated": False,
        "production_effect": "none",
        "score_effect": 0,
        "verdict_effect": "none",
        "promotion_allowed": False,
        "missing_for_full_replay": [
            "date-aligned prior stress-event protection and recovery factors",
            "an identical durability-feature definition in live and historical data",
            "point-in-time management execution research",
            "point-in-time government-policy exposure and alignment research",
        ],
        "interpretation": "This is a fixed descriptive challenger, not a tuned production rule. Even a positive result cannot change scoring until the complete quantitative core is replayed and survives unseen periods.",
    }
