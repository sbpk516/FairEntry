from fairentry.analytics.demand_momentum import (
    _ret,
    _up_down_volume,
    _volume_accumulation_label,
)


def test_period_return_uses_trading_day_window():
    closes = [100.0, 102.0, 110.0]

    assert _ret(closes, 1) == 7.84
    assert _ret(closes, 2) == 10.0


def test_up_down_volume_compares_volume_on_advancing_vs_declining_days():
    closes = [10.0, 11.0, 10.0, 12.0]
    volumes = [50.0, 100.0, 200.0, 300.0]

    assert _up_down_volume(closes, volumes, days=3) == 2.0


def test_volume_accumulation_label_buckets():
    assert _volume_accumulation_label(None) == "unknown"
    assert _volume_accumulation_label(1.4) == "accumulation"
    assert _volume_accumulation_label(0.75) == "distribution"
    assert _volume_accumulation_label(1.0) == "neutral"
