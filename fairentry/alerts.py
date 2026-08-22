"""Actionable alerts for shortlisted stocks near their 200-week average."""
from __future__ import annotations

import json
import os
import smtplib
import urllib.request
from email.message import EmailMessage


def wma_alerts(stocks: list[dict], metrics_by_ticker: dict, threshold_pct: float = 3.0) -> list[dict]:
    """Return Buy/Watch names whose latest price is within the threshold of 200 WMA."""
    alerts = []
    for stock in stocks:
        if stock.get("verdict") not in {"Buy", "Watch"}:
            continue
        metrics = metrics_by_ticker.get(stock.get("ticker"), {})
        wma_metric = metrics.get("sma_200week") or {}
        wma = wma_metric.get("value") if isinstance(wma_metric, dict) else wma_metric
        price = stock.get("price")
        if not isinstance(wma, (int, float)) or wma <= 0 or not isinstance(price, (int, float)):
            continue
        distance = (price / wma - 1) * 100
        if abs(distance) > threshold_pct:
            continue
        alerts.append({
            "ticker": stock["ticker"], "company": stock.get("company"),
            "verdict": stock["verdict"], "price": round(price, 2),
            "wma_200": round(wma, 2),
            "distance_pct": round(distance, 1),
        })
    return sorted(alerts, key=lambda item: abs(item["distance_pct"]))


def email_wma_alerts(alerts: list[dict]) -> bool:
    """Email the alert list through Resend, or SMTP as a fallback."""
    if not alerts:
        return False
    lines = ["Shortlisted Buy/Watch stocks near their 200-week moving average:", ""]
    for item in alerts:
        side = "above" if item["distance_pct"] >= 0 else "below"
        lines.append(f"{item['ticker']} ({item['verdict']}): ${item['price']:.2f}; "
                     f"200 WMA ${item['wma_200']:.2f}; {abs(item['distance_pct']):.1f}% {side}")
    return _send_email(f"FairEntry: {len(alerts)} stock(s) near the 200 WMA", lines)


def _send_email(subject: str, lines: list[str]) -> bool:
    """Send through Resend when configured, otherwise use SMTP."""
    recipient = (os.environ.get("FAIRENTRY_ALERT_EMAIL")
                 or os.environ.get("WMA_ALERT_EMAIL"))
    api_key = os.environ.get("RESEND_API_KEY")
    body = "\n".join(lines) + "\n\nFor personal research only, not investment advice."
    if recipient and api_key:
        sender = os.environ.get("RESEND_FROM_EMAIL", "FairEntry <onboarding@resend.dev>")
        payload = json.dumps({"from": sender, "to": [recipient],
                              "subject": subject, "text": body}).encode("utf-8")
        request = urllib.request.Request(
            "https://api.resend.com/emails", data=payload, method="POST",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            return 200 <= response.status < 300
    host = os.environ.get("SMTP_HOST")
    if not recipient or not host:
        return False
    sender = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER") or recipient
    message = EmailMessage()
    message["Subject"], message["From"], message["To"] = subject, sender, recipient
    message.set_content(body)
    port = int(os.environ.get("SMTP_PORT", "587"))
    user, password = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
    if os.environ.get("SMTP_SSL", "").lower() in {"1", "true", "yes"}:
        server = smtplib.SMTP_SSL(host, port, timeout=20)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
        server.starttls()
    with server:
        if user and password:
            server.login(user, password)
        server.send_message(message)
    return True


def send_test_email() -> bool:
    """Send a harmless delivery check using the configured provider."""
    return _send_email("FairEntry email notifications are active", [
        "Your FairEntry email connection is working.",
        "Future messages will be sent for new Buy candidates and stocks reaching +25%.",
    ])


def email_trading_alerts(new_buys: list[dict], near_30: list[dict]) -> dict:
    """Send separate event emails for new Buy candidates and +25% milestones."""
    sent = {"new_buys": False, "near_30": False}
    if new_buys:
        lines = ["New stocks entered the FairEntry Buy list:", ""]
        for item in new_buys:
            previous = f"; previously {item['from']}" if item.get("from") else ""
            lines.append(f"{item['ticker']} - {item.get('company') or ''}: "
                         f"${item.get('price', 0):.2f}; score {item.get('score')}{previous}")
        sent["new_buys"] = _send_email(
            f"FairEntry: {len(new_buys)} new Buy candidate(s)", lines)
    if near_30:
        lines = ["These tracked Buy positions reached at least +25% and are close to the +30% target:", ""]
        for item in near_30:
            lines.append(f"{item['ticker']} - {item.get('company') or ''}: "
                         f"{item['gain_pct']:+.1f}% (${item['entry_price']:.2f} -> ${item['price']:.2f}); "
                         f"+30% target ${item['target_price']:.2f}")
        sent["near_30"] = _send_email(
            f"FairEntry: {len(near_30)} stock(s) close to +30% target", lines)
    return sent
