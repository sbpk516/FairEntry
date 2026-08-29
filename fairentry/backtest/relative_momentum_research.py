"""Frozen, point-in-time research for six-month sector-relative momentum.

This module evaluates one pre-registered definition.  It does not search for a
better lookback or threshold after seeing results, and it cannot alter an
official score, verdict, position, or alert.
"""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

from fairentry.analytics.demand_momentum import _SECTOR_ETF
from fairentry.analytics.relative_momentum import calculate_relative_momentum
from fairentry.backtest.research_cycle import _partition, _split_dates, _target_summary
from fairentry.backtest.sfa_tune import _episode_roots


ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "config" / "relative_momentum_experiments.json"
EXPERIMENT_ID = "six_month_sector_relative_momentum_v1"
VERSION = 1
MINIMUM_COMPLETED_PER_GROUP = 30
MINIMUM_UNIQUE_ISSUERS_PER_GROUP = 20
MINIMUM_IMPROVEMENT_PP = 5.0
MAXIMUM_DRAWDOWN_DETERIORATION_PP = 2.0
OUTCOME_CONTRACTS = (
    ("primary_30_within_one_year", 30, 365),
    ("secondary_25_within_one_year", 25, 365),
    ("secondary_30_within_two_years", 30, 730),
)
GROUPS = (
    ("improving", "Supportive · improving"),
    ("neutral", "Neutral"),
    ("deteriorating", "Contradictory · deteriorating"),
    ("unavailable", "Unavailable"),
)


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def load_experiment_registry(path: Path = REGISTRY_PATH) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    experiments = payload.get("experiments") or []
    ids = [str(row.get("id") or "") for row in experiments]
    if not experiments or len(ids) != len(set(ids)) or EXPERIMENT_ID not in ids:
        raise ValueError("relative-momentum experiment registry is missing or invalid")
    return payload


def calculate_history_relative_momentum(
    stock_history: list[dict],
    sector_history: list[dict],
) -> dict:
    """Align stock and sector closes by date before applying the shared rule."""
    stock = {
        str(row.get("date"))[:10]: _number(row.get("close"))
        for row in stock_history
        if _number(row.get("close")) is not None and _number(row.get("close")) > 0
    }
    sector = {
        str(row.get("date"))[:10]: _number(row.get("close"))
        for row in sector_history
        if _number(row.get("close")) is not None and _number(row.get("close")) > 0
    }
    dates = sorted(set(stock) & set(sector))
    result = calculate_relative_momentum(
        [stock[observed] for observed in dates],
        [sector[observed] for observed in dates],
    )
    return {
        **result,
        "aligned_sessions": len(dates),
        "history_start": dates[0] if dates else None,
        "as_of": dates[-1] if dates else None,
    }


