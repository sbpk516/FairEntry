import json

from fairentry.alerts import _send_email, moving_average_zone_candidates


def test_moving_average_zones_require_strong_buy_or_watch_and_any_zone():
    categories = [
        {"id": "quality", "score": 80},
        {"id": "survival", "score": 75},
        {"id": "growth", "score": 90},
    ]
    stocks = [
        {"ticker": "BUY", "company": "Buy Co", "verdict": "Buy", "price": 102.0,
         "categories": categories, "vetoes": []},
        {"ticker": "WATCH", "company": "Watch Co", "verdict": "Watch", "price": 98.0,
         "categories": categories, "vetoes": []},
        {"ticker": "FAR", "company": "Far Co", "verdict": "Watch", "price": 120.0,
         "categories": categories, "vetoes": []},
        {"ticker": "AVOID", "company": "Avoid Co", "verdict": "Avoid", "price": 100.0,
         "categories": categories, "vetoes": []},
        {"ticker": "WEAK", "company": "Weak Co", "verdict": "Watch", "price": 100.0,
         "categories": [{**row, "score": 40} for row in categories], "vetoes": []},
    ]
    metrics = {
        "BUY": {"sma_9month": {"value": 100.0}, "sma_200week": {"value": 101.0}},
        "WATCH": {"sma_20month": {"value": 100.0}},
        "FAR": {"sma_200week": {"value": 100.0}},
        "AVOID": {"sma_200week": {"value": 100.0}},
        "WEAK": {"sma_9month": {"value": 100.0}},
    }
    rows = moving_average_zone_candidates(stocks, metrics, threshold_pct=3.0)
    assert [row["ticker"] for row in rows] == ["BUY", "WATCH"]
    assert rows[0]["nearest_zone"]["label"] == "200-week SMA"
    assert [zone["label"] for zone in rows[0]["zones"]] == [
        "200-week SMA", "9-month SMA"
    ]
    assert rows[1]["nearest_zone"]["label"] == "20-month SMA"
    assert all(row["verdict"] in {"Buy", "Watch"} for row in rows)


def test_send_email_prefers_resend(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data)
        return Response()

    monkeypatch.setenv("FAIRENTRY_ALERT_EMAIL", "recipient@example.com")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "FairEntry <onboarding@resend.dev>")
    monkeypatch.setattr("fairentry.alerts.urllib.request.urlopen", fake_urlopen)

    assert _send_email("Test", ["Hello"]) is True
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["payload"]["to"] == ["recipient@example.com"]
    assert captured["payload"]["subject"] == "Test"
