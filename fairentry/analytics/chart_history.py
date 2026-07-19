"""Chart history export for the FairEntry web UI.

The chart is a visual aid. It reuses the same price series and breakout fields
that power the existing support/resistance and breakout explanation.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
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
            period="5y",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
            group_by="column",
        )
    except Exception:
        return None


def _daily_bars(frame, ticker: str, limit: int = 1300) -> list[dict]:
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


def weekly_bars(daily: list[dict], limit: int = 260) -> list[dict]:
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
    return weeks[-limit:]


def _latest_average(values: list[float], days: int):
    vals = [float(v) for v in values if isinstance(v, (int, float))]
    if len(vals) < days:
        return None
    return round(sum(vals[-days:]) / days, 2)


def _chart_payload(stock: dict, daily: list[dict]) -> dict:
    breakout = stock.get("breakout_setup") or {}
    sr = breakout.get("support_resistance") or {}
    closes = [bar["c"] for bar in daily if isinstance(bar.get("c"), (int, float))]
    latest = closes[-1] if closes else stock.get("price")
    return {
        "ticker": stock.get("ticker"),
        "company": stock.get("company"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Yahoo Finance adjusted daily OHLCV via yfinance",
        "note": "Visual aid only. Breakout labels come from FairEntry's scoring/export logic.",
        "daily": daily[-260:],
        "weekly": weekly_bars(daily),
        "levels": {
            "price": _round_num(latest),
            "support": _round_num(sr.get("support")),
            "resistance": _round_num(sr.get("resistance")),
            "sma50": _latest_average(closes, 50),
            "sma200": _latest_average(closes, 200),
            "fair_value": _round_num((stock.get("valuation") or {}).get("base")),
            "buy_below": _round_num((stock.get("valuation") or {}).get("buy_below")),
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
