from __future__ import annotations

import pandas as pd

from fairentry.analytics.entry_alignment import compute_entry_alignment_from_history
from fairentry.backtest.evidence import _return_attainment_view
from fairentry.backtest.sfa_replay import SFAReplay
from fairentry.scoring.engine import buy_entry_alignment


def _daily_history(start="2023-01-02", end="2025-01-10"):
    index = pd.bdate_range(start, end)
    frame = pd.DataFrame({"Close": 100.0, "Volume": 1_000_000.0}, index=index)
    frame.loc[frame.index >= "2025-01-06", "Close"] = 101.0
    return frame


def test_point_in_time_indicators_ignore_later_rows_in_incomplete_week():
    history = _daily_history()
    cutoff = "2025-01-08"
    expected = compute_entry_alignment_from_history(history.loc[:cutoff])
    actual = compute_entry_alignment_from_history(history, asof=cutoff)
    assert actual == expected
    assert actual["entry_alignment_price_date"] == cutoff

    # The future Thursday/Friday observations are intentionally extreme. They
    # must change an unrestricted calculation, proving the test is meaningful.
    history.loc[history.index > cutoff, ["Close", "Volume"]] = [200.0, 100_000_000.0]
    unrestricted = compute_entry_alignment_from_history(history)
    assert unrestricted["ema_9month"] != actual["ema_9month"]


def test_sfa_daily_enrichment_feeds_the_exact_production_or_rule():
    import duckdb

    history = _daily_history(end="2025-01-08").reset_index(names="date")
    history.insert(0, "ticker", "ABC")
    history["close"] = history["Close"]
    history["closeadj"] = history["Close"]
    history = history[["ticker", "date", "close", "closeadj", "Volume"]].rename(
        columns={"Volume": "volume"}
    )
    con = duckdb.connect(":memory:")
    con.register("daily", history)
    con.execute("CREATE TABLE sfa_prices AS SELECT * FROM daily")
    con.unregister("daily")
    item = {
        "sec": {"ticker": "ABC"},
        "metrics": {"price": {"value": 101.0, "source": "test", "fetched_at": "2025-01-08"}},
    }
    SFAReplay(type("Warehouse", (), {"con": con})())._enrich_entry_alignment(
        [item], "2025-01-08"
    )
    metrics = {key: value["value"] for key, value in item["metrics"].items()}
    assert metrics["obv_above_20week_ema"] == 1
    result = buy_entry_alignment(
        {"buy_entry_alignment": {
            "category_minimum": 70,
            "categories": ["quality", "survival", "growth"],
            "fair_value_method_minimum": 1,
            "ema_proximity_pct": 5,
            "ema_policy": "any",
            "monthly_ema_metrics": ["ema_9month", "ema_20month"],
            "obv_metric": "obv_above_20week_ema",
        }},
        {"quality": 70, "survival": 70, "growth": 70},
        metrics,
        {"fair_base": 101, "method_count": 1},
    )
    assert result["passes"] is True
    assert result["ema_policy"] == "any"
    metrics["ema_20month"] = 50
    one_of_two = buy_entry_alignment(
        {"buy_entry_alignment": {"ema_policy": "any"}},
        {"quality": 70, "survival": 70, "growth": 70},
        metrics,
        {"fair_base": 101, "method_count": 1},
    )
    assert one_of_two["monthly_emas"]["ema_9month"]["passes"] is True
    assert one_of_two["monthly_emas"]["ema_20month"]["passes"] is False
    assert one_of_two["passes"] is True
    con.close()


def test_return_report_includes_sample_hit_rate_and_drawdown_for_both_horizons():
    rows = [
        {
            "ticker": "A", "entry_date": "2020-01-01",
            "return_milestones": {
                "first_hit_days": {"30": 300}, "last_observed_days": 800,
                "max_drawdown_pct_by_horizon": {"365": -12, "730": -18},
            },
        },
        {
            "ticker": "B", "entry_date": "2020-01-01",
            "return_milestones": {
                "first_hit_days": {"30": 500}, "last_observed_days": 800,
                "max_drawdown_pct_by_horizon": {"365": -24, "730": -30},
            },
        },
    ]
    report = _return_attainment_view(rows, (30,), (365, 730))["matrix"]["30"]
    assert report["365"] == {
        **report["365"],
        "evaluable": 2,
        "reached": 1,
        "hit_rate_pct": 50.0,
        "drawdown_sample": 2,
        "median_max_drawdown_pct": -18.0,
        "worst_max_drawdown_pct": -24.0,
    }
    assert report["730"]["evaluable"] == 2
    assert report["730"]["reached"] == 2
    assert report["730"]["hit_rate_pct"] == 100.0
    assert report["730"]["median_max_drawdown_pct"] == -24.0
    assert report["730"]["worst_max_drawdown_pct"] == -30.0
