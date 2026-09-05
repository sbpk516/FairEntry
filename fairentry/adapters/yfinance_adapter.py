"""Keyless price-history indicators for shortlisted names.

The daily adjusted history is resampled without future observations to produce
the 9/20-month EMAs and weekly OBV confirmation used by the Buy-entry rule.
"""
from __future__ import annotations

from .cache_lite import cache_get, cache_put

OWNS = "yfinance"
_CACHE_NS = "yf_entry_alignment_v1"
_TTL_DAYS = 7


def _compute_from_history(hist):
    """Return indicators from an ascending daily adjusted OHLCV frame."""
    import pandas as pd

    if hist is None or hist.empty or "Close" not in hist:
        return None
    frame = hist[[c for c in ("Close", "Volume") if c in hist]].copy()
    frame["Close"] = pd.to_numeric(frame["Close"], errors="coerce")
    if "Volume" not in frame:
        frame["Volume"] = 0.0
    frame["Volume"] = pd.to_numeric(frame["Volume"], errors="coerce").fillna(0.0)
    frame = frame.dropna(subset=["Close"]).sort_index()
    if frame.empty or float(frame["Close"].iloc[-1]) <= 0:
        return None

    latest = float(frame["Close"].iloc[-1])
    monthly = frame["Close"].resample("ME").last().dropna()
    weekly = frame.resample("W-FRI").agg({"Close": "last", "Volume": "sum"})
    weekly = weekly.dropna(subset=["Close"])
    out = {}

    if len(weekly) >= 200:
        sma = float(weekly["Close"].tail(200).mean())
        if sma > 0:
            out.update({
                "sma_200week": round(sma, 4),
                "dist_200wma_pct": round((latest / sma - 1) * 100, 4),
            })

    for span, key in ((9, "ema_9month"), (20, "ema_20month")):
        if len(monthly) < span:
            continue
        ema = float(monthly.ewm(span=span, adjust=False, min_periods=span).mean().iloc[-1])
        if ema > 0:
            out[key] = round(ema, 4)
            out[f"dist_{span}month_ema_pct"] = round((latest / ema - 1) * 100, 4)

    if len(weekly) >= 20:
        direction = weekly["Close"].diff().apply(
            lambda change: 1.0 if change > 0 else (-1.0 if change < 0 else 0.0)
        )
        obv = (direction * weekly["Volume"]).fillna(0.0).cumsum()
        obv_ema = obv.ewm(span=20, adjust=False, min_periods=20).mean()
        if pd.notna(obv_ema.iloc[-1]):
            out["obv_above_20week_ema"] = bool(obv.iloc[-1] > obv_ema.iloc[-1])
    return out or None


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
