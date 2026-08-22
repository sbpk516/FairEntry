"""Factor-level Buy success comparison with rolling walk-forward exploration.

Only ratios and signals frozen on or before each Buy date are used. Thresholds
are learned from older episodes and evaluated on the next chronological fold.
The explorer is research-only: its changing fold rules are not deployable and
can never alter production scoring.
"""
from __future__ import annotations

import itertools
import math
import statistics

from fairentry.backtest.evidence import _fixed_horizon_evaluation
from fairentry.backtest.research_cycle import _number, _target_summary, _wilson, factor_value
from fairentry.backtest.sfa_tune import _episode_roots


FACTOR_DEFINITIONS = (
    {
        "id": "growth_deceleration",
        "label": "Revenue growth acceleration / deceleration",
        "field": "research.revenue_growth_change_pp",
        "direction": "higher",
        "unit": "percentage points",
        "theme": "growth_deceleration",
        "meaning": "Current year-over-year quarterly revenue growth minus the prior-year growth rate; negative means deceleration.",
    },
    {
        "id": "margin_direction",
        "label": "Operating-margin direction",
        "field": "research.operating_margin_change_qoq_pp",
        "direction": "higher",
        "unit": "percentage points",
        "theme": "margin_direction",
        "meaning": "Current quarterly operating margin minus the preceding quarter's operating margin.",
    },
    {
        "id": "cash_flow_quality",
        "label": "Free-cash-flow margin",
        "field": "research.fcf_margin_pct",
        "direction": "higher",
        "unit": "%",
        "theme": "cash_flow_quality",
        "meaning": "Trailing free cash flow divided by trailing revenue, using the filing available on the Buy date.",
    },
    {
        "id": "valuation_to_growth",
        "label": "P/E relative to revenue growth",
        "field": "research.pe_to_revenue_growth",
        "direction": "lower",
        "unit": "x",
        "theme": "valuation_to_growth",
        "meaning": "Point-in-time trailing P/E divided by positive year-over-year revenue growth; lower is less demanding.",
    },
    {
        "id": "relative_strength",
        "label": "Relative strength",
        "field": "metric.relative_strength_score",
        "direction": "higher",
        "unit": "/100",
        "theme": "market_confirmation",
        "meaning": "Three-month stock performance relative to its sector benchmark and SPY.",
    },
    {
        "id": "trend_regime",
        "label": "Trend regime",
        "field": "metric.trend_regime_score",
        "direction": "higher",
        "unit": "/100",
        "theme": "market_confirmation",
        "meaning": "Price and 50/200-day moving-average trend checks available on the Buy date.",
    },
)

DEFAULT_POLICY = {
    "folds": 5,
    "minimum_training_completed": 40,
    "minimum_training_unique_issuers": 25,
    "minimum_fold_completed": 10,
    "minimum_total_oos_completed": 50,
    "minimum_oos_improvement_pp": 5.0,
    "minimum_oos_success_rate_pct": 55.0,
    "maximum_drawdown_deterioration_pp": 2.0,
    "quantiles": (0.25, 0.50, 0.75),
}


def _ratio(numerator, denominator, scale=1.0):
    a, b = _number(numerator), _number(denominator)
    return a / b * scale if a is not None and b not in {None, 0} else None


