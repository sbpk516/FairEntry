#!/usr/bin/env python3
"""Backtest fundamentals + fair price + moving-average entry alignment."""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import ijson
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fairentry.backtest.research_cycle import _target_summary  # noqa: E402
from fairentry.backtest.three_way_entry import (  # noqa: E402
    fundamentals_strong,
    near_average,
    valuation_aligned,
)
from fairentry.sharadar import SharadarWarehouse  # noqa: E402
from scripts.analyze_buy_failures import episode_roots  # noqa: E402


def attach_moving_averages(rows: list[dict], connection) -> dict:
    frame = pd.DataFrame({
        "observation_id": [row["observation_id"] for row in rows],
        "ticker": [row["ticker"] for row in rows],
        "decision_date": [row["decision_date"] for row in rows],
    })
    connection.register("entry_observations", frame)
    try:
        prices = connection.execute("""
          SELECT observation_id,date,closeadj,volume
          FROM (
            SELECT o.observation_id,p.date,p.closeadj,p.volume,
                   row_number() OVER (
                     PARTITION BY o.observation_id ORDER BY p.date DESC
                   ) AS recency
            FROM entry_observations o
            JOIN sfa_prices p ON p.ticker=o.ticker
             AND p.date<=CAST(o.decision_date AS DATE)
          ) WHERE recency<=1000
          ORDER BY observation_id,date
        """).fetchdf()
    finally:
        connection.unregister("entry_observations")

    by_id = {}
    for observation_id, group in prices.groupby("observation_id", sort=False):
        closes = group["closeadj"].dropna().astype(float)
        current = closes.iloc[-1] if len(closes) else None
        ema200 = (closes.ewm(span=200, adjust=False, min_periods=200).mean().iloc[-1]
                  if len(closes) >= 200 else None)
        sma1000 = closes.iloc[-1000:].mean() if len(closes) >= 900 else None
        sma200_series = closes.rolling(200, min_periods=200).mean()
        sma200_current = sma200_series.iloc[-1] if len(closes) >= 200 else None
        sma200_prior20 = sma200_series.iloc[-21] if len(closes) >= 220 else None
        sma50_current = closes.iloc[-50:].mean() if len(closes) >= 50 else None
        momentum_12_1 = (
            (closes.iloc[-22] / closes.iloc[-253] - 1) * 100
            if len(closes) >= 253 and closes.iloc[-253] > 0 else None
        )
        weekly = (
            group.assign(date=pd.to_datetime(group["date"]))
            .set_index("date")
            .resample("W-FRI")
            .agg({"closeadj": "last", "volume": "sum"})
            .dropna(subset=["closeadj", "volume"])
        )
        direction = weekly["closeadj"].diff().apply(
            lambda change: 1.0 if change > 0 else (-1.0 if change < 0 else 0.0)
        )
        obv = (direction * weekly["volume"]).fillna(0.0).cumsum()
        obv_ema20 = obv.ewm(span=20, adjust=False, min_periods=20).mean()
        obv_confirmed = (
            bool(obv.iloc[-1] > obv_ema20.iloc[-1])
            if len(obv) >= 20 and pd.notna(obv_ema20.iloc[-1]) else None
        )
        monthly = (
            group.assign(date=pd.to_datetime(group["date"]))
            .set_index("date")["closeadj"]
            .resample("ME")
            .last()
            .dropna()
            .astype(float)
        )
        ema9month = (
            monthly.ewm(span=9, adjust=False, min_periods=9).mean().iloc[-1]
            if len(monthly) >= 9 else None
        )
        ema20month = (
            monthly.ewm(span=20, adjust=False, min_periods=20).mean().iloc[-1]
            if len(monthly) >= 20 else None
        )
        by_id[str(observation_id)] = (
            current, ema200, sma1000, len(closes), obv_confirmed, len(weekly),
            ema9month, ema20month, len(monthly), sma200_current,
            sma200_prior20, sma50_current, momentum_12_1
        )

    attached = 0
    for row in rows:
        (current, ema200, sma1000, sessions, obv_confirmed, weekly_periods,
         ema9month, ema20month, monthly_periods, sma200_current,
         sma200_prior20, sma50_current, momentum_12_1) = by_id.get(
            str(row["observation_id"]),
            (None, None, None, 0, None, 0, None, None, 0,
             None, None, None, None),
        )
        factors = row.setdefault("research_factors", {})
        factors["ema_40week_distance_pct"] = (
            (current / ema200 - 1) * 100 if current and ema200 else None
        )
        factors["sma_200week_distance_pct"] = (
            (current / sma1000 - 1) * 100 if current and sma1000 else None
        )
        factors["ema_9month_distance_pct"] = (
            (current / ema9month - 1) * 100 if current and ema9month else None
        )
        factors["ema_20month_distance_pct"] = (
            (current / ema20month - 1) * 100 if current and ema20month else None
        )
        factors["moving_average_history_sessions"] = sessions
        factors["obv_above_20week_ema"] = obv_confirmed
        factors["obv_history_weeks"] = weekly_periods
        factors["moving_average_history_months"] = monthly_periods
        factors["price_above_sma200"] = (
            bool(current > sma200_current)
            if current is not None and sma200_current is not None else None
        )
        factors["sma200_rising_20_sessions"] = (
            bool(sma200_current > sma200_prior20)
            if sma200_current is not None and sma200_prior20 is not None else None
        )
        factors["price_to_sma50_pct"] = (
            (current / sma50_current - 1) * 100
            if current is not None and sma50_current else None
        )
        factors["momentum_12_1_pct"] = momentum_12_1
        attached += current is not None
    return {"observations": len(rows), "attached": attached}


