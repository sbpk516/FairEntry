"""Point-in-time movement-capacity research for fixed return targets.

The trailing range and volatility measurements in this module are research
evidence only.  They are calculated strictly from prices available on or
before the first Buy date, then compared with later target outcomes.  Nothing
here changes an official score, verdict, or production screen.
"""
from __future__ import annotations

import math
import statistics
from datetime import date, timedelta

from fairentry.analytics.breakout_setup import trend_regime_metric
from fairentry.backtest.research_cycle import _partition, _split_dates, _target_summary
from fairentry.backtest.sfa_tune import _episode_roots


VERSION = 1
TRADING_SESSIONS = 252
MINIMUM_SESSIONS = 200
TARGET_PCT = 30.0
RANGE_BANDS = (
    {"id": "under_20", "label": "Below 20%", "minimum": None, "maximum": 20.0},
    {"id": "20_to_under_30", "label": "20% to under 30%", "minimum": 20.0, "maximum": 30.0},
    {"id": "30_to_under_45", "label": "30% to under 45%", "minimum": 30.0, "maximum": 45.0},
    {"id": "45_to_under_60", "label": "45% to under 60%", "minimum": 45.0, "maximum": 60.0},
    {"id": "60_plus", "label": "60% or more", "minimum": 60.0, "maximum": None},
)
OUTCOME_CONTRACTS = (
    ("primary_30_within_one_year", 30, 365),
    ("extended_30_within_two_years", 30, 730),
    ("compounder_50_within_two_years", 50, 730),
)
COMBINATIONS = (
    ("constructive_controlled", "Constructive trend + controlled downside"),
    ("constructive_uncontrolled", "Constructive trend + elevated downside"),
    ("nonconstructive_controlled", "Non-constructive trend + controlled downside"),
    ("nonconstructive_uncontrolled", "Non-constructive trend + elevated downside"),
)


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _mean(values):
    clean = [_number(value) for value in values]
    clean = [value for value in clean if value is not None]
    return statistics.fmean(clean) if clean else None


def _returns(closes):
    output = []
    for previous, current in zip(closes, closes[1:]):
        if previous is not None and current is not None and previous > 0:
            output.append(current / previous - 1)
    return output


def _realized_volatility(returns, sessions=126):
    if len(returns) < sessions:
        return None
    return statistics.pstdev(returns[-sessions:]) * math.sqrt(252) * 100


def _downside_upside_ratio(returns, sessions=63):
    if len(returns) < sessions:
        return None
    recent = returns[-sessions:]
    upside = [value for value in recent if value > 0]
    downside = [-value for value in recent if value < 0]
    if len(upside) < 5 or len(downside) < 5:
        return None
    upside_volatility = statistics.pstdev(upside)
    downside_volatility = statistics.pstdev(downside)
    if upside_volatility <= 0:
        return None
    return downside_volatility / upside_volatility


def _range_band(value):
    if value is None:
        return None
    for band in RANGE_BANDS:
        if ((band["minimum"] is None or value >= band["minimum"])
                and (band["maximum"] is None or value < band["maximum"])):
            return band["id"]
    return None