def attach_relative_momentum_factors(episodes: list[dict], connection) -> dict:
    """Attach stock and sector histories strictly before each first Buy date."""
    missing = [row for row in episodes if not row.get("relative_momentum_factors")]
    if not missing or connection is None:
        return {
            "episodes": len(episodes),
            "requested": len(missing),
            "attached": 0,
            "already_attached": len(episodes) - len(missing),
            "available": sum(bool((row.get("relative_momentum_factors") or {}).get("available"))
                             for row in episodes),
            "future_data_used": False,
        }

    import pandas as pd

    requests, by_id = [], {}
    for index, row in enumerate(missing):
        ticker = row.get("ticker")
        sector_etf = _SECTOR_ETF.get(row.get("sector"))
        buy_date = str(row.get("decision_date") or row.get("entry_date") or "")[:10]
        request_id = f"relative-momentum-{index}"
        if not ticker or not sector_etf or not buy_date:
            factor = calculate_relative_momentum([], [])
            factor["sector_benchmark"] = sector_etf
            factor["reason"] = (
                "No sector ETF mapping is available."
                if not sector_etf else "Ticker or first Buy date is unavailable."
            )
            row["relative_momentum_factors"] = factor
            continue
        requests.append({
            "request_id": request_id,
            "ticker": ticker,
            "sector_etf": sector_etf,
            "buy_date": buy_date,
            "start_date": (date.fromisoformat(buy_date) - timedelta(days=500)).isoformat(),
        })
        by_id[request_id] = row

    histories = {
        request["request_id"]: {"stock": [], "sector": []}
        for request in requests
    }
    if requests:
        connection.register("relative_momentum_requests", pd.DataFrame(requests))
        try:
            price_rows = connection.execute("""
              SELECT r.request_id,'stock' kind,p.date,coalesce(p.closeadj,p.close) close_value
              FROM relative_momentum_requests r
              JOIN sfa_prices p ON p.ticker=r.ticker
               AND p.date>=CAST(r.start_date AS DATE)
               AND p.date<CAST(r.buy_date AS DATE)
              UNION ALL
              SELECT r.request_id,'sector' kind,p.date,p.closeadj close_value
              FROM relative_momentum_requests r
              JOIN sfa_fund_prices p ON p.ticker=r.sector_etf
               AND p.date>=CAST(r.start_date AS DATE)
               AND p.date<CAST(r.buy_date AS DATE)
              ORDER BY request_id,date
            """).fetchall()
        finally:
            connection.unregister("relative_momentum_requests")
        for request_id, kind, observed, close in price_rows:
            value = _number(close)
            if value is None or value <= 0:
                continue
            histories[str(request_id)][str(kind)].append({
                "date": observed.isoformat() if hasattr(observed, "isoformat") else str(observed)[:10],
                "close": value,
            })

    request_by_id = {request["request_id"]: request for request in requests}
    for request_id, row in by_id.items():
        history = histories.get(request_id) or {"stock": [], "sector": []}
        factor = calculate_history_relative_momentum(history["stock"], history["sector"])
        factor["sector_benchmark"] = request_by_id[request_id]["sector_etf"]
        factor["measurement_rule"] = "latest common trading session strictly before first Buy"
        row["relative_momentum_factors"] = factor
    return {
        "episodes": len(episodes),
        "requested": len(requests),
        "attached": len(by_id),
        "already_attached": len(episodes) - len(missing),
        "available": sum(bool((row.get("relative_momentum_factors") or {}).get("available"))
                         for row in episodes),
        "future_data_used": False,
    }


def _outcomes(rows: list[dict]) -> dict:
    return {
        key: _target_summary(rows, target, horizon)
        for key, target, horizon in OUTCOME_CONTRACTS
    }


def _difference(left: dict, right: dict, field="success_rate_pct"):
    a, b = _number(left.get(field)), _number(right.get(field))
    return round(a - b, 1) if None not in (a, b) else None


def _group_results(rows: list[dict]) -> dict:
    groups = {}
    for group_id, label in GROUPS:
        selected = [
            row for row in rows
            if (row.get("relative_momentum_factors") or {}).get(
                "classification", "unavailable"
            ) == group_id
        ]
        groups[group_id] = {
            "id": group_id,
            "label": label,
            "outcomes": _outcomes(selected),
        }
    baseline = _outcomes(rows)
    key = "primary_30_within_one_year"
    supportive = groups["improving"]["outcomes"][key]
    contradictory = groups["deteriorating"]["outcomes"][key]
    all_buy = baseline[key]
    return {
        "baseline": baseline,
        "groups": groups,
        "supportive_minus_contradictory_pp": _difference(supportive, contradictory),
        "supportive_minus_all_buy_pp": _difference(supportive, all_buy),
        "supportive_drawdown_change_vs_all_buy_pp": _difference(
            supportive, all_buy, "median_max_drawdown_pct"
        ),
    }