def attach_warehouse_factors(observations: list[dict], connection) -> dict:
    """Attach derived as-of-date ratios to Buy observations in one warehouse query."""
    rows = [row for row in observations
            if row.get("verdict") == "Buy" and row.get("ticker") and row.get("decision_date")]
    if not rows:
        return {"observations": 0, "enriched": 0}
    import pandas as pd

    frame = pd.DataFrame({
        "observation_id": [row["observation_id"] for row in rows],
        "ticker": [row["ticker"] for row in rows],
        "decision_date": [row["decision_date"] for row in rows],
    })
    connection.register("factor_observations", frame)
    try:
        result = connection.execute("""
        WITH available_arq AS (
          SELECT o.observation_id,f.ticker,f.datekey,f.reportperiod,f.calendardate,
                 f.revenue,f.opinc,
                 row_number() OVER (
                   PARTITION BY o.observation_id,coalesce(f.calendardate,f.reportperiod)
                   ORDER BY f.datekey DESC,f.reportperiod DESC
                 ) AS revision_rank
          FROM factor_observations o
          JOIN sfa_fundamentals f
            ON f.ticker=o.ticker AND f.dimension='ARQ'
           AND f.datekey<=CAST(o.decision_date AS DATE)
        ), arq_history AS (
          SELECT observation_id,ticker,datekey,reportperiod,calendardate,revenue,opinc,
                 lag(revenue) OVER w AS revenue_prev_q,
                 lag(revenue,4) OVER w AS revenue_prev_y,
                 lag(revenue,8) OVER w AS revenue_prev_2y,
                 lag(opinc) OVER w AS opinc_prev_q,
                 row_number() OVER (
                   PARTITION BY observation_id
                   ORDER BY coalesce(calendardate,reportperiod) DESC,datekey DESC
                 ) AS latest_rank
          FROM available_arq WHERE revision_rank=1
          WINDOW w AS (
            PARTITION BY observation_id
            ORDER BY coalesce(calendardate,reportperiod),datekey
          )
        )
        SELECT o.observation_id,
               arq.revenue,arq.revenue_prev_q,arq.revenue_prev_y,arq.revenue_prev_2y,
               arq.opinc,arq.opinc_prev_q,
               art.revenue art_revenue,art.fcf,
               daily.pe
        FROM factor_observations o
        LEFT JOIN LATERAL (
          SELECT * FROM arq_history a
          WHERE a.observation_id=o.observation_id AND a.latest_rank=1
        ) arq ON true
        LEFT JOIN LATERAL (
          SELECT ticker,datekey,reportperiod,revenue,fcf
          FROM sfa_fundamentals a
          WHERE a.ticker=o.ticker AND a.datekey<=CAST(o.decision_date AS DATE)
            AND a.dimension='ART'
          ORDER BY a.reportperiod DESC,a.datekey DESC LIMIT 1
        ) art ON true
        LEFT JOIN LATERAL (
          SELECT pe FROM sfa_daily d
          WHERE d.ticker=o.ticker AND d.date<=CAST(o.decision_date AS DATE)
          ORDER BY d.date DESC LIMIT 1
        ) daily ON true
        """).fetchall()
    finally:
        connection.unregister("factor_observations")
    by_id = {}
    for (observation_id, revenue, revenue_prev_q, revenue_prev_y, revenue_prev_2y,
         opinc, opinc_prev_q, art_revenue, fcf, pe) in result:
        revenue_number = _number(revenue)
        revenue_prev_y_number = _number(revenue_prev_y)
        revenue_prev_2y_number = _number(revenue_prev_2y)
        current_growth = (_ratio(revenue_number - revenue_prev_y_number,
                                 revenue_prev_y_number, 100)
                          if None not in (revenue_number, revenue_prev_y_number) else None)
        prior_growth = (_ratio(revenue_prev_y_number - revenue_prev_2y_number,
                               revenue_prev_2y_number, 100)
                        if None not in (revenue_prev_y_number, revenue_prev_2y_number) else None)
        current_margin = _ratio(opinc, revenue, 100)
        prior_margin = _ratio(opinc_prev_q, revenue_prev_q, 100)
        fcf_margin = _ratio(fcf, art_revenue, 100)
        pe_number = _number(pe)
        by_id[observation_id] = {
            "revenue_growth_yoy_pct": round(current_growth, 4) if current_growth is not None else None,
            "prior_revenue_growth_yoy_pct": round(prior_growth, 4) if prior_growth is not None else None,
            "revenue_growth_change_pp": round(current_growth - prior_growth, 4)
            if None not in (current_growth, prior_growth) else None,
            "operating_margin_change_qoq_pp": round(current_margin - prior_margin, 4)
            if None not in (current_margin, prior_margin) else None,
            "fcf_margin_pct": round(fcf_margin, 4) if fcf_margin is not None else None,
            "pe_to_revenue_growth": round(pe_number / current_growth, 4)
            if pe_number is not None and pe_number > 0
            and current_growth is not None and current_growth > 0 else None,
        }
    enriched = 0
    for row in rows:
        factors = by_id.get(row["observation_id"])
        if factors:
            row["research_factors"] = factors
            enriched += 1
    return {"observations": len(rows), "enriched": enriched}


def _percentile(values: list[float], fraction: float):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lo, hi = math.floor(index), math.ceil(index)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)


def _completed(rows: list[dict]):
    output = []
    for row in rows:
        state = _fixed_horizon_evaluation(row, 30, 365)["result"]
        if state in {"success", "failure"}:
            output.append((row, state == "success"))
    return output


def _distribution(values: list[float]):
    return {
        "n": len(values),
        "median": round(statistics.median(values), 2) if values else None,
        "p25": round(_percentile(values, .25), 2) if values else None,
        "p75": round(_percentile(values, .75), 2) if values else None,
    }


