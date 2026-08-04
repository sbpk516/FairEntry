from fairentry.alerts import wma_alerts


def test_wma_alerts_only_include_shortlisted_names_inside_threshold():
    stocks = [
        {"ticker": "BUY", "company": "Buy Co", "verdict": "Buy", "price": 102.0},
        {"ticker": "WATCH", "company": "Watch Co", "verdict": "Watch", "price": 98.0},
        {"ticker": "FAR", "company": "Far Co", "verdict": "Buy", "price": 120.0},
        {"ticker": "AVOID", "company": "Avoid Co", "verdict": "Avoid", "price": 100.0},
    ]
    metrics = {
        "BUY": {"sma_200week": {"value": 100.0}},
        "WATCH": {"sma_200week": {"value": 100.0}},
        "FAR": {"sma_200week": {"value": 100.0}},
        "AVOID": {"sma_200week": {"value": 100.0}},
    }
    alerts = wma_alerts(stocks, metrics, threshold_pct=3.0)
    assert [a["ticker"] for a in alerts] == ["BUY", "WATCH"]
    assert alerts[0]["wma_200"] == 100.0
