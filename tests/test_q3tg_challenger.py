from fairentry.backtest.q3tg_challenger import (
    market_regime_passes,
    report_stock_trend_passes,
)


def test_report_stock_trend_requires_every_available_rule():
    factors = {
        "price_above_sma200": True,
        "sma200_rising_20_sessions": True,
        "momentum_12_1_pct": 12,
        "price_to_sma50_pct": 4,
    }
    assert report_stock_trend_passes({"research_factors": factors})
    assert not report_stock_trend_passes({
        "research_factors": {**factors, "price_to_sma50_pct": -0.1}
    })
    assert not report_stock_trend_passes({
        "research_factors": {**factors, "sma200_rising_20_sessions": False}
    })


def test_market_regime_is_strictly_supportive_only():
    assert market_regime_passes({
        "research_factors": {"spy_above_10month_sma": True}
    })
    assert not market_regime_passes({
        "research_factors": {"spy_above_10month_sma": None}
    })
