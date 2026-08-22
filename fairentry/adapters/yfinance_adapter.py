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
    names = sorted(set(tickers or []))
    if not names:
        return {}
    out = {}
    try:
        # One bounded, threaded request avoids a slow request per historical
        # recommendation as the retained tracking list grows.
        data = yf.download(names, period="5d", interval="1d", auto_adjust=False,
                           progress=False, threads=True, timeout=20)
        closes = data.get("Close")
        if closes is not None:
            if len(names) == 1 and getattr(closes, "ndim", 1) == 1:
                closes = closes.to_frame(name=names[0])
            for ticker in names:
                if ticker not in closes:
                    continue
                series = closes[ticker].dropna()
                if not series.empty and float(series.iloc[-1]) > 0:
                    out[ticker] = round(float(series.iloc[-1]), 4)
    except Exception:
        pass
    return out
