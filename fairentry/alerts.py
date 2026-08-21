"""Actionable alerts for shortlisted stocks near their 200-week average."""
from __future__ import annotations

import os
import smtplib
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
    """Email the alert list when SMTP environment variables are configured."""
    recipient, host = os.environ.get("WMA_ALERT_EMAIL"), os.environ.get("SMTP_HOST")
    if not alerts or not recipient or not host:
        return False
    sender = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER") or recipient
    message = EmailMessage()
    message["Subject"] = f"FairEntry: {len(alerts)} stock(s) near the 200 WMA"
    message["From"], message["To"] = sender, recipient
    lines = ["Shortlisted Buy/Watch stocks near their 200-week moving average:", ""]
    for item in alerts:
        side = "above" if item["distance_pct"] >= 0 else "below"
        lines.append(f"{item['ticker']} ({item['verdict']}): ${item['price']:.2f}; "
                     f"200 WMA ${item['wma_200']:.2f}; {abs(item['distance_pct']):.1f}% {side}")
    message.set_content("\n".join(lines) + "\n\nFor personal research only, not investment advice.")
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


def _send_email(subject: str, lines: list[str]) -> bool:
    """Send one FairEntry event email using the shared SMTP configuration."""
    recipient = (os.environ.get("FAIRENTRY_ALERT_EMAIL")
                 or os.environ.get("WMA_ALERT_EMAIL"))
    host = os.environ.get("SMTP_HOST")
    if not recipient or not host:
        return False
    sender = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER") or recipient
    message = EmailMessage()
    message["Subject"], message["From"], message["To"] = subject, sender, recipient
    message.set_content("\n".join(lines) + "\n\nFor personal research only, not investment advice.")
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