def attach_outcomes(rows: list[dict], connection) -> dict:
    """Attach comparable one- and two-year outcomes to research candidates."""
    frame = pd.DataFrame({
        "observation_id": [row["observation_id"] for row in rows],
        "ticker": [row["ticker"] for row in rows],
        "entry_date": [row["entry_date"] for row in rows],
        "entry_price": [row.get("_entry_closeadj") or row.get("entry_price")
                        for row in rows],
    })
    connection.register("candidate_observations", frame)
    try:
        outcomes = connection.execute("""
          SELECT o.observation_id,
                 min(p.date) FILTER (
                   WHERE p.closeadj>=o.entry_price*1.25
                 ) AS hit_date_25,
                 min(p.date) FILTER (
                   WHERE p.closeadj>=o.entry_price*1.30
                 ) AS hit_date_30,
                 min(p.date) FILTER (
                   WHERE p.closeadj>=o.entry_price*1.40
                 ) AS hit_date_40,
                 min(p.date) FILTER (
                   WHERE p.closeadj>=o.entry_price*1.50
                 ) AS hit_date_50,
                 max(p.date) AS last_date,
                 min(p.closeadj) FILTER (
                   WHERE p.date<=CAST(o.entry_date AS DATE)+INTERVAL '365 days'
                 ) AS lowest_close_365,
                 max(p.closeadj) FILTER (
                   WHERE p.date<=CAST(o.entry_date AS DATE)+INTERVAL '365 days'
                 ) AS highest_close_365,
                 min(p.closeadj) AS lowest_close_730,
                 max(p.closeadj) AS highest_close_730,
                 any_value(o.entry_price) AS entry_price
          FROM candidate_observations o
          LEFT JOIN sfa_prices p ON p.ticker=o.ticker
           AND p.date>=CAST(o.entry_date AS DATE)
           AND p.date<=CAST(o.entry_date AS DATE)+INTERVAL '730 days'
          WHERE o.entry_price IS NOT NULL AND o.entry_price>0
          GROUP BY o.observation_id
        """).fetchall()
    finally:
        connection.unregister("candidate_observations")

    by_id = {str(row[0]): row[1:] for row in outcomes}
    attached = 0
    for row in rows:
        values = by_id.get(str(row["observation_id"]))
        if not values:
            continue
        (hit_date_25, hit_date_30, hit_date_40, hit_date_50,
         last_date, low_365, high_365, low_730, high_730, entry_price) = values
        entry = date.fromisoformat(str(row["entry_date"])[:10])
        hit_days = {
            "25": (hit_date_25 - entry).days if hit_date_25 is not None else None,
            "30": (hit_date_30 - entry).days if hit_date_30 is not None else None,
            "40": (hit_date_40 - entry).days if hit_date_40 is not None else None,
            "50": (hit_date_50 - entry).days if hit_date_50 is not None else None,
        }
        last_days = (last_date - entry).days if last_date is not None else None
        previous = row.get("return_milestones") or {}
        terminal_days = previous.get("terminal_days")
        row["return_milestones"] = {
            **previous,
            "first_hit_days": {**(previous.get("first_hit_days") or {}),
                               **hit_days},
            "last_observed_days": last_days,
            "last_observed_date": last_date.isoformat() if last_date else None,
            "terminal_days": terminal_days,
            "max_drawdown_pct_by_horizon": {
                **(previous.get("max_drawdown_pct_by_horizon") or {}),
                "365": round((float(low_365) / float(entry_price) - 1) * 100, 2)
                if low_365 is not None else None,
                "730": round((float(low_730) / float(entry_price) - 1) * 100, 2)
                if low_730 is not None else None,
            },
            "max_return_pct_by_horizon": {
                **(previous.get("max_return_pct_by_horizon") or {}),
                "365": round((float(high_365) / float(entry_price) - 1) * 100, 2)
                if high_365 is not None else None,
                "730": round((float(high_730) / float(entry_price) - 1) * 100, 2)
                if high_730 is not None else None,
            },
        }
        attached += last_date is not None
    return {"observations": len(rows), "attached": attached}


