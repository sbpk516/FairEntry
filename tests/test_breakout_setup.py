from fairentry.analytics.breakout_setup import (
    _fundamental_label,
    _overall,
    _sector_trend,
    _short_label,
    _support_resistance,
    _trend_label,
)


def test_trend_label_handles_direction_and_missing_data():
    assert _trend_label([]) == "unknown"
    assert _trend_label([1, 4]) == "improving"
    assert _trend_label([4, 1]) == "worsening"
    assert _trend_label([20, 16], lower_is_better=True) == "improving"


def test_fundamental_label_requires_weight_of_evidence():
    assert _fundamental_label(["improving", "improving", "stable"]) == "improving"
    assert _fundamental_label(["worsening", "worsening", "stable"]) == "worsening"
    assert _fundamental_label(["improving", "worsening"]) == "mixed"
    assert _fundamental_label(["unknown", "unknown"]) == "unknown"


def test_support_resistance_detects_breakout_and_basing():
    base = [100.0] * 80
    breakout = base + [103.0]
    assert _support_resistance(breakout)["label"] == "breakout"

    basing = [100.0, 115.0] * 45
    got = _support_resistance(basing)
    assert got["label"] == "basing"
    assert got["support_touches"] >= 2


def test_short_pressure_labels_current_and_trend():
    assert _short_label(None, []) == "unknown"
    assert _short_label(12, [15, 13]) == "easing"
    assert _short_label(12, [10, 12]) == "rising"
    assert _short_label(22, [22]) == "crowded"
    assert _short_label(3, [3]) == "low"


def test_sector_trend_reads_market_confirmation_context():
    closes = list(range(100, 320))
    spy = list(range(100, 300)) + [300] * 20
    got = _sector_trend("XLK", {"XLK": {"close": closes}, "SPY": {"close": spy}})

    assert got["label"] == "supportive"
    assert got["above_50d"] is True
    assert got["above_200d"] is True


def test_overall_is_context_label_not_score():
    assert _overall("improving", "breakout", "easing", "supportive") == "confirmed"
    assert _overall("stabilizing", "basing", "moderate", "neutral") == "building"
    assert _overall("worsening", "neutral", "rising", "hostile") == "failed"
