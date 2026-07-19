from datetime import date, timedelta

from fairentry.analytics.chart_history import (
    _chart_payload,
    chart_filename,
    crossover_signal,
    weekly_bars,
)


def test_chart_filename_keeps_url_safe_ticker_names():
    assert chart_filename("BRK.B") == "BRK.B.json"
    assert chart_filename("ABC/DEF") == "ABC_DEF.json"


def test_weekly_bars_aggregates_ohlcv():
    daily = [
        {"d": "2026-01-05", "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 100},
        {"d": "2026-01-06", "o": 10.5, "h": 12, "l": 10, "c": 11.5, "v": 150},
        {"d": "2026-01-12", "o": 12, "h": 13, "l": 11, "c": 12.5, "v": 200},
    ]

    weeks = weekly_bars(daily)

    assert weeks == [
        {"d": "2026-01-06", "o": 10, "h": 12, "l": 9, "c": 11.5, "v": 250},
        {"d": "2026-01-12", "o": 12, "h": 13, "l": 11, "c": 12.5, "v": 200},
    ]


def test_weekly_bars_excludes_an_incomplete_current_week():
    daily = [
        {"d": "2026-07-13", "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 100},
        {"d": "2026-07-14", "o": 10.5, "h": 12, "l": 10, "c": 11.5, "v": 150},
    ]
    assert weekly_bars(daily, today=date(2026, 7, 15)) == []
    assert len(weekly_bars(daily, today=date(2026, 7, 18))) == 1


def test_chart_payload_exposes_50_and_200_week_levels_and_labels():
    start = date(2021, 1, 4)
    daily = []
    for index in range(220):
        close = float(index + 1)
        daily.append({"d": (start + timedelta(weeks=index)).isoformat(),
                      "o": close, "h": close, "l": close, "c": close, "v": 100})
    payload = _chart_payload({"ticker": "T", "company": "Test", "valuation": {}}, daily)
    levels = payload["levels"]
    assert levels["sma50week"] == 195.5
    assert levels["sma200week"] == 120.5
    assert levels["sma50week_position"]["label"] == "Above"
    assert levels["sma200week_position"]["label"] == "Above"
    assert payload["weekly_signals"]["intermediate_cross"]["label"] == "Bullish alignment"
    assert payload["weekly_signals"]["long_term_reclaim"]["note"].startswith("Supporting")
    assert payload["daily_signals"]["golden_cross"]["label"] == "Bullish alignment"


def test_crossover_signal_never_calls_it_a_breakout():
    signal = crossover_signal(list(range(1, 221)), 10, 200)
    assert signal["status"] == "satisfied"
    assert "breakout" not in signal["label"].lower()
    assert "not a standalone breakout" in signal["note"]
