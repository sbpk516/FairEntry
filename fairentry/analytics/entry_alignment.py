"""Shared price-history calculations for entry rules and SMA-zone discovery."""
from __future__ import annotations


def compute_entry_alignment_from_history(hist, *, asof=None):
    """Calculate monthly averages and weekly confirmation without future rows.

    ``hist`` must be an ascending or unsorted daily frame indexed by date with
    ``Close`` and, optionally, ``Volume`` columns.  When ``asof`` is supplied,
    rows after that timestamp are removed before any resampling.  Consequently
    an in-progress week or month contains only observations known at ``asof``.
    """
    import pandas as pd

    if hist is None or hist.empty or "Close" not in hist:
        return None
    frame = hist[[c for c in ("Close", "Volume") if c in hist]].copy()
    frame.index = pd.to_datetime(frame.index)
    if asof is not None:
        cutoff = pd.Timestamp(asof)
        if frame.index.tz is not None and cutoff.tzinfo is None:
            cutoff = cutoff.tz_localize(frame.index.tz)
        elif frame.index.tz is None and cutoff.tzinfo is not None:
            cutoff = cutoff.tz_localize(None)
        frame = frame.loc[frame.index <= cutoff]
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
    out = {
        "entry_alignment_price": latest,
        "entry_alignment_price_date": frame.index[-1].date().isoformat(),
        "entry_alignment_history_days": len(frame),
        "entry_alignment_history_months": len(monthly),
        "entry_alignment_history_weeks": len(weekly),
    }

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
        ema = float(monthly.ewm(
            span=span, adjust=False, min_periods=span
        ).mean().iloc[-1])
        if ema > 0:
            out[key] = round(ema, 4)
            out[f"dist_{span}month_ema_pct"] = round(
                (latest / ema - 1) * 100, 4
            )

    for span, key in ((9, "sma_9month"), (20, "sma_20month")):
        if len(monthly) < span:
            continue
        sma = float(monthly.tail(span).mean())
        if sma > 0:
            out[key] = round(sma, 4)
            out[f"dist_{span}month_sma_pct"] = round(
                (latest / sma - 1) * 100, 4
            )

    if len(weekly) >= 20:
        direction = weekly["Close"].diff().apply(
            lambda change: 1.0 if change > 0 else (-1.0 if change < 0 else 0.0)
        )
        obv = (direction * weekly["Volume"]).fillna(0.0).cumsum()
        obv_ema = obv.ewm(span=20, adjust=False, min_periods=20).mean()
        if pd.notna(obv_ema.iloc[-1]):
            out["obv_above_20week_ema"] = bool(obv.iloc[-1] > obv_ema.iloc[-1])
    return out
