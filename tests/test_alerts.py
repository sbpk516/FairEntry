import json

from fairentry.alerts import _send_email, strong_business_wma_candidates, wma_alerts


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


def test_strong_business_wma_candidates_do_not_require_a_buy_verdict():
    categories = [
        {"id": "quality", "score": 80},
        {"id": "survival", "score": 75},
        {"id": "growth", "score": 90},
    ]
    stocks = [
        {"ticker": "STRONG", "company": "Strong Co", "verdict": "Watch",
         "price": 101.0, "categories": categories, "vetoes": []},
        {"ticker": "WEAK", "company": "Weak Co", "verdict": "Buy",
         "price": 100.0, "categories": [{**c, "score": 40} for c in categories],
         "vetoes": []},
    ]
    metrics = {
        "STRONG": {"sma_200week": {"value": 100.0}},
        "WEAK": {"sma_200week": {"value": 100.0}},
    }

    rows = strong_business_wma_candidates(stocks, metrics, threshold_pct=3.0)

    assert [row["ticker"] for row in rows] == ["STRONG"]
    assert rows[0]["verdict"] == "Watch"
    assert rows[0]["production_effect"] == "none"


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
