import pandas as pd

from fairentry.adapters.yfinance_adapter import _metrics_from_closes


def test_weekly_adapter_returns_50_and_200_week_values_from_same_series():
    closes = pd.Series([float(value) for value in range(1, 221)])
    metrics = _metrics_from_closes(closes)

    assert metrics["sma_50week"] == 195.5
    assert metrics["dist_50wma_pct"] == 12.5
    assert metrics["sma_200week"] == 120.5
    assert metrics["dist_200wma_pct"] == 82.6


def test_weekly_adapter_can_return_50wma_when_200_weeks_are_unavailable():
    metrics = _metrics_from_closes(pd.Series([float(value) for value in range(1, 61)]))

    assert metrics["sma_50week"] == 35.5
    assert "sma_200week" not in metrics