def _summary(rows: list[dict]) -> dict:
    return {
        f"{target}_within_{years}_{'year' if years == 1 else 'years'}":
            _target_summary(rows, target, horizon)
        for target in (25, 30, 40, 50)
        for years, horizon in ((1, 365), (2, 730))
    }


def volume_confirmed(row: dict) -> bool:
    return (row.get("research_factors") or {}).get("obv_above_20week_ema") is True


def strong_observations(path: Path) -> list[dict]:
    rows = []
    with path.open("rb") as stream:
        for row in ijson.items(stream, "observations.item", use_float=True):
            if fundamentals_strong(row):
                rows.append(row)
    return rows


def candidate_episode_roots(rows: list[dict], predicate,
                            max_gap_days: int = 45) -> list[dict]:
    grouped = defaultdict(list)
    for row in rows:
        issuer = row.get("issuer_key") or row.get("security_id") or row.get("ticker")
        if issuer and row.get("entry_date"):
            grouped[str(issuer)].append(row)
    roots = []
    for issuer_rows in grouped.values():
        active = False
        previous = None
        for row in sorted(issuer_rows, key=lambda item: item["entry_date"]):
            current = date.fromisoformat(row["entry_date"])
            gap = (current - previous).days if previous is not None else None
            qualifies = bool(predicate(row))
            if qualifies and (not active or gap is None or gap > max_gap_days):
                roots.append(row)
            active = qualifies
            previous = current
    return sorted(roots, key=lambda row: (row["entry_date"], row.get("ticker", "")))


