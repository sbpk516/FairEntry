import pandas as pd

from fairentry.adapters.yfinance_adapter import _compute_from_history


def test_monthly_ema_and_weekly_obv_are_computed_from_adjusted_daily_history():
    dates = pd.bdate_range("2021-01-04", periods=1100)
    history = pd.DataFrame(
        {
            "Close": [50 + index * 0.1 for index in range(len(dates))],
            "Volume": [1_000_000 + index for index in range(len(dates))],
        },
        index=dates,
    )
    result = _compute_from_history(history)

    assert result["ema_9month"] > result["ema_20month"] > 0
    assert abs(result["dist_9month_ema_pct"]) < abs(result["dist_20month_ema_pct"])
    assert result["obv_above_20week_ema"] is True
    assert result["sma_200week"] > 0


def test_indicator_history_is_missing_until_required_lookback_exists():
    dates = pd.bdate_range("2026-01-02", periods=30)
    history = pd.DataFrame({"Close": range(100, 130), "Volume": 1000}, index=dates)
    result = _compute_from_history(history)

    assert result is None