def _detail(row: dict) -> dict:
    factor = row.get("relative_momentum_factors") or {}
    hits = (row.get("return_milestones") or {}).get("first_hit_days") or {}
    return {
        "ticker": row.get("ticker"),
        "company": row.get("company"),
        "issuer_key": row.get("issuer_key"),
        "sector": row.get("sector"),
        "sector_benchmark": factor.get("sector_benchmark"),
        "strategy": row.get("strategy_key"),
        "earliest_buy_date": row.get("entry_date"),
        "measurement_as_of": factor.get("as_of"),
        "classification": factor.get("classification", "unavailable"),
        "display_direction": factor.get("display_direction", "Unavailable"),
        "stock_return_6m_pct": factor.get("stock_return_6m_pct"),
        "sector_return_6m_pct": factor.get("sector_return_6m_pct"),
        "relative_momentum_6m_pct": factor.get("relative_momentum_6m_pct"),
        "prior_relative_momentum_6m_pct": factor.get("prior_relative_momentum_6m_pct"),
        "relative_momentum_change_1m_pp": factor.get("relative_momentum_change_1m_pp"),
        "aligned_sessions": factor.get("aligned_sessions"),
        "evidence_available": factor.get("available", False),
        "unavailable_reason": factor.get("reason"),
        "score_effect": 0,
        "verdict_effect": "none",
        "days_to_25_pct": hits.get("25"),
        "days_to_30_pct": hits.get("30"),
    }


