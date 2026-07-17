"""Demand & Momentum context.

This module is deliberately UI-only. It produces context panels for investors,
but does not feed the scoring engine, verdict bands, gates, or backtests.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..adapters.cache_lite import cache_get, cache_put

_CACHE_NS = "demand_momentum_v1"
_TTL_DAYS = 1
_PERIODS = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}
_MARKET = "SPY"
_SECTOR_ETF = {
    "Technology": "XLK",
    "Communication Services": "XLC",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Healthcare": "XLV",
    "Financial": "XLF",
    "Industrials": "XLI",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Basic Materials": "XLB",
}


def _num(metrics: dict, key: str):
    v = metrics.get(key, {})
    v = v.get("value") if isinstance(v, dict) else v
    return v if isinstance(v, (int, float)) else None


def _ret(closes: list[float], days: int):
    if not closes or len(closes) <= days:
        return None
    start, end = closes[-days - 1], closes[-1]
    if not start or start <= 0:
        return None
    return round((end / start - 1) * 100, 2)


def _series(frame, ticker: str, column: str) -> list[float]:
    try:
        data = frame[column]
        if hasattr(data, "columns"):
            data = data[ticker]
        vals = data.dropna().astype(float).tolist()
        return vals
    except Exception:
        return []


def _up_down_volume(closes: list[float], volumes: list[float], days: int = 20):
    if len(closes) < days + 1 or len(volumes) < days + 1:
        return None
    up = down = 0.0
    c = closes[-days - 1:]
    v = volumes[-days:]
    for i, vol in enumerate(v, start=1):
        if c[i] > c[i - 1]:
            up += vol
        elif c[i] < c[i - 1]:
            down += vol
    if down <= 0:
        return 3.0 if up > 0 else None
    return round(min(up / down, 3.0), 2)


def _volume_accumulation_label(up_down):
    if up_down is None:
        return "unknown"
    if up_down >= 1.4:
        return "accumulation"
    if up_down <= 0.75:
        return "distribution"
    return "neutral"


def _read(bench: dict, ticker: str, period: str):
    return (bench.get(ticker) or {}).get(period)


def _tone(rows: list[dict], volume_label: str):
    three = next((r for r in rows if r["period"] == "3m"), None)
    six = next((r for r in rows if r["period"] == "6m"), None)
    positives = 0
    for row in (three, six):
        if row and (row.get("market_alpha_pct") or 0) > 0 and (row.get("sector_alpha_pct") or 0) > 0:
            positives += 1
    if positives >= 2 and volume_label != "distribution":
        return "strong"
    if positives >= 1:
        return "improving"
    if volume_label == "distribution":
        return "weak"
    return "mixed"


def _summary(tone: str, rows: list[dict], volume_label: str):
    three = next((r for r in rows if r["period"] == "3m"), None) or {}
    ma = three.get("market_alpha_pct")
    sa = three.get("sector_alpha_pct")
    bits = []
    if ma is not None:
        bits.append(f"3m vs SPY {ma:+.1f}%")
    if sa is not None:
        bits.append(f"vs sector {sa:+.1f}%")
    if volume_label != "unknown":
        bits.append(volume_label)
    if not bits:
        return "Insufficient price/volume history for demand context."
    prefix = {
        "strong": "Demand looks strong",
        "improving": "Demand is improving",
        "weak": "Demand looks weak",
        "mixed": "Demand is mixed",
    }.get(tone, "Demand is mixed")
    return prefix + ": " + ", ".join(bits) + "."


def _download_history(tickers: list[str]):
    try:
        import yfinance as yf
        return yf.download(
            tickers=tickers,
            period="15mo",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
            group_by="column",
        )
    except Exception:
        return None


def _benchmark_returns(frame, tickers: set[str]):
    out = {}
    if frame is None:
        return out
    for ticker in tickers:
        closes = _series(frame, ticker, "Close")
        out[ticker] = {p: _ret(closes, d) for p, d in _PERIODS.items()}
    return out


def build_context(records: list[tuple[dict, dict]]) -> dict:
    """Return {ticker: demand_momentum_context}.

    ``records`` is [(score_record, metrics_for_ticker)]. Values are current UI
    context only; callers must not feed this back into scoring.
    """
    if not records:
        return {}
    tickers = sorted({rec["ticker"] for rec, _ in records})
    sector_etfs = {rec["sector"]: _SECTOR_ETF.get(rec["sector"]) for rec, _ in records}
    needed = sorted(set(tickers) | {_MARKET} | {v for v in sector_etfs.values() if v})
    cached = cache_get(_CACHE_NS, ",".join(needed), _TTL_DAYS)
    if cached is None:
        frame = _download_history(needed)
        bench = _benchmark_returns(frame, set(needed))
        series = {}
        if frame is not None:
            for ticker in tickers:
                series[ticker] = {
                    "close": _series(frame, ticker, "Close"),
                    "volume": _series(frame, ticker, "Volume"),
                }
        cached = {"bench": bench, "series": series}
        cache_put(_CACHE_NS, ",".join(needed), cached)

    bench = cached.get("bench", {})
    series = cached.get("series", {})
    out = {}
    for rec, metrics in records:
        ticker = rec["ticker"]
        sector = rec["sector"]
        sector_etf = _SECTOR_ETF.get(sector)
        closes = (series.get(ticker) or {}).get("close") or []
        volumes = (series.get(ticker) or {}).get("volume") or []
        rows = []
        for period in ("1m", "3m", "6m", "12m"):
            stock_ret = _ret(closes, _PERIODS[period])
            market_ret = _read(bench, _MARKET, period)
            sector_ret = _read(bench, sector_etf, period) if sector_etf else None
            rows.append({
                "period": period,
                "stock_return_pct": stock_ret,
                "market_return_pct": market_ret,
                "sector_return_pct": sector_ret,
                "market_alpha_pct": round(stock_ret - market_ret, 2)
                if stock_ret is not None and market_ret is not None else None,
                "sector_alpha_pct": round(stock_ret - sector_ret, 2)
                if stock_ret is not None and sector_ret is not None else None,
            })
        up_down = _up_down_volume(closes, volumes)
        vol_label = _volume_accumulation_label(up_down)
        rel_vol = _num(metrics, "rel_volume")
        tone = _tone(rows, vol_label)
        out[ticker] = {
            "context_only": True,
            "not_scored": True,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "market_benchmark": _MARKET,
            "sector_benchmark": sector_etf,
            "tone": tone,
            "summary": _summary(tone, rows, vol_label),
            "relative_strength": rows,
            "volume": {
                "relative_volume": rel_vol,
                "up_down_volume_20d": up_down,
                "accumulation_label": vol_label,
            },
            "note": "Context only - not used in FairEntry score, Buy/Watch/Avoid, or backtest verdicts.",
        }
    return out
