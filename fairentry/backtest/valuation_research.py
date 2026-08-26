"""Point-in-time valuation challenger research.

Production valuation remains the frozen baseline.  These policies only remove
historical Buy observations that would not have satisfied a more selective
valuation contract.  They never rewrite production configuration, and every
input is taken from the original decision date.
"""
from __future__ import annotations

import math
import statistics

from fairentry.scoring.valuation_agreement import (
    MAX_DISPERSION_PCT,
    MIN_ROE_PCT,
    TARGET_UPSIDE_PCT,
    book_is_relevant as shared_book_is_relevant,
)

from fairentry.backtest.research_cycle import (
    _partition,
    _split_dates,
    _summary,
)
from fairentry.backtest.sfa_tune import _episode_roots


VERSION = 1
POLICIES = (
    {
        "id": "pb_relevance",
        "label": "P/B only for relevant businesses",
        "description": "Remove book-value fair prices from sectors and industries where tangible book value is not economically representative.",
        "book_relevance": True,
    },
    {
        "id": "pb_relevance_roe",
        "label": "Relevant P/B plus ROE quality",
        "description": "Apply the P/B relevance rule and require ROE of at least 8% before book value may support fair value.",
        "book_relevance": True, "book_roe": True,
    },
    {
        "id": "two_relevant_methods",
        "label": "At least two relevant methods",
        "description": "Require two available valuation methods after the P/B relevance and ROE checks.",
        "book_relevance": True, "book_roe": True, "minimum_methods": 2,
    },
    {
        "id": "method_agreement",
        "label": "Two methods with controlled dispersion",
        "description": "Require two relevant methods and keep the highest fair value no more than 75% above the lowest.",
        "book_relevance": True, "book_roe": True, "minimum_methods": 2,
        "maximum_dispersion_pct": MAX_DISPERSION_PCT,
    },
    {
        "id": "adjusted_ev_sales",
        "label": "Margin/growth-adjusted EV/Sales",
        "description": "Replace peer P/S with sector EV/Sales adjusted by point-in-time relative margin and reported-growth scores; require two relevant methods.",
        "book_relevance": True, "book_roe": True, "minimum_methods": 2,
        "adjust_ev_sales": True,
    },
    {
        "id": "variable_fcf_multiple",
        "label": "Historical quality-adjusted FCF multiple",
        "description": "Replace 18x with a 10x-26x multiple derived from dated reported growth, FCF margin, growth stability and debt direction.",
        "adjust_fcf": True,
        "production_eligible": False,
        "production_blocker": "The live board does not yet reproduce the identical multi-quarter stability inputs.",
    },
    {
        "id": "combined_replayable",
        "label": "Combined replayable valuation correction",
        "description": "Combine P/B relevance and ROE, two-method agreement, adjusted EV/Sales and the historical FCF multiple.",
        "book_relevance": True, "book_roe": True, "minimum_methods": 2,
        "maximum_dispersion_pct": MAX_DISPERSION_PCT,
        "adjust_ev_sales": True, "adjust_fcf": True,
        "production_eligible": False,
        "production_blocker": "Variable FCF inputs need identical live calculation before promotion.",
    },
)


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _item(row: dict, item_id: str, field: str = "actual"):
    for category in row.get("categories") or []:
        for item in category.get("items") or []:
            if item_id in {item.get("id"), item.get("metric")}:
                return _number(item.get(field))
    return None


def _research(row: dict, key: str):
    return _number((row.get("research_factors") or {}).get(key))


def book_is_relevant(row: dict) -> bool:
    return shared_book_is_relevant(row.get("sector"), row.get("industry"))


def _tested_methods(row: dict) -> list[dict]:
    return [
        {"key": method.get("key"), "fair": _number(method.get("fair"))}
        for method in ((row.get("valuation") or {}).get("methods") or [])
        if method.get("decision_status", "tested") == "tested"
        and _number(method.get("fair")) is not None
        and _number(method.get("fair")) > 0
    ]


