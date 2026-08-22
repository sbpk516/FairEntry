from fairentry.backtest.liquidity import compare_liquidity_thresholds


def _row(ticker, volume, hit=None, drawdown=-10, verdict="Buy"):
    return {
        "ticker": ticker, "security_id": ticker, "avg_dollar_volume": volume,
        "verdict": verdict,
        "_tuning_outcome": {
            "first_hit_secondary_days": hit, "last_observed_days": 365,
            "terminal_days": None, "max_drawdown_pct": drawdown,
        },
    }


def test_liquidity_comparison_counts_success_failure_and_drawdown():
    report = compare_liquidity_thresholds([
        _row("A", 6_000_000, hit=100, drawdown=-12),
        _row("B", 12_000_000, hit=None, drawdown=-25),
        _row("C", 22_000_000, hit=200, drawdown=-8),
        _row("D", 30_000_000, verdict="Watch"),
    ])
    five, ten, twenty = report["rows"][0], report["rows"][1], report["rows"][3]
    assert five["success_rate_pct"] == 66.7
    assert five["failure_rate_pct"] == 33.3
    assert five["worst_max_drawdown_pct"] == -25
    assert ten["unique_buy_stocks"] == 2
    assert twenty["success_rate_pct"] == 100.0
    assert report["bid_ask_spread_status"] == "unavailable"
