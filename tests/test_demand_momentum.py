from fairentry.analytics.demand_momentum import (
    _ret,
    _up_down_volume,
    _volume_accumulation_label,
    build_context,
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


def test_live_context_exposes_zero_effect_high_confidence_shadow(monkeypatch):
    stock = [100.0] * 121 + [100.0 + index for index in range(39)]
    sector = [100.0] * 160
    cached = {
        "bench": {},
        "series": {
            "TEST": {"close": stock, "volume": [1000.0] * 160},
            "XLK": {"close": sector, "volume": [1000.0] * 160},
        },
    }
    monkeypatch.setattr(
        "fairentry.analytics.demand_momentum.cache_get",
        lambda *_args, **_kwargs: cached,
    )

    result = build_context([({
        "ticker": "TEST",
        "sector": "Technology",
        "verdict": "Buy",
    }, {})])["TEST"]

    shadow = result["high_confidence_buy_shadow"]
    assert result["six_month_sector_confirmation"]["classification"] == "improving"
    assert shadow["eligible"] is True
    assert shadow["label"] == "High-Confidence Buy · shadow"
    assert shadow["score_effect"] == 0
    assert shadow["verdict_effect"] == "none"
