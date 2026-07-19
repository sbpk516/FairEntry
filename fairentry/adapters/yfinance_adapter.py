"""yfinance adapter — completed-week 50WMA/200WMA values and distances.
Keyless (Yahoo public weekly history). Cached per ticker; called only for the
tickers passed in (shortlist), never the whole universe.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .cache_lite import cache_get, cache_put

OWNS = "yfinance"
_CACHE_NS = "yf_weekly_ma_v2"
_TTL_DAYS = 7


def _completed_closes(hist):
    closes = hist["Close"].dropna() if hist is not None and not hist.empty else None
    if closes is None or closes.empty:
        return closes
    today = datetime.now(timezone.utc).date()
    if today.weekday() < 5:
        try:
            last_date = closes.index[-1].date()
            if last_date.isocalendar()[:2] == today.isocalendar()[:2]:
                closes = closes.iloc[:-1]
        except Exception:
            pass
    return closes


def _metrics_from_closes(closes):
    if closes is None or len(closes) < 50:
        return None
    latest = float(closes.iloc[-1])
    if latest <= 0:
        return None
    out = {}
    for weeks in (50, 200):
        if len(closes) < weeks:
            continue
        average = float(closes.tail(weeks).mean())
        if average <= 0:
            continue
        out[f"sma_{weeks}week"] = round(average, 2)
        out[f"dist_{weeks}wma_pct"] = round((latest - average) / average * 100, 1)
    return out or None


def _compute(ticker: str):
    import yfinance as yf
    for _ in range(3):
        try:
            hist = yf.Ticker(ticker).history(period="max", interval="1wk",
                                             auto_adjust=True, repair=True)
            return _metrics_from_closes(_completed_closes(hist))
        except Exception:
            continue
    return None


def fetch(cfg, field_ids, tickers=None):
    metrics = {}
    for t in (tickers or []):
        cached = cache_get(_CACHE_NS, t, _TTL_DAYS)
        if cached is None:
            cached = _compute(t) or {}
            cache_put(_CACHE_NS, t, cached)
        if cached:
            metrics[t] = {k: v for k, v in cached.items() if k in field_ids}
    return metrics