def _adjusted_ev_sales_method(row: dict):
    current_ev_sales = _research(row, "enterprise_value_to_sales")
    peer_ev_sales = _research(row, "sector_median_enterprise_value_to_sales")
    ev_to_equity = _research(row, "enterprise_value_to_equity")
    price = _number(row.get("entry_price"))
    margin_score = _item(row, "gross_margin_vs_sector", "score")
    growth_score = _item(row, "revenue_growth", "score")
    if None in (current_ev_sales, peer_ev_sales, ev_to_equity, price,
                margin_score, growth_score) or current_ev_sales <= 0:
        return None
    # At neutral 50/100 relative scores, no premium is granted.  The combined
    # adjustment is capped at +/-40% and is fully disclosed for replay.
    adjustment = max(.60, min(1.40, 1 + (margin_score - 50) / 250
                              + (growth_score - 50) / 250))
    target_ev_sales = peer_ev_sales * adjustment
    fair_equity_ratio = 1 + ev_to_equity * (target_ev_sales / current_ev_sales - 1)
    if fair_equity_ratio <= 0:
        return None
    return {
        "key": "adjusted_ev_sales",
        "fair": price * fair_equity_ratio,
        "multiple": target_ev_sales,
        "adjustment": adjustment,
    }


def _adjusted_fcf_method(row: dict, method: dict):
    growth = _research(row, "revenue_growth_yoy_pct")
    fcf_margin = _research(row, "fcf_margin_pct")
    stability = _research(row, "revenue_growth_volatility_pct")
    debt_change = _research(row, "debt_to_assets_change_yoy_pp")
    if None in (growth, fcf_margin, stability, debt_change):
        return None
    multiple = 18.0
    multiple += max(-20, min(40, growth)) * .10
    multiple += max(-10, min(30, fcf_margin)) * .10
    multiple -= max(0, min(40, stability)) * .05
    multiple -= max(0, min(20, debt_change)) * .10
    multiple = max(10.0, min(26.0, multiple))
    fair_per_multiple = method["fair"] / 18.0
    return {"key": "quality_adjusted_fcf", "fair": fair_per_multiple * multiple,
            "multiple": multiple}


def evaluate_policy(row: dict, policy: dict) -> dict:
    """Evaluate a Buy-date observation without reading any later outcome."""
    price = _number(row.get("entry_price"))
    methods = _tested_methods(row)
    unavailable = []
    retained = []
    for method in methods:
        if method["key"] == "book" and policy.get("book_relevance"):
            if not book_is_relevant(row):
                continue
            if policy.get("book_roe"):
                roe = _research(row, "return_on_equity_pct")
                if roe is None or roe < MIN_ROE_PCT:
                    continue
        if method["key"] == "peer_ps" and policy.get("adjust_ev_sales"):
            adjusted = _adjusted_ev_sales_method(row)
            if adjusted is None:
                unavailable.append("adjusted_ev_sales")
            else:
                retained.append(adjusted)
            continue
        if method["key"] == "fcf" and policy.get("adjust_fcf"):
            adjusted = _adjusted_fcf_method(row, method)
            if adjusted is None:
                unavailable.append("quality_adjusted_fcf")
            else:
                retained.append(adjusted)
            continue
        retained.append(method)

    minimum = int(policy.get("minimum_methods", 1))
    fair_values = sorted(method["fair"] for method in retained)
    fair_base = statistics.median(fair_values) if fair_values else None
    upside = (fair_base / price - 1) * 100 if fair_base and price and price > 0 else None
    dispersion = ((fair_values[-1] / fair_values[0] - 1) * 100
                  if len(fair_values) >= 2 and fair_values[0] > 0 else None)
    maximum_dispersion = policy.get("maximum_dispersion_pct")
    passes = (
        len(retained) >= minimum
        and upside is not None and upside >= TARGET_UPSIDE_PCT
        and (maximum_dispersion is None
             or (dispersion is not None and dispersion <= maximum_dispersion))
    )
    return {
        "passes": passes,
        "method_count": len(retained),
        "methods": [method["key"] for method in retained],
        "fair_base": round(fair_base, 2) if fair_base is not None else None,
        "upside_pct": round(upside, 2) if upside is not None else None,
        "dispersion_pct": round(dispersion, 2) if dispersion is not None else None,
        "unavailable_adjustments": unavailable,
    }


