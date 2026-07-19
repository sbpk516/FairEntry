"""Chart history export for the FairEntry web UI.

The chart is a visual aid. It reuses the same price series and breakout fields
that power the existing support/resistance and breakout explanation.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from ..adapters.cache_lite import cache_get, cache_put

_CACHE_NS = "chart_history_v2"
_TTL_DAYS = 1


def chart_filename(ticker: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(ticker).upper())
    return f"{safe or 'UNKNOWN'}.json"


def _round_num(value, digits=2):
    try:
        return round(float(value), digits)
    except Exception:
        return None


def _column(frame, ticker: str, column: str):
    try:
        data = frame[column]
        if hasattr(data, "columns"):
            data = data[ticker]
        return data
    except Exception:
        return None


def _download_history(tickers: list[str]):
    try:
        import yfinance as yf

        return yf.download(
            tickers=tickers,
            period="10y",
            interval="1d",
            auto_adjust=True,
            repair=True,
            progress=False,
            threads=True,
            group_by="column",
        )
    except Exception:
        return None


def _daily_bars(frame, ticker: str, limit: int = 2600) -> list[dict]:
    if frame is None:
        return []
    cols = {name: _column(frame, ticker, name) for name in ("Open", "High", "Low", "Close", "Volume")}
    if any(value is None for value in cols.values()):
        return []
    try:
        import pandas as pd

        df = pd.concat(cols.values(), axis=1)
        df.columns = ["open", "high", "low", "close", "volume"]
        df = df.dropna(subset=["close"]).tail(limit)
    except Exception:
        return []

    bars = []
    for idx, row in df.iterrows():
        close = _round_num(row.get("close"))
        if close is None:
            continue
        bars.append({
            "d": idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10],
            "o": _round_num(row.get("open")) or close,
            "h": _round_num(row.get("high")) or close,
            "l": _round_num(row.get("low")) or close,
            "c": close,
            "v": int(float(row.get("volume") or 0)),
        })
    return bars


def weekly_bars(daily: list[dict], limit: int = 320, *, today: date | None = None) -> list[dict]:
    """Aggregate daily bars into completed exchange weeks.

    During Monday-Friday, the current ISO week is excluded because its close is
    still changing. On weekends the just-finished week is retained.
    """
    weeks: list[dict] = []
    current = None
    current_key = None
    for bar in daily:
        try:
            d = datetime.fromisoformat(bar["d"]).date()
            key = d.isocalendar()[:2]
        except Exception:
            key = bar["d"][:7]
        if key != current_key:
            if current:
                weeks.append(current)
            current_key = key
            current = {
                "d": bar["d"],
                "o": bar["o"],
                "h": bar["h"],
                "l": bar["l"],
                "c": bar["c"],
                "v": bar.get("v") or 0,
            }
            continue
        current["d"] = bar["d"]
        current["h"] = max(current["h"], bar["h"])
        current["l"] = min(current["l"], bar["l"])
        current["c"] = bar["c"]
        current["v"] += bar.get("v") or 0
    if current:
        weeks.append(current)
    today = today or datetime.now(timezone.utc).date()
    if weeks and today.weekday() < 5:
        try:
            last_date = datetime.fromisoformat(weeks[-1]["d"]).date()
            if last_date.isocalendar()[:2] == today.isocalendar()[:2]:
                weeks.pop()
        except Exception:
            pass
    return weeks[-limit:]


def _latest_average(values: list[float], days: int):
    vals = [float(v) for v in values if isinstance(v, (int, float))]
    if len(vals) < days:
        return None
    return round(sum(vals[-days:]) / days, 2)


def _position(latest, average, tolerance_pct: float) -> dict:
    if not isinstance(latest, (int, float)) or not isinstance(average, (int, float)) or average <= 0:
        return {"label": "Unknown", "status": "unknown", "distance_pct": None}
    distance = round((latest / average - 1) * 100, 2)
    if distance > tolerance_pct:
        return {"label": "Above", "status": "satisfied", "distance_pct": distance}
    if distance < -tolerance_pct:
        return {"label": "Below", "status": "failed", "distance_pct": distance}
    return {"label": "Near", "status": "partial", "distance_pct": distance}


def crossover_signal(values: list[float], fast_period: int, slow_period: int,
                     recent_periods: int = 4, period_name: str = "week") -> dict:
    """Describe alignment and whether a crossover happened recently.

    This is evidence, not a standalone breakout decision.
    """
    vals = [float(v) for v in values if isinstance(v, (int, float))]
    if len(vals) < slow_period:
        return {"label": "Insufficient history", "status": "unknown",
                "fast_period": fast_period, "slow_period": slow_period}
    start = max(slow_period, len(vals) - recent_periods - 1)
    observations = []
    for end in range(start, len(vals) + 1):
        fast = sum(vals[end-fast_period:end]) / fast_period
        slow = sum(vals[end-slow_period:end]) / slow_period
        observations.append((end, fast, slow, fast - slow))
    _, fast, slow, gap = observations[-1]
    crossed = None
    weeks_ago = None
    for index in range(len(observations) - 1, 0, -1):
        prior_gap, current_gap = observations[index - 1][3], observations[index][3]
        if prior_gap <= 0 < current_gap:
            crossed, weeks_ago = "bullish", len(observations) - 1 - index
            break
        if prior_gap >= 0 > current_gap:
            crossed, weeks_ago = "bearish", len(observations) - 1 - index
            break
    if crossed == "bullish":
        label, status = "Bullish cross", "satisfied"
    elif crossed == "bearish":
        label, status = "Bearish cross", "failed"
    elif gap > 0:
        label, status = "Bullish alignment", "satisfied"
    else:
        label, status = "Bearish alignment", "failed"
    return {
        "label": label, "status": status,
        "fast_period": fast_period, "slow_period": slow_period,
        "fast": round(fast, 2), "slow": round(slow, 2),
        "gap_pct": round(gap / slow * 100, 2) if slow else None,
        "crossed_within_periods": weeks_ago,
        "period_name": period_name,
        "note": "Supporting trend evidence only; not a standalone breakout.",
    }


def _chart_payload(stock: dict, daily: list[dict]) -> dict:
    breakout = stock.get("breakout_setup") or {}
    sr = breakout.get("support_resistance") or {}
    closes = [bar["c"] for bar in daily if isinstance(bar.get("c"), (int, float))]
    weekly = weekly_bars(daily)
    weekly_closes = [bar["c"] for bar in weekly if isinstance(bar.get("c"), (int, float))]
    latest = closes[-1] if closes else stock.get("price")
    sma50w = _latest_average(weekly_closes, 50)
    sma200w = _latest_average(weekly_closes, 200)
    return {
        "ticker": stock.get("ticker"),
        "company": stock.get("company"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Yahoo Finance adjusted daily OHLCV via yfinance",
        "note": "Moving averages use completed weeks. Crossovers are supporting evidence, not standalone breakout decisions.",
        "moving_average_methodology": {
            "weekly_price": "final adjusted close from each completed trading week",
            "current_incomplete_week_excluded": True,
            "sma50week": "arithmetic mean of the latest 50 completed weekly closes",
            "sma200week": "arithmetic mean of the latest 200 completed weekly closes",
            "position_tolerance": "50WMA +/-2%; 200WMA +/-3%",
            "weekly_as_of": weekly[-1]["d"] if weekly else None,
        },
        "daily": daily[-260:],
        "weekly": weekly,
        "levels": {
            "price": _round_num(latest),
            "support": _round_num(sr.get("support")),
            "resistance": _round_num(sr.get("resistance")),
            "sma50": _latest_average(closes, 50),
            "sma200": _latest_average(closes, 200),
            "sma50week": sma50w,
            "sma200week": sma200w,
            "sma50week_position": _position(latest, sma50w, 2),
            "sma200week_position": _position(latest, sma200w, 3),
            "fair_value": _round_num((stock.get("valuation") or {}).get("base")),
            "buy_below": _round_num((stock.get("valuation") or {}).get("buy_below")),
        },
        "weekly_signals": {
            "intermediate_cross": crossover_signal(weekly_closes, 10, 40),
            "long_term_reclaim": crossover_signal(weekly_closes, 10, 200),
            "secular_alignment": crossover_signal(weekly_closes, 50, 200),
        },
        "daily_signals": {
            "golden_cross": crossover_signal(closes, 50, 200, 20, "session"),
        },
        "breakout_setup": breakout,
    }


def write_chart_files(stocks: list[dict], out_dir: Path) -> dict[str, str]:
    tickers = sorted({str(stock.get("ticker", "")).upper() for stock in stocks if stock.get("ticker")})
    if not tickers:
        return {}
    key = ",".join(tickers)
    cached = cache_get(_CACHE_NS, key, _TTL_DAYS)
    if cached is None:
        frame = _download_history(tickers)
        cached = {ticker: _daily_bars(frame, ticker) for ticker in tickers}
        cache_put(_CACHE_NS, key, cached)

    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for stock in stocks:
        ticker = str(stock.get("ticker", "")).upper()
        daily = cached.get(ticker) or []
        if not daily:
            continue
        filename = chart_filename(ticker)
        payload = _chart_payload(stock, daily)
        (out_dir / filename).write_text(json.dumps(payload, indent=1), encoding="utf-8")
        written[ticker] = f"data/charts/{filename}"
    return written