def run_relative_momentum_research(
    observations: list[dict],
    connection=None,
    *,
    step_days: int = 30,
) -> dict:
    """Evaluate the single frozen definition across chronological partitions."""
    registry = load_experiment_registry()
    experiment = next(
        row for row in registry["experiments"] if row["id"] == EXPERIMENT_ID
    )
    max_gap = max(step_days + 1, int(math.ceil(step_days * 1.5)))
    episodes = _episode_roots(
        observations, {}, {"buy": 0, "watch": 0}, max_gap,
        use_recorded_verdict=True,
    )
    enrichment = attach_relative_momentum_factors(episodes, connection)
    split = _split_dates(episodes, {"development_share": .60, "validation_share": .20})
    partitions = {
        "all_history": episodes,
        "development": _partition(episodes, split["development"]) if split else [],
        "validation": _partition(episodes, split["validation"]) if split else [],
        "final_historical_test": _partition(episodes, split["test"]) if split else [],
    }
    results = {name: _group_results(rows) for name, rows in partitions.items()}
    key = "primary_30_within_one_year"
    validation = results["validation"]
    final = results["final_historical_test"]
    final_supportive = final["groups"]["improving"]["outcomes"][key]
    final_contradictory = final["groups"]["deteriorating"]["outcomes"][key]
    validation_supportive = validation["groups"]["improving"]["outcomes"][key]
    validation_contradictory = validation["groups"]["deteriorating"]["outcomes"][key]
    checks = {
        "single_registered_definition": len(registry["experiments"]) == 1,
        "validation_supportive_sample": (
            validation_supportive["completed_episodes"] >= MINIMUM_COMPLETED_PER_GROUP
            and validation_supportive["unique_issuers"] >= MINIMUM_UNIQUE_ISSUERS_PER_GROUP
        ),
        "validation_contradictory_sample": (
            validation_contradictory["completed_episodes"] >= MINIMUM_COMPLETED_PER_GROUP
            and validation_contradictory["unique_issuers"] >= MINIMUM_UNIQUE_ISSUERS_PER_GROUP
        ),
        "final_supportive_sample": (
            final_supportive["completed_episodes"] >= MINIMUM_COMPLETED_PER_GROUP
            and final_supportive["unique_issuers"] >= MINIMUM_UNIQUE_ISSUERS_PER_GROUP
        ),
        "final_contradictory_sample": (
            final_contradictory["completed_episodes"] >= MINIMUM_COMPLETED_PER_GROUP
            and final_contradictory["unique_issuers"] >= MINIMUM_UNIQUE_ISSUERS_PER_GROUP
        ),
        "validation_direction": (
            validation["supportive_minus_contradictory_pp"] is not None
            and validation["supportive_minus_contradictory_pp"] >= 0
        ),
        "final_improvement_vs_contradictory": (
            final["supportive_minus_contradictory_pp"] is not None
            and final["supportive_minus_contradictory_pp"] >= MINIMUM_IMPROVEMENT_PP
        ),
        "final_improvement_vs_all_buy": (
            final["supportive_minus_all_buy_pp"] is not None
            and final["supportive_minus_all_buy_pp"] >= MINIMUM_IMPROVEMENT_PP
        ),
        "final_drawdown_not_materially_worse": (
            final["supportive_drawdown_change_vs_all_buy_pp"] is not None
            and final["supportive_drawdown_change_vs_all_buy_pp"]
            >= -MAXIMUM_DRAWDOWN_DETERIORATION_PP
        ),
    }
    historical_signal = all(checks.values())
    if not all(checks[name] for name in (
        "validation_supportive_sample", "validation_contradictory_sample",
        "final_supportive_sample", "final_contradictory_sample",
    )):
        conclusion = "One or more chronological groups have too few completed cases to establish a reliable momentum advantage."
    elif historical_signal:
        conclusion = "The frozen definition passed its historical checks, but it still requires genuinely new shadow outcomes before any production use."
    else:
        conclusion = "The frozen six-month relative-momentum definition did not pass every historical stability and risk check."
    final_dates = split["test"] if split else set()
    available = [
        row for row in episodes
        if (row.get("relative_momentum_factors") or {}).get("available")
    ]
    return {
        "ok": True,
        "version": VERSION,
        "experiment_id": EXPERIMENT_ID,
        "production_effect": "none",
        "one_official_score": True,
        "not_validated": True,
        "promotion_eligible": False,
        "historical_signal": historical_signal,
        "primary_hypothesis": (
            "Earliest official Buy episodes with improving six-month stock-minus-sector "
            "momentum reach +30% within one year more often than episodes with "
            "deteriorating relative momentum."
        ),
        "factor_definition": experiment,
        "measurement_boundary": "Latest common stock/sector trading session strictly before the earliest Buy; no same-day or future close is used.",
        "missing_history_policy": "Missing sector mapping or fewer than 148 aligned sessions is unavailable, never neutral or supportive.",
        "experiment_registry": {
            "version": registry.get("version"),
            "path": "config/relative_momentum_experiments.json",
            "definitions_tested": len(registry["experiments"]),
            "definition_ids": [row["id"] for row in registry["experiments"]],
            "selection_rule": "No lookback, threshold, or classification boundary was selected after viewing outcomes.",
        },
        "anti_overfitting_controls": {
            "chronological_development_pct": 60,
            "chronological_validation_pct": 20,
            "chronological_final_historical_test_pct": 20,
            "final_historical_test_dates": [min(final_dates), max(final_dates)] if final_dates else None,
            "historical_test_status": "One-time evaluation of this frozen definition",
            "global_holdout_warning": "FairEntry has used the historical timeline for other research; genuinely new future shadow outcomes remain required.",
            "minimum_completed_per_compared_group": MINIMUM_COMPLETED_PER_GROUP,
            "minimum_unique_issuers_per_compared_group": MINIMUM_UNIQUE_ISSUERS_PER_GROUP,
            "minimum_improvement_pp": MINIMUM_IMPROVEMENT_PP,
            "maximum_drawdown_deterioration_pp": MAXIMUM_DRAWDOWN_DETERIORATION_PP,
        },
        "coverage": {
            "earliest_buy_episodes": len(episodes),
            "with_relative_momentum": len(available),
            "without_relative_momentum": len(episodes) - len(available),
        },
        "enrichment": enrichment,
        "results": results,
        "research_checks": checks,
        "research_conclusion": conclusion,
        "episode_details": [_detail(row) for row in episodes],
        "interpretation": (
            "Momentum is independent market-confirmation evidence, not a forecast or gate. "
            "A positive historical result cannot affect scoring until new shadow outcomes "
            "confirm the frozen rule."
        ),
    }
