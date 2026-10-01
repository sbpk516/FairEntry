from datetime import datetime, timezone

import pandas as pd
import pytest

from fairentry.investors.prices import summarize, enrich_prices
from fairentry.investors.ledger import Ledger


def test_completed_periods_and_high_use_consistent_closes():
    dates = pd.date_range('2020-01-01', '2026-09-30', freq='B')
    history = pd.DataFrame({'Close': 100.0}, index=dates)
    history.loc['2026-09-29', 'Close'] = 80
    history.loc['2026-09-30', 'Close'] = 1000  # unfinished session must be excluded
    result = summarize(history, datetime(2026, 9, 30, tzinfo=timezone.utc), 'USD')
    assert result['price'] == 80
    assert result['sma_200w'] == 100
    assert result['sma_20m'] == 100
    assert result['sma_36m'] == 100
    assert result['distance_36m_pct'] == pytest.approx(-20)
    assert result['distance_200w_pct'] == pytest.approx(-20)
    assert result['below_high_pct'] == pytest.approx(20)
    assert result['history_high'] == 100
    assert result['as_of'] == '2026-09-29'


def test_short_history_and_missing_month_are_unknown():
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    dates = pd.date_range('2026-01-01', '2026-09-29', freq='B')
    result = summarize(pd.DataFrame({'Close': 10.0}, index=dates), now)
    assert result['sma_200w'] is None and result['sma_20m'] is None
    assert result['sma_36m'] is None
    dates = pd.date_range('2020-01-01', '2026-09-29', freq='B')
    dates = dates[~((dates.year == 2026) & (dates.month == 1))]
    result = summarize(pd.DataFrame({'Close': 10.0}, index=dates), now)
    assert result['sma_200w'] is None and result['sma_20m'] is None


def test_distinct_month_windows_and_weekend_month_end():
    dates = pd.date_range('2020-01-01', '2026-08-31', freq='B')
    history = pd.DataFrame({'Close': 10.0}, index=dates)
    history.loc['2025-01-01':, 'Close'] = 20
    result = summarize(history, datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert result['sma_20m'] == 20
    assert result['sma_36m'] == pytest.approx((20 * 20 + 16 * 10) / 36)
    # August 2025 ends Sunday; Friday's final close still completes that month.
    result = summarize(history, datetime(2025, 9, 1, tzinfo=timezone.utc))
    assert result['sma_20m'] == pytest.approx((8 * 20 + 12 * 10) / 20)


def test_failure_preserves_old_observation_and_caches_attempt(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path / 'prices.db')
    ledger.put('price_levels_v1', {'TEST': {'price': 10, 'as_of': '2026-09-01'}})
    def fail(*args):
        raise ValueError('unavailable')
    monkeypatch.setattr('fairentry.investors.prices.fetch_price', fail)
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    result = enrich_prices(['TEST'], ledger, now)['TEST']
    assert result['price'] == 10 and result['as_of'] == '2026-09-01'
    assert result['error'] == 'ValueError'
    monkeypatch.setattr('fairentry.investors.prices.fetch_price', lambda *a: pytest.fail('cached retry'))
    enrich_prices(['TEST'], ledger, now)
    ledger.close()