def analyze(artifact: Path, warehouse_path: Path) -> dict:
    roots = episode_roots(artifact)
    strong_rows = strong_observations(artifact)
    with SharadarWarehouse(warehouse_path, read_only=True) as warehouse:
        enrichment = attach_moving_averages(roots, warehouse.con)
        challenger_enrichment = attach_moving_averages(strong_rows, warehouse.con)
        outcome_enrichment = attach_outcomes(strong_rows, warehouse.con)

    dates = sorted({row["decision_date"] for row in roots})
    boundary = round(len(dates) * .8)
    final_dates = set(dates[boundary:])
    scopes = {"all_history": roots,
              "final_unseen_20pct": [row for row in roots
                                      if row["decision_date"] in final_dates]}
    valuation_modes = (
        "inside_fair_range", "at_or_below_fair_base", "at_or_below_buy_zone"
    )
    technical_fields = {
        "near_40week_ema": ("ema_40week_distance_pct",),
        "near_200week_sma": ("sma_200week_distance_pct",),
        "near_9month_ema": ("ema_9month_distance_pct",),
        "near_20month_ema": ("ema_20month_distance_pct",),
        "near_either_9month_or_20month_ema": (
            "ema_9month_distance_pct", "ema_20month_distance_pct"
        ),
        "near_both_9month_and_20month_ema": (
            "ema_9month_distance_pct", "ema_20month_distance_pct"
        ),
    }
    results = {}
    for scope, rows in scopes.items():
        strong = [row for row in rows if fundamentals_strong(row)]
        result = {"baseline": _summary(rows), "fundamentals_strong": _summary(strong),
                  "alignments": {}}
        for valuation_mode in valuation_modes:
            fair = [row for row in strong if valuation_aligned(row, valuation_mode)]
            combinations = {}
            for label, fields in technical_fields.items():
                combinations[label] = {
                    str(threshold): {
                        "without_volume_confirmation": _summary([
                            row for row in fair
                            if (any(near_average(row, field, threshold)
                                    for field in fields)
                                if label == "near_either_9month_or_20month_ema"
                                else all(near_average(row, field, threshold)
                                         for field in fields))
                        ]),
                        "with_obv_confirmation": _summary([
                            row for row in fair
                            if (any(near_average(row, field, threshold)
                                    for field in fields)
                                if label == "near_either_9month_or_20month_ema"
                                else all(near_average(row, field, threshold)
                                         for field in fields))
                            and volume_confirmed(row)
                        ]),
                    }
                    for threshold in (3, 5, 10)
                }
            result["alignments"][valuation_mode] = {
                "fundamentals_and_valuation": _summary(fair),
                **combinations,
            }
        results[scope] = result

    challenger_results = {}
    for valuation_mode in valuation_modes:
        challenger_results[valuation_mode] = {}
        for label, fields in technical_fields.items():
            challenger_results[valuation_mode][label] = {}
            for threshold in (3, 5, 10):
                candidate_roots = candidate_episode_roots(
                    strong_rows,
                    lambda row, vm=valuation_mode, fs=fields, t=threshold,
                    either=(label == "near_either_9month_or_20month_ema"): (
                        valuation_aligned(row, vm)
                        and ((any if either else all)(
                            near_average(row, f, t) for f in fs
                        ))
                    ),
                )
                challenger_results[valuation_mode][label][str(threshold)] = {
                    "without_volume_confirmation": {
                        "all_history": _summary(candidate_roots),
                        "final_unseen_20pct": _summary([
                            row for row in candidate_roots
                            if row["decision_date"] in final_dates
                        ]),
                    },
                }
                volume_roots = candidate_episode_roots(
                    strong_rows,
                    lambda row, vm=valuation_mode, fs=fields, t=threshold,
                    either=(label == "near_either_9month_or_20month_ema"): (
                        valuation_aligned(row, vm)
                        and ((any if either else all)(
                            near_average(row, f, t) for f in fs
                        ))
                        and volume_confirmed(row)
                    ),
                )
                challenger_results[valuation_mode][label][str(threshold)][
                    "with_obv_confirmation"
                ] = {
                    "all_history": _summary(volume_roots),
                    "final_unseen_20pct": _summary([
                        row for row in volume_roots
                        if row["decision_date"] in final_dates
                    ]),
                }
    return {
        "model": "moving_average_entry_alignment_v3",
        "status": "research_only",
        "production_effect": "none",
        "definitions": {
            "fundamentals": "Quality, Financial Strength, and Growth scores each >=70; no tested veto",
            "fair_price_modes": list(valuation_modes),
            "technical": (
                "Absolute distance from 40-week EMA, 200-week SMA, "
                "9-month EMA, 20-month EMA, their exact production OR, or both"
            ),
            "volume": "Weekly OBV must be above its 20-week EMA",
            "proximity_thresholds_pct": [3, 5, 10],
            "outcomes": [
                f"+{target}% within {horizon} days"
                for target in (25, 30, 40, 50)
                for horizon in (365, 730)
            ],
        },
        "chronological_final_dates": [min(final_dates), max(final_dates)] if final_dates else None,
        "enrichment": enrichment,
        "challenger_enrichment": challenger_enrichment,
        "challenger_outcome_enrichment": outcome_enrichment,
        "results": results,
        "replacement_challenger": {
            "scope": "All scored observations, including former Watch and Avoid rows",
            "baseline_recorded_buy": {
                "all_history": _summary(roots),
                "final_unseen_20pct": _summary([
                    row for row in roots if row["decision_date"] in final_dates
                ]),
            },
            "alignments": challenger_results,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--warehouse", type=Path,
                        default=Path("data/sharadar/warehouse.duckdb"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = analyze(args.artifact, args.warehouse)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