def attach_valuation_factors(observations: list[dict], connection) -> dict:
    """Attach point-in-time EV/Sales inputs and same-date sector medians."""
    rows = [row for row in observations if row.get("verdict") == "Buy"
            and row.get("ticker") and row.get("decision_date")]
    if not rows:
        return {"observations": 0, "enriched": 0}
    import pandas as pd

    frame = pd.DataFrame({
        "observation_id": [row["observation_id"] for row in rows],
        "ticker": [row["ticker"] for row in rows],
        "decision_date": [row["decision_date"] for row in rows],
        "sector": [row.get("sector") for row in rows],
    })
    connection.register("valuation_observations", frame)
    try:
        result = connection.execute("""
        WITH cohort_sectors AS (
          SELECT DISTINCT CAST(decision_date AS DATE) decision_date,sector
          FROM valuation_observations WHERE sector IS NOT NULL
        ), peer_values AS (
          SELECT c.decision_date,c.sector,u.ticker,
                 d.ps*d.ev/nullif(d.marketcap,0) ev_sales
          FROM cohort_sectors c
          JOIN canonical_securities u ON u.sector=c.sector
            AND u.firstpricedate<=c.decision_date AND u.lastpricedate>=c.decision_date
          JOIN sfa_daily d ON d.ticker=u.ticker AND d.date=c.decision_date
          WHERE d.ps>0 AND d.ev>0 AND d.marketcap>0
        ), peer_summary AS (
          SELECT decision_date,sector,median(ev_sales) median_ev_sales,
                 count(*) peer_count
          FROM peer_values WHERE ev_sales>0 GROUP BY decision_date,sector
        )
        SELECT o.observation_id,d.ev,d.marketcap,d.ps,
               p.median_ev_sales,p.peer_count,art.roe
        FROM valuation_observations o
        LEFT JOIN LATERAL (
          SELECT ev,marketcap,ps FROM sfa_daily d0
          WHERE d0.ticker=o.ticker AND d0.date<=CAST(o.decision_date AS DATE)
          ORDER BY d0.date DESC LIMIT 1
        ) d ON true
        LEFT JOIN LATERAL (
          SELECT roe FROM sfa_fundamentals f0
          WHERE f0.ticker=o.ticker AND f0.dimension='ART'
            AND f0.datekey<=CAST(o.decision_date AS DATE)
          ORDER BY f0.reportperiod DESC,f0.datekey DESC LIMIT 1
        ) art ON true
        LEFT JOIN peer_summary p
          ON p.decision_date=CAST(o.decision_date AS DATE) AND p.sector=o.sector
        """).fetchall()
    finally:
        connection.unregister("valuation_observations")
    by_id = {}
    for observation_id, ev, marketcap, ps, peer_median, peer_count, roe in result:
        ev, marketcap, ps = _number(ev), _number(marketcap), _number(ps)
        ev_sales = ps * ev / marketcap if None not in (ps, ev, marketcap) and marketcap > 0 else None
        by_id[observation_id] = {
            "enterprise_value_to_sales": round(ev_sales, 4) if ev_sales else None,
            "enterprise_value_to_equity": round(ev / marketcap, 4)
            if ev is not None and marketcap and marketcap > 0 else None,
            "sector_median_enterprise_value_to_sales": round(float(peer_median), 4)
            if peer_median is not None and peer_count >= 10 else None,
            "sector_ev_sales_peer_count": int(peer_count or 0),
            "return_on_equity_pct": round(float(roe) * 100, 4)
            if roe is not None else None,
        }
    enriched = 0
    for row in rows:
        factors = by_id.get(row["observation_id"])
        if factors:
            row.setdefault("research_factors", {}).update(factors)
            enriched += 1
    return {"observations": len(rows), "enriched": enriched,
            "minimum_sector_peer_count": 10}