def compare_factors(episodes: list[dict], factors=FACTOR_DEFINITIONS) -> list[dict]:
    completed = _completed(episodes)
    output = []
    for factor in factors:
        success, failure = [], []
        for row, won in completed:
            value = factor_value(row, factor["field"])
            if isinstance(value, (int, float)):
                (success if won else failure).append(float(value))
        covered = len(success) + len(failure)
        success_median = statistics.median(success) if success else None
        failure_median = statistics.median(failure) if failure else None
        difference = (success_median - failure_median
                      if None not in (success_median, failure_median) else None)
        aligned = (difference is not None and
                   (difference > 0 if factor["direction"] == "higher" else difference < 0))
        output.append({
            **factor,
            "completed_episodes": len(completed),
            "covered_episodes": covered,
            "coverage_pct": round(covered / len(completed) * 100, 1) if completed else None,
            "successes_with_data": len(success),
            "failures_with_data": len(failure),
            "success_distribution": _distribution(success),
            "failure_distribution": _distribution(failure),
            "median_difference_success_minus_failure": round(difference, 2)
            if difference is not None else None,
            "direction_matches_hypothesis": aligned,
            "descriptive_only": True,
        })
    return output


def _fold_date_sets(episodes: list[dict], folds: int):
    dates = sorted({row.get("decision_date") or row.get("entry_date") for row in episodes})
    if len(dates) < folds:
        folds = max(3, len(dates))
    chunks = []
    for index in range(folds):
        start = round(index * len(dates) / folds)
        end = round((index + 1) * len(dates) / folds)
        if dates[start:end]:
            chunks.append(set(dates[start:end]))
    return chunks


def _condition(factor: dict, threshold: float):
    return {"factor_id": factor["id"], "field": factor["field"],
            "op": ">=" if factor["direction"] == "higher" else "<=",
            "value": round(float(threshold), 4)}


def _passes(row: dict, conditions: list[dict]):
    for condition in conditions:
        value = factor_value(row, condition["field"])
        if not isinstance(value, (int, float)):
            return False
        if condition["op"] == ">=" and value < condition["value"]:
            return False
        if condition["op"] == "<=" and value > condition["value"]:
            return False
    return True


