from fairentry.analytics.chart_history import chart_filename, weekly_bars


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