def _episodes(observations: list[dict], step_days: int):
    max_gap = max(step_days + 1, int(math.ceil(step_days * 1.5)))
    return _episode_roots(observations, {}, {"buy": 0, "watch": 0}, max_gap,
                          use_recorded_verdict=True)


def _strategy_summary(episodes: list[dict]):
    output = {}
    for strategy in ("deep_value", "quality_growth"):
        selected = [row for row in episodes if row.get("strategy_key") == strategy]
        output[strategy] = _summary(selected)["primary"]
    return output


def run_valuation_research(observations: list[dict], *, step_days: int = 30) -> dict:
    buy_rows = [row for row in observations if row.get("verdict") == "Buy"]
    baseline_episodes = _episodes(buy_rows, step_days)
    split_dates = _split_dates(baseline_episodes, {
        "development_share": .60, "validation_share": .20,
    })
    if not split_dates:
        return {"ok": False, "reason": "fewer than three historical Buy dates"}

    def splits(episodes):
        values = {name: _summary(_partition(episodes, dates))
                  for name, dates in split_dates.items()}
        values["all"] = _summary(episodes)
        values["by_strategy"] = _strategy_summary(episodes)
        return values

    baseline = splits(baseline_episodes)
    challengers = []
    for definition in POLICIES:
        evaluated = [(row, evaluate_policy(row, definition)) for row in buy_rows]
        selected_rows = [row for row, result in evaluated if result["passes"]]
        unavailable = sum(bool(result["unavailable_adjustments"])
                          for _row, result in evaluated)
        candidate_episodes = _episodes(selected_rows, step_days)
        result = splits(candidate_episodes)
        current = baseline["test"]["primary"]
        challenger = result["test"]["primary"]
        improvement = (round(challenger["success_rate_pct"] - current["success_rate_pct"], 1)
                       if None not in (challenger.get("success_rate_pct"),
                                       current.get("success_rate_pct")) else None)
        definition_public = {key: value for key, value in definition.items()
                             if key not in {"book_relevance", "book_roe", "minimum_methods",
                                            "maximum_dispersion_pct", "adjust_ev_sales", "adjust_fcf"}}
        challengers.append({
            **definition_public,
            "production_eligible": bool(definition.get("production_eligible", True)),
            "observations_evaluated": len(evaluated),
            "observations_selected": len(selected_rows),
            "unavailable_adjustments": unavailable,
            "results": result,
            "final_test_improvement_pp": improvement,
        })
    return {
        "ok": True,
        "version": VERSION,
        "objective": "Test whether more relevant and better-agreeing point-in-time valuation methods improve +30% within one-year Buy precision.",
        "production_effect": "none",
        "baseline_changed": False,
        "promotion": "manual only after stable unseen-period evidence and identical live calculation",
        "information_boundary": "Only values available on or before the original Buy date are used; later outcomes only measure results.",
        "fixed_rules": {
            "target_upside_pct": TARGET_UPSIDE_PCT,
            "book_minimum_roe_pct": MIN_ROE_PCT,
            "minimum_relevant_methods": 2,
            "maximum_method_dispersion_pct": MAX_DISPERSION_PCT,
            "sector_ev_sales_minimum_peers": 10,
        },
        "baseline": baseline,
        "challengers": challengers,
        "split": {name: {"first": min(dates), "last": max(dates),
                         "cohorts": len(dates)}
                  for name, dates in split_dates.items()},
        "non_backtestable_exclusions": [
            "Analyst expected growth and revisions",
            "Full WACC without historical risk-free-rate and equity-risk-premium series",
            "Recurring-revenue quality without a dated standardized history",
        ],
        "decision": "Research results only; current production valuation remains the baseline.",
    }