def calculate_movement_capacity(history: list[dict]) -> dict:
    """Calculate transparent target-capacity evidence from one as-of history."""
    rows = []
    for point in sorted(history, key=lambda item: str(item.get("date") or "")):
        close = _number(point.get("close"))
        high = _number(point.get("high"))
        low = _number(point.get("low"))
        if close is None or close <= 0:
            continue
        rows.append({
            "date": str(point.get("date"))[:10],
            "close": close,
            "high": high if high is not None and high > 0 else close,
            "low": low if low is not None and low > 0 else close,
        })
    rows = rows[-TRADING_SESSIONS:]
    base = {
        "available": False,
        "required_sessions": MINIMUM_SESSIONS,
        "available_sessions": len(rows),
        "lookback_sessions": TRADING_SESSIONS,
        "score_effect": 0,
        "verdict_effect": "none",
        "future_data_used": False,
    }
    if len(rows) < MINIMUM_SESSIONS:
        return {**base, "reason": "Fewer than 200 prior trading sessions are available."}

    closes = [point["close"] for point in rows]
    trailing_high = max(point["high"] for point in rows)
    trailing_low = min(point["low"] for point in rows)
    if trailing_low <= 0 or trailing_high < trailing_low:
        return {**base, "reason": "The adjusted trailing high/low range is invalid."}

    range_pct = (trailing_high / trailing_low - 1) * 100
    current = closes[-1]
    position = (
        (current - trailing_low) / (trailing_high - trailing_low) * 100
        if trailing_high > trailing_low else None
    )
    returns = _returns(closes)
    realized = _realized_volatility(returns)
    downside_ratio = _downside_upside_ratio(returns)
    ma50 = _mean(closes[-50:]) if len(closes) >= 50 else None
    ma200 = _mean(closes[-200:]) if len(closes) >= 200 else None
    prior50 = _mean(closes[-70:-20]) if len(closes) >= 70 else None
    trend_score, trend_checks = trend_regime_metric(current, ma50, ma200, prior50)
    constructive = trend_score is not None and trend_score >= 75
    downside_controlled = downside_ratio is not None and downside_ratio <= 1.10
    combination = None
    if trend_score is not None and downside_ratio is not None:
        combination = (
            "constructive_" if constructive else "nonconstructive_"
        ) + ("controlled" if downside_controlled else "uncontrolled")
    target_sigma_distance = (
        math.log1p(TARGET_PCT / 100) / (realized / 100)
        if realized is not None and realized > 0 else None
    )
    high_point = max(rows, key=lambda point: point["high"])
    low_point = min(rows, key=lambda point: point["low"])
    return {
        **base,
        "available": True,
        "reason": None,
        "as_of": rows[-1]["date"],
        "history_start": rows[0]["date"],
        "trailing_high": round(trailing_high, 4),
        "trailing_high_date": high_point["date"],
        "trailing_low": round(trailing_low, 4),
        "trailing_low_date": low_point["date"],
        "trailing_range_pct": round(range_pct, 2),
        "range_band": _range_band(range_pct),
        "movement_capacity_ratio": round(range_pct / TARGET_PCT, 2),
        "target_to_range_ratio": round(TARGET_PCT / range_pct, 2) if range_pct > 0 else None,
        "current_range_position_pct": round(position, 1) if position is not None else None,
        "realized_volatility_126d_pct": round(realized, 2) if realized is not None else None,
        "target_sigma_distance": round(target_sigma_distance, 2)
        if target_sigma_distance is not None else None,
        "downside_upside_volatility_63d": round(downside_ratio, 2)
        if downside_ratio is not None else None,
        "downside_controlled": downside_controlled if downside_ratio is not None else None,
        "trend_regime_score": trend_score,
        "trend_checks_passed": sum(trend_checks),
        "trend_checks_available": len(trend_checks),
        "constructive_trend": constructive,
        "trend_downside_combination": combination,
    }


def attach_movement_capacity_factors(episodes: list[dict], connection) -> dict:
    """Attach adjusted OHLC evidence truncated at each episode's first Buy date."""
    missing = [row for row in episodes if not row.get("movement_capacity_factors")]
    if not missing or connection is None:
        return {
            "episodes": len(episodes),
            "requested": len(missing),
            "attached": 0,
            "already_attached": len(episodes) - len(missing),
            "available": sum(bool((row.get("movement_capacity_factors") or {}).get("available"))
                             for row in episodes),
            "future_data_used": False,
        }

    import pandas as pd

    requests = []
    by_id = {}
    for index, row in enumerate(missing):
        ticker = row.get("ticker")
        cutoff = str(row.get("decision_date") or row.get("entry_date") or "")[:10]
        if not ticker or not cutoff:
            row["movement_capacity_factors"] = calculate_movement_capacity([])
            continue
        request_id = f"movement-{index}"
        requests.append({
            "request_id": request_id,
            "ticker": ticker,
            "start_date": (date.fromisoformat(cutoff) - timedelta(days=400)).isoformat(),
            "cutoff_date": cutoff,
        })
        by_id[request_id] = row

    histories = {request["request_id"]: [] for request in requests}
    if requests:
        connection.register("movement_capacity_requests", pd.DataFrame(requests))
        try:
            price_rows = connection.execute("""
              SELECT r.request_id,p.date,p.close,p.closeadj,p.high,p.low
              FROM movement_capacity_requests r
              JOIN sfa_prices p ON p.ticker=r.ticker
                AND p.date BETWEEN CAST(r.start_date AS DATE) AND CAST(r.cutoff_date AS DATE)
              ORDER BY r.request_id,p.date
            """).fetchall()
        finally:
            connection.unregister("movement_capacity_requests")
        for request_id, observed, close, closeadj, high, low in price_rows:
            adjusted = _number(closeadj) or _number(close)
            raw_close = _number(close)
            if adjusted is None or adjusted <= 0:
                continue
            adjustment = adjusted / raw_close if raw_close and raw_close > 0 else 1.0
            histories[str(request_id)].append({
                "date": observed.isoformat() if hasattr(observed, "isoformat") else str(observed)[:10],
                "close": adjusted,
                "high": (_number(high) * adjustment) if _number(high) is not None else adjusted,
                "low": (_number(low) * adjustment) if _number(low) is not None else adjusted,
            })
    for request_id, row in by_id.items():
        row["movement_capacity_factors"] = calculate_movement_capacity(
            histories.get(request_id, [])
        )
    return {
        "episodes": len(episodes),
        "requested": len(requests),
        "attached": len(by_id),
        "already_attached": len(episodes) - len(missing),
        "available": sum(bool((row.get("movement_capacity_factors") or {}).get("available"))
                         for row in episodes),
        "future_data_used": False,
    }


