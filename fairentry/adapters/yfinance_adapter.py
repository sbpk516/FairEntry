"""Keyless price-history indicators for shortlisted names.

The daily adjusted history is resampled without future observations to produce
the monthly SMA/EMA zones and weekly indicators used by discovery and entry.
"""
from __future__ import annotations

from ..analytics.entry_alignment import compute_entry_alignment_from_history
from .cache_lite import cache_get, cache_put

OWNS = "yfinance"
_CACHE_NS = "yf_entry_alignment_v2"
_TTL_DAYS = 7


def _compute_from_history(hist):
    """Return indicators from an ascending daily adjusted OHLCV frame."""
    result = compute_entry_alignment_from_history(hist)
    if not result:
        return None
    # Historical audit fields are useful to replay but are not catalog metrics.
    filtered = {key: value for key, value in result.items()
                if not key.startswith("entry_alignment_")}
    return filtered or None


def _compute(ticker: str):
    import yfinance as yf
    for _ in range(3):
        try:
            hist = yf.Ticker(ticker).history(period="5y", interval="1d", auto_adjust=True)
            return _compute_from_history(hist)
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
