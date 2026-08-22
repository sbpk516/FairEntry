"""yfinance adapter — the true 200-week moving average + distance from it.
Keyless (Yahoo public weekly history). Cached per ticker; called only for the
tickers passed in (shortlist), never the whole universe.
"""
from __future__ import annotations

from .cache_lite import cache_get, cache_put

OWNS = "yfinance"
_CACHE_NS = "yf_200wma"
_TTL_DAYS = 7


def _compute(ticker: str):
    import yfinance as yf
    for _ in range(3):
        try:
            hist = yf.Ticker(ticker).history(period="max", interval="1wk", auto_adjust=True)
            closes = hist["Close"].dropna() if not hist.empty else None
            if closes is None or len(closes) < 200:
                return None
            sma = float(closes.tail(200).mean())
            latest = float(closes.iloc[-1])
            if sma <= 0 or latest <= 0:
                return None
            return {"sma_200week": round(sma, 2),
                    "dist_200wma_pct": round((latest - sma) / sma * 100, 1)}
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


def fetch_quotes(tickers=None):
    """Independent current quotes for held/previously recommended names.

    These quotes are tracking-only and must never affect board scoring.
    """
    import yfinance as yf
    out = {}
    for ticker in sorted(set(tickers or [])):
        try:
            obj = yf.Ticker(ticker)
            price = obj.fast_info.get("last_price")
            if not isinstance(price, (int, float)) or price <= 0:
                hist = obj.history(period="5d", interval="1d", auto_adjust=False)
                price = float(hist["Close"].dropna().iloc[-1]) if not hist.empty else None
            if isinstance(price, (int, float)) and price > 0:
                out[ticker] = round(float(price), 4)
        except Exception:
            continue
    return out