def _outcomes(rows: list[dict]) -> dict:
    return {
        key: _target_summary(rows, target, horizon)
        for key, target, horizon in OUTCOME_CONTRACTS
    }


def _difference(left, right):
    left_rate = _number(left.get("success_rate_pct"))
    right_rate = _number(right.get("success_rate_pct"))
    return round(left_rate - right_rate, 1) if None not in (left_rate, right_rate) else None


def _detail(row: dict) -> dict:
    factors = row.get("movement_capacity_factors") or {}
    hits = (row.get("return_milestones") or {}).get("first_hit_days") or {}
    return {
        "ticker": row.get("ticker"),
        "company": row.get("company"),
        "issuer_key": row.get("issuer_key"),
        "strategy": row.get("strategy_key"),
        "earliest_buy_date": row.get("entry_date"),
        "measurement_as_of": factors.get("as_of"),
        "entry_price": row.get("entry_price"),
        "available_sessions": factors.get("available_sessions"),
        "trailing_high": factors.get("trailing_high"),
        "trailing_low": factors.get("trailing_low"),
        "trailing_range_pct": factors.get("trailing_range_pct"),
        "range_band": factors.get("range_band"),
        "movement_capacity_ratio": factors.get("movement_capacity_ratio"),
        "current_range_position_pct": factors.get("current_range_position_pct"),
        "realized_volatility_126d_pct": factors.get("realized_volatility_126d_pct"),
        "target_sigma_distance": factors.get("target_sigma_distance"),
        "trend_regime_score": factors.get("trend_regime_score"),
        "downside_upside_volatility_63d": factors.get(
            "downside_upside_volatility_63d"
        ),
        "trend_downside_combination": factors.get("trend_downside_combination"),
        "evidence_available": factors.get("available", False),
        "unavailable_reason": factors.get("reason"),
        "days_to_30_pct": hits.get("30"),
        "days_to_50_pct": hits.get("50"),
    }


