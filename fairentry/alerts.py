"""Actionable alerts for strong Buy/Watch stocks near moving-average zones."""
from __future__ import annotations

import json
import os
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage


_MOVING_AVERAGE_ZONES = (
    ("sma_9month", "9-month SMA"),
    ("sma_20month", "20-month SMA"),
    ("sma_200week", "200-week SMA"),
)


def moving_average_zone_candidates(stocks: list[dict], metrics_by_ticker: dict,
                                   threshold_pct: float = 3.0) -> list[dict]:
    """Return strong Buy/Watch names near at least one configured SMA zone."""
    candidates = []
    for stock in stocks:
        if stock.get("verdict") not in {"Buy", "Watch"} or stock.get("vetoes"):
            continue
        scores = {
            category.get("id"): category.get("score")
            for category in stock.get("categories", [])
        }
        if any(not isinstance(scores.get(key), (int, float)) or scores[key] < 70
               for key in ("quality", "survival", "growth")):
            continue
        metrics = metrics_by_ticker.get(stock.get("ticker"), {})
        price = stock.get("price")
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        zones = []
        for metric_id, label in _MOVING_AVERAGE_ZONES:
            metric = metrics.get(metric_id) or {}
            average = metric.get("value") if isinstance(metric, dict) else metric
            if not isinstance(average, (int, float)) or average <= 0:
                continue
            distance = (price / average - 1) * 100
            if abs(distance) <= threshold_pct:
                zones.append({
                    "id": metric_id,
                    "label": label,
                    "average": round(average, 2),
                    "distance_pct": round(distance, 1),
                })
        if not zones:
            continue
        zones.sort(key=lambda zone: abs(zone["distance_pct"]))
        candidates.append({
            "ticker": stock["ticker"],
            "company": stock.get("company"),
            "verdict": stock.get("verdict"),
            "price": round(price, 2),
            "nearest_zone": zones[0],
            "zones": zones,
            "quality_score": scores["quality"],
            "financial_strength_score": scores["survival"],
            "growth_score": scores["growth"],
            "production_effect": "none",
        })
    return sorted(candidates, key=lambda item: (
        abs(item["nearest_zone"]["distance_pct"]), item["ticker"]
    ))


def email_wma_alerts(alerts: list[dict]) -> bool:
    """Email the alert list through Resend, or SMTP as a fallback."""
    if not alerts:
        return False
    lines = ["Fundamentally strong Buy/Watch stocks near an SMA zone:", ""]
    for item in alerts:
        zone = item["nearest_zone"]
        side = "above" if zone["distance_pct"] >= 0 else "below"
        lines.append(f"{item['ticker']} ({item['verdict']}): ${item['price']:.2f}; "
                     f"{zone['label']} ${zone['average']:.2f}; "
                     f"{abs(zone['distance_pct']):.1f}% {side}")
    return _send_email(f"FairEntry: {len(alerts)} stock(s) near an SMA zone", lines)


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
                     "Content-Type": "application/json",
                     "User-Agent": "FairEntry/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return 200 <= response.status < 300
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Resend rejected the email ({exc.code}): {detail}") from exc
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
        "Future messages will be sent for new Buy candidates, stocks reaching +25%, and exit/risk reviews.",
    ])


def email_trading_alerts(new_buys: list[dict], near_30: list[dict],
                         exit_reviews: list[dict] | None = None) -> dict:
    """Send separate emails for entry, target-proximity and exit-review events."""
    exit_reviews = exit_reviews or []
    sent = {"new_buys": False, "near_30": False, "exit_reviews": False}
    if new_buys:
        lines = ["New stocks entered the FairEntry Buy list:", ""]
        for item in new_buys:
            previous = f"; previously {item['from']}" if item.get("from") else ""
            confidence = f"; {item['confidence']}" if item.get("confidence") else ""
            lines.append(f"{item['ticker']} - {item.get('company') or ''}: "
                         f"${item.get('price', 0):.2f}; score {item.get('score')}"
                         f"{confidence}{previous}")
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
    if exit_reviews:
        lines = ["These held stocks need an exit or risk review:", ""]
        for item in exit_reviews:
            price = (f"${item['price']:.2f}" if isinstance(item.get("price"), (int, float))
                     else "price unavailable")
            lines.append(f"{item['ticker']} - {item.get('company') or ''}: {price}; "
                         f"{item.get('verdict')}; score {item.get('score')} — {item['reason']}")
        lines += ["", "Review the evidence before deciding whether to hold, trim, or sell."]
        sent["exit_reviews"] = _send_email(
            f"FairEntry: {len(exit_reviews)} exit/review alert(s)", lines)
    return sent