def _candidate_catalog(training: list[dict], factors: tuple[dict, ...], quantiles):
    thresholds = {}
    for factor in factors:
        values = [factor_value(row, factor["field"]) for row, _won in _completed(training)]
        values = [float(value) for value in values if isinstance(value, (int, float))]
        thresholds[factor["id"]] = [
            _condition(factor, value) for q in quantiles
            if (value := _percentile(values, q)) is not None
        ]
    candidates = []
    for factor in factors:
        for condition in thresholds[factor["id"]]:
            candidates.append({"id": factor["id"], "conditions": [condition]})
    # Pair median thresholds only. This limits multiple testing while allowing
    # the requested interaction exploration.
    for left, right in itertools.combinations(factors, 2):
        if thresholds[left["id"]] and thresholds[right["id"]]:
            candidates.append({
                "id": f"{left['id']}+{right['id']}",
                "conditions": [thresholds[left["id"]][len(thresholds[left["id"]]) // 2],
                               thresholds[right["id"]][len(thresholds[right["id"]]) // 2]],
            })
    return candidates


def _rule_summary(rows: list[dict], conditions: list[dict]):
    return _target_summary([row for row in rows if _passes(row, conditions)], 30, 365)


def walk_forward_explorer(episodes: list[dict], *, factors=FACTOR_DEFINITIONS,
                          policy: dict | None = None):
    policy = {**DEFAULT_POLICY, **(policy or {})}
    chunks = _fold_date_sets(episodes, int(policy["folds"]))
    fold_reports, aggregate_selected, aggregate_baseline = [], [], []
    for index in range(2, len(chunks)):
        training_dates = set().union(*chunks[:index])
        test_dates = chunks[index]
        training = [row for row in episodes
                    if (row.get("decision_date") or row.get("entry_date")) in training_dates]
        testing = [row for row in episodes
                   if (row.get("decision_date") or row.get("entry_date")) in test_dates]
        candidates = _candidate_catalog(training, tuple(factors), policy["quantiles"])
        ranked = []
        for candidate in candidates:
            summary = _rule_summary(training, candidate["conditions"])
            if (summary["completed_episodes"] < policy["minimum_training_completed"]
                    or summary["unique_issuers"] < policy["minimum_training_unique_issuers"]):
                continue
            ci = summary.get("success_rate_ci90_pct") or [-1, -1]
            ranked.append(((ci[0], summary.get("success_rate_pct") or -1,
                            summary["completed_episodes"]), candidate, summary))
        baseline_training = _target_summary(training, 30, 365)
        baseline_test = _target_summary(testing, 30, 365)
        if not ranked:
            fold_reports.append({
                "fold": index - 1, "status": "no_eligible_training_rule",
                "training": {"first": min(training_dates), "last": max(training_dates)},
                "test": {"first": min(test_dates), "last": max(test_dates)},
                "baseline_training": baseline_training, "baseline_test": baseline_test,
            })
            aggregate_baseline.extend(testing)
            continue
        ranked.sort(key=lambda item: item[0], reverse=True)
        _rank, selected, selected_training = ranked[0]
        selected_test_rows = [row for row in testing if _passes(row, selected["conditions"])]
        selected_test = _target_summary(selected_test_rows, 30, 365)
        improvement = (round(selected_test["success_rate_pct"] - baseline_test["success_rate_pct"], 1)
                       if None not in (selected_test.get("success_rate_pct"),
                                       baseline_test.get("success_rate_pct")) else None)
        fold_reports.append({
            "fold": index - 1,
            "status": "evaluated",
            "training": {"first": min(training_dates), "last": max(training_dates),
                         "completed_episodes": baseline_training["completed_episodes"]},
            "test": {"first": min(test_dates), "last": max(test_dates)},
            "selected_rule": selected,
            "training_result": selected_training,
            "baseline_test": baseline_test,
            "selected_test": selected_test,
            "test_improvement_pp": improvement,
        })
        aggregate_selected.extend(selected_test_rows)
        aggregate_baseline.extend(testing)
    baseline_oos = _target_summary(aggregate_baseline, 30, 365)
    selected_oos = _target_summary(aggregate_selected, 30, 365)
    improvement = (round(selected_oos["success_rate_pct"] - baseline_oos["success_rate_pct"], 1)
                   if None not in (selected_oos.get("success_rate_pct"),
                                   baseline_oos.get("success_rate_pct")) else None)
    drawdown_change = (round(selected_oos["median_max_drawdown_pct"]
                             - baseline_oos["median_max_drawdown_pct"], 1)
                       if None not in (selected_oos.get("median_max_drawdown_pct"),
                                       baseline_oos.get("median_max_drawdown_pct")) else None)
    evaluated = [fold for fold in fold_reports if fold["status"] == "evaluated"]
    nonworse = sum(
        isinstance(fold.get("test_improvement_pp"), (int, float))
        and fold["test_improvement_pp"] >= 0
        for fold in evaluated
    )
    checks = {
        "enough_oos_episodes": selected_oos["completed_episodes"] >= policy["minimum_total_oos_completed"],
        "minimum_fold_sample": bool(evaluated) and all(
            fold["selected_test"]["completed_episodes"] >= policy["minimum_fold_completed"]
            for fold in evaluated
        ),
        "oos_improvement": improvement is not None and improvement >= policy["minimum_oos_improvement_pp"],
        "oos_success_rate": selected_oos.get("success_rate_pct") is not None
        and selected_oos["success_rate_pct"] >= policy["minimum_oos_success_rate_pct"],
        "fold_stability": bool(evaluated) and nonworse / len(evaluated) >= 2 / 3,
        "drawdown": drawdown_change is not None
        and drawdown_change >= -policy["maximum_drawdown_deterioration_pp"],
    }
    return {
        "method": "rolling_origin_factor_threshold_explorer_v1",
        "production_effect": "none",
        "deployable_rule": False,
        "policy": policy,
        "folds": fold_reports,
        "out_of_sample_baseline": baseline_oos,
        "out_of_sample_selected": selected_oos,
        "out_of_sample_improvement_pp": improvement,
        "median_drawdown_change_pp": drawdown_change,
        "folds_nonworse": nonworse,
        "folds_evaluated": len(evaluated),
        "research_checks": checks,
        "research_signal": all(checks.values()),
        "interpretation": (
            "A research signal nominates factor families for a new frozen rule. It does not "
            "promote the fold-specific changing thresholds or alter production."
        ),
    }


def run_factor_explorer(observations: list[dict], *, step_days: int = 30,
                        policy: dict | None = None):
    max_gap = max(step_days + 1, int(math.ceil(step_days * 1.5)))
    episodes = _episode_roots(observations, {}, {"buy": 0, "watch": 0}, max_gap,
                              use_recorded_verdict=True)
    return {
        "ok": True,
        "version": 1,
        "objective": "Identify entry-date factors separating +30%-within-one-year successes and failures.",
        "information_boundary": "Point-in-time values available on or before the first Buy date only.",
        "production_effect": "none",
        "episodes": len(episodes),
        "factor_comparison": compare_factors(episodes),
        "walk_forward": walk_forward_explorer(episodes, policy=policy),
    }