def run_movement_capacity_research(
    observations: list[dict],
    connection=None,
    *,
    step_days: int = 30,
) -> dict:
    """Compare predeclared movement-capacity bands with fixed target outcomes."""
    max_gap = max(step_days + 1, int(math.ceil(step_days * 1.5)))
    episodes = _episode_roots(
        observations, {}, {"buy": 0, "watch": 0}, max_gap,
        use_recorded_verdict=True,
    )
    enrichment = attach_movement_capacity_factors(episodes, connection)
    split = _split_dates(episodes, {"development_share": .60, "validation_share": .20})
    test_dates = split["test"] if split else set()
    final_episodes = _partition(episodes, test_dates) if split else []
    available = [row for row in episodes
                 if (row.get("movement_capacity_factors") or {}).get("available")]
    final_available = [row for row in final_episodes
                       if (row.get("movement_capacity_factors") or {}).get("available")]
    baseline = {"all_history": _outcomes(episodes), "final_unseen": _outcomes(final_episodes)}
    available_baseline = {
        "all_history": _outcomes(available),
        "final_unseen": _outcomes(final_available),
    }

    bands = []
    for definition in RANGE_BANDS:
        rows = [row for row in available
                if (row.get("movement_capacity_factors") or {}).get("range_band") == definition["id"]]
        final_rows = [row for row in final_available
                      if (row.get("movement_capacity_factors") or {}).get("range_band") == definition["id"]]
        bands.append({
            **definition,
            "all_history": _outcomes(rows),
            "final_unseen": _outcomes(final_rows),
        })

    comparisons = []
    for cutoff in (20.0, 30.0):
        below = [row for row in available
                 if row["movement_capacity_factors"]["trailing_range_pct"] < cutoff]
        at_least = [row for row in available
                    if row["movement_capacity_factors"]["trailing_range_pct"] >= cutoff]
        final_below = [row for row in final_available
                       if row["movement_capacity_factors"]["trailing_range_pct"] < cutoff]
        final_at_least = [row for row in final_available
                          if row["movement_capacity_factors"]["trailing_range_pct"] >= cutoff]
        below_all, above_all = _outcomes(below), _outcomes(at_least)
        below_final, above_final = _outcomes(final_below), _outcomes(final_at_least)
        key = "primary_30_within_one_year"
        comparisons.append({
            "cutoff_pct": cutoff,
            "below": {"all_history": below_all, "final_unseen": below_final},
            "at_least": {"all_history": above_all, "final_unseen": above_final},
            "at_least_minus_below_pp": {
                "all_history": _difference(above_all[key], below_all[key]),
                "final_unseen": _difference(above_final[key], below_final[key]),
            },
        })

    combinations = []
    for combination_id, label in COMBINATIONS:
        rows = [row for row in available
                if (row.get("movement_capacity_factors") or {}).get(
                    "trend_downside_combination") == combination_id]
        final_rows = [row for row in final_available
                      if (row.get("movement_capacity_factors") or {}).get(
                          "trend_downside_combination") == combination_id]
        combinations.append({
            "id": combination_id,
            "label": label,
            "all_history": _outcomes(rows),
            "final_unseen": _outcomes(final_rows),
        })

    below_30 = comparisons[-1]
    low_final = below_30["below"]["final_unseen"]["primary_30_within_one_year"]
    higher_final = below_30["at_least"]["final_unseen"]["primary_30_within_one_year"]
    final_difference = below_30["at_least_minus_below_pp"]["final_unseen"]
    enough_low_cases = low_final.get("completed_episodes", 0) >= 30
    enough_higher_cases = higher_final.get("completed_episodes", 0) >= 30
    directional_support = bool(
        enough_low_cases and enough_higher_cases
        and final_difference is not None and final_difference >= 5
    )
    conclusion = (
        "The newest historical period supports the low-capacity concern, but the rule remains research-only and needs a separately frozen confirmation."
        if directional_support else
        "The newest historical period does not yet justify removing every stock whose trailing range is below 30%."
        if enough_low_cases and enough_higher_cases else
        "The newest historical period has too few completed cases on one side of the 30% range comparison to justify an exclusion."
    )
    return {
        "ok": True,
        "version": VERSION,
        "production_effect": "none",
        "one_official_score": True,
        "not_validated": True,
        "primary_contract": "+30% within 365 days from the earliest official Buy",
        "extended_contracts": ["+30% within 730 days", "+50% within 730 days"],
        "factor_definition": (
            "Trailing 52-week movement capacity = (highest adjusted daily high / "
            "lowest adjusted daily low - 1) × 100, using at most 252 sessions available "
            "on or before the first Buy date."
        ),
        "capacity_ratio_definition": "Trailing 52-week range percentage / 30% target.",
        "volatility_definition": (
            "126-session annualized close-to-close volatility; it is not directly "
            "compared with a 30% cumulative return."
        ),
        "trend_rule": "Constructive means at least 75% of the available price/50DMA/200DMA/rising-50DMA checks pass.",
        "downside_rule": "Controlled means the 63-session downside/upside volatility ratio is 1.10 or less.",
        "missing_history_policy": "Fewer than 200 prior sessions is unavailable, never a pass.",
        "chronological_split": {
            "development_pct": 60,
            "validation_pct": 20,
            "final_unseen_pct": 20,
            "final_unseen_dates": [min(test_dates), max(test_dates)] if test_dates else None,
        },
        "coverage": {
            "earliest_buy_episodes": len(episodes),
            "with_movement_capacity": len(available),
            "without_sufficient_history": len(episodes) - len(available),
            "final_unseen_episodes": len(final_episodes),
            "final_unseen_with_movement_capacity": len(final_available),
        },
        "enrichment": enrichment,
        "baseline": baseline,
        "available_history_baseline": available_baseline,
        "range_bands": bands,
        "cutoff_comparisons": comparisons,
        "trend_downside_combinations": combinations,
        "low_range_exclusion_signal": directional_support,
        "research_conclusion": conclusion,
        "episode_details": [_detail(row) for row in episodes],
        "interpretation": (
            "A narrow prior range can be negative evidence for a one-year +30% target, "
            "but sufficient range is not proof of an attractive investment. High movement "
            "must be read with trend and downside asymmetry. No result changes production "
            "until a separately frozen out-of-time test confirms it."
        ),
    }
