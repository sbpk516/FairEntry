#!/usr/bin/env python3
"""Evaluate the report's reproducible regime/trend block as a challenger."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fairentry.backtest.q3tg_challenger import (  # noqa: E402
    market_regime_passes,
    report_stock_trend_passes,
)
from fairentry.backtest.three_way_entry import near_average, valuation_aligned  # noqa: E402
from fairentry.sharadar import SharadarWarehouse  # noqa: E402
from scripts.analyze_three_way_entry import (  # noqa: E402
    _summary,
    attach_moving_averages,
    attach_outcomes,
    candidate_episode_roots,
    strong_observations,
    volume_confirmed,
)


def attach_market_regime(rows: list[dict], connection) -> dict:
    prices = connection.execute("""
      SELECT date,closeadj FROM canonical_benchmarks
      WHERE ticker='SPY' ORDER BY date
    """).fetchdf()
    prices["date"] = pd.to_datetime(prices["date"])
    monthly = (
        prices.set_index("date")["closeadj"]
        .resample("ME")
        .agg(["last"])
        .rename(columns={"last": "close"})
    )
    actual_dates = prices.groupby(prices["date"].dt.to_period("M"))["date"].max()
    monthly["actual_date"] = actual_dates.to_numpy()
    monthly = monthly.dropna(subset=["close", "actual_date"])

    by_date = {}
    for value in sorted({str(row["decision_date"])[:10] for row in rows}):
        observed = pd.Timestamp(value)
        available = monthly[monthly["actual_date"] <= observed]
        latest = available.tail(10)
        by_date[value] = (
            bool(latest["close"].iloc[-1] >= latest["close"].mean())
            if len(latest) == 10 else None
        )
    available_count = 0
    supportive = 0
    for row in rows:
        signal = by_date.get(str(row["decision_date"])[:10])
        row.setdefault("research_factors", {})["spy_above_10month_sma"] = signal
        available_count += signal is not None
        supportive += signal is True
    return {
        "observations": len(rows),
        "available": available_count,
        "supportive": supportive,
        "benchmark": "SPY adjusted close as total-return proxy",
    }


def base_alignment(row: dict, ema_fields: tuple[str, ...]) -> bool:
    return (
        valuation_aligned(row, "at_or_below_fair_base")
        and all(near_average(row, field, 5) for field in ema_fields)
        and volume_confirmed(row)
    )


def analyze(artifact: Path, warehouse_path: Path) -> dict:
    rows = strong_observations(artifact)
    with SharadarWarehouse(warehouse_path, read_only=True) as warehouse:
        price_enrichment = attach_moving_averages(rows, warehouse.con)
        regime_enrichment = attach_market_regime(rows, warehouse.con)
        outcome_enrichment = attach_outcomes(rows, warehouse.con)

    dates = sorted({row["decision_date"] for row in rows})
    final_dates = set(dates[round(len(dates) * .8):])
    definitions = {
        "9_month_ema": ("ema_9month_distance_pct",),
        "20_month_ema": ("ema_20month_distance_pct",),
        "both_monthly_emas": (
            "ema_9month_distance_pct", "ema_20month_distance_pct"
        ),
    }
    results = {}
    for label, fields in definitions.items():
        predicates = {
            "current_strict_alignment": lambda row, fs=fields: base_alignment(row, fs),
            "plus_market_regime": lambda row, fs=fields: (
                base_alignment(row, fs) and market_regime_passes(row)
            ),
            "plus_stock_trend": lambda row, fs=fields: (
                base_alignment(row, fs) and report_stock_trend_passes(row)
            ),
            "plus_market_and_stock_trend": lambda row, fs=fields: (
                base_alignment(row, fs)
                and market_regime_passes(row)
                and report_stock_trend_passes(row)
            ),
        }
        results[label] = {}
        for variant, predicate in predicates.items():
            roots = candidate_episode_roots(rows, predicate)
            results[label][variant] = {
                "all_history": _summary(roots),
                "later_chronological_20pct": _summary([
                    row for row in roots if row["decision_date"] in final_dates
                ]),
            }
    return {
        "model": "q3tg_regime_trend_challenger_v1",
        "status": "research_only",
        "production_effect": "none",
        "scope": (
            "Incremental event-level test on the existing strict FairEntry alignment; "
            "not the report's three-stock portfolio simulation"
        ),
        "implemented_rules": {
            "market": "Last completed SPY month at or above its 10-month SMA",
            "stock": (
                "Price above a 200-day SMA that is higher than 20 sessions earlier; "
                "12-1 momentum positive; price 0% to 8% above 50-day SMA"
            ),
        },
        "not_implemented": [
            "idiosyncratic-volatility cross-sectional rank",
            "scheduled earnings-date exclusion",
            "three-stock construction, exits, weekly tranches, and cash returns",
        ],
        "enrichment": {
            "price": price_enrichment,
            "market_regime": regime_enrichment,
            "outcomes": outcome_enrichment,
        },
        "later_dates": [min(final_dates), max(final_dates)] if final_dates else None,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--warehouse", type=Path,
                        default=Path("data/sharadar/warehouse.duckdb"))
    parser.add_argument("--out", type=Path,
                        default=Path("data/sharadar/reports/q3tg-regime-trend-v1.json"))
    args = parser.parse_args()
    result = analyze(args.artifact, args.warehouse)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.out), "status": result["status"]}, indent=2))


if __name__ == "__main__":
    main()
