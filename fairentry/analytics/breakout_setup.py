"""Deep value breakout setup context.

This module is deliberately context-only. It estimates whether a cheap stock is
starting to set up for a recovery/breakout, but it does not feed scoring,
verdicts, gates, or backtests until those labels have been validated.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..adapters.cache_lite import cache_get, cache_put
from .demand_momentum import _MARKET, _PERIODS, _SECTOR_ETF, _ret, _series

_CACHE_NS = "breakout_setup_v1"
_TTL_DAYS = 1


def _num(metrics: dict, key: str):
    v = metrics.get(key, {})
    v = v.get("value") if isinstance(v, dict) else v
    return v if isinstance(v, (int, float)) else None


def _download_history(tickers: list[str]):
    try:
        import yfinance as yf
        return yf.download(
            tickers=tickers,
            period="18mo",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
            group_by="column",
        )
    except Exception:
        return None


def _pct(a, b):
    if a is None or b in (None, 0):
        return None
    return round((a / b - 1) * 100, 2)


def _ma(values: list[float], days: int):
    if len(values) < days:
        return None
    return sum(values[-days:]) / days


def _trend_label(values: list[float], *, lower_is_better: bool = False) -> str:
    vals = [v for v in values if isinstance(v, (int, float))]
    if len(vals) < 2:
        return "unknown"
    first, last = vals[0], vals[-1]
    delta = last - first
    if lower_is_better:
        delta = -delta
    if delta >= 2:
        return "improving"
    if delta <= -2:
        return "worsening"
    return "stable"


def _fundamental_label(parts: list[str]) -> str:
    known = [p for p in parts if p != "unknown"]
    if not known:
        return "unknown"
    improving = known.count("improving")
    worsening = known.count("worsening")
    if improving >= 2 and worsening == 0:
        return "improving"
    if worsening >= 2 and improving == 0:
        return "worsening"
    if improving > worsening:
        return "stabilizing"
    if worsening > improving:
        return "deteriorating"
    return "mixed"


def _support_touches(closes: list[float], support: float | None, tolerance_pct: float = 3.0) -> int:
    if not closes or support is None or support <= 0:
        return 0
    touches = 0
    last_idx = -10
    for i, close in enumerate(closes):
        if abs(close / support - 1) * 100 <= tolerance_pct and i - last_idx >= 10:
            touches += 1
            last_idx = i
    return touches


def _support_resistance(closes: list[float]) -> dict:
    if len(closes) < 80:
        return {
            "label": "unknown",
            "distance_to_52w_low_pct": None,
            "distance_to_resistance_pct": None,
            "support_touches": 0,
            "breakout": False,
            "support": None,
            "resistance": None,
        }
    latest = closes[-1]
    year = closes[-252:] if len(closes) >= 252 else closes
    recent = closes[-126:] if len(closes) >= 126 else closes
    prior = closes[-68:-5] if len(closes) >= 68 else closes[:-5]
    low_52w = min(year)
    support = min(recent)
    resistance = max(prior) if prior else max(recent)
    dist_low = _pct(latest, low_52w)
    dist_res = _pct(resistance, latest)
    touches = _support_touches(recent, support)
    breakout = bool(resistance and latest >= resistance * 1.02)
    failed = bool(support and latest <= support * 0.97)
    if breakout:
        label = "breakout"
    elif failed:
        label = "failed"
    elif touches >= 2 and dist_low is not None and dist_low <= 20:
        label = "basing"
    elif dist_low is not None and dist_low <= 12:
        label = "near support"
    elif dist_res is not None and dist_res <= 8:
        label = "near resistance"
    else:
        label = "neutral"
    return {
        "label": label,
        "distance_to_52w_low_pct": dist_low,
        "distance_to_resistance_pct": dist_res,
        "support_touches": touches,
        "breakout": breakout,
        "support": round(support, 2) if support else None,
        "resistance": round(resistance, 2) if resistance else None,
    }


def _short_label(current, history: list[float]) -> str:
    if current is None:
        return "unknown"
    vals = [v for v in history if isinstance(v, (int, float))]
    if len(vals) >= 2:
        delta = vals[-1] - vals[0]
        if delta <= -1:
            return "easing"
        if delta >= 1:
            return "rising"
    if current >= 20:
        return "crowded"
    if current <= 5:
        return "low"
    return "moderate"


def _sector_trend(sector_etf: str | None, bench: dict) -> dict:
    closes = (bench.get(sector_etf) or {}).get("close") if sector_etf else []
    spy = (bench.get(_MARKET) or {}).get("close") or []
    if not closes:
        return {
            "label": "unknown",
            "sector_etf": sector_etf,
            "above_50d": None,
            "above_200d": None,
            "sector_vs_spy_3m_pct": None,
        }
    latest = closes[-1]
    ma50 = _ma(closes, 50)
    ma200 = _ma(closes, 200)
    above50 = latest > ma50 if ma50 else None
    above200 = latest > ma200 if ma200 else None
    sector_3m = _ret(closes, _PERIODS["3m"])
    spy_3m = _ret(spy, _PERIODS["3m"])
    alpha = round(sector_3m - spy_3m, 2) if sector_3m is not None and spy_3m is not None else None
    if above50 and above200 and (alpha is None or alpha >= 0):
        label = "supportive"
    elif above200 is False and (alpha is None or alpha < 0):
        label = "hostile"
    elif alpha is not None and alpha > 0:
        label = "improving"
    else:
        label = "neutral"
    return {
        "label": label,
        "sector_etf": sector_etf,
        "above_50d": above50,
        "above_200d": above200,
        "sector_vs_spy_3m_pct": alpha,
    }


def _overall(fund: str, sr: str, short: str, sector: str) -> str:
    if sr == "failed" or (fund in {"worsening", "deteriorating"} and sector == "hostile"):
        return "failed"
    if sr == "breakout" and fund in {"improving", "stabilizing", "mixed"}:
        return "confirmed"
    if sr in {"basing", "near resistance"} and fund in {"improving", "stabilizing"} and sector != "hostile":
        return "building"
    if short in {"easing", "crowded"} and sr in {"basing", "near resistance", "breakout"}:
        return "building"
    if fund == "unknown" and sr == "unknown" and sector == "unknown":
        return "unknown"
    return "early"


def _history(store, field_id: str) -> dict[str, list[float]]:
    rows = store.con.execute(
        "SELECT h.ticker, substr(h.fetched_at,1,10) d, h.value_num v "
        "FROM metrics_history h "
        "JOIN ("
        "  SELECT ticker, substr(fetched_at,1,10) d, max(fetched_at) fetched_at "
        "  FROM metrics_history "
        "  WHERE field_id=? AND value_num IS NOT NULL "
        "  GROUP BY ticker, d"
        ") latest ON latest.ticker=h.ticker "
        "  AND latest.d=substr(h.fetched_at,1,10) "
        "  AND latest.fetched_at=h.fetched_at "
        "WHERE h.field_id=? AND h.value_num IS NOT NULL "
        "ORDER BY h.ticker, d",
        (field_id, field_id),
    )
    out: dict[str, list[float]] = {}
    for r in rows:
        out.setdefault(r["ticker"], []).append(r["v"])
    return out


def _history_bundle(store) -> dict[str, dict[str, list[float]]]:
    fields = ("rev_growth_qoq", "gross_margin", "oper_margin", "pfcf_ratio", "short_float")
    return {field: _history(store, field) for field in fields}


def _price_series(records: list[tuple[dict, dict]]) -> dict:
    tickers = sorted({rec["ticker"] for rec, _ in records})
    sector_etfs = {_SECTOR_ETF.get(rec["sector"]) for rec, _ in records}
    needed = sorted(set(tickers) | {_MARKET} | {v for v in sector_etfs if v})
    cached = cache_get(_CACHE_NS, ",".join(needed), _TTL_DAYS)
    if cached is not None:
        return cached
    frame = _download_history(needed)
    series = {}
    if frame is not None:
        for ticker in needed:
            series[ticker] = {"close": _series(frame, ticker, "Close")}
    cache_put(_CACHE_NS, ",".join(needed), series)
    return series


def build_context(store, records: list[tuple[dict, dict]]) -> dict:
    """Return {ticker: breakout_setup_context}.

    ``records`` is [(score_record, metrics_for_ticker)]. Output is context only;
    callers must keep it out of score traces and verdict computation.
    """
    if not records:
        return {}
    histories = _history_bundle(store)
    price_series = _price_series(records)
    out = {}
    for rec, metrics in records:
        ticker = rec["ticker"]
        sector_etf = _SECTOR_ETF.get(rec["sector"])
        rev_label = _trend_label(histories["rev_growth_qoq"].get(ticker, []))
        gross_label = _trend_label(histories["gross_margin"].get(ticker, []))
        oper_label = _trend_label(histories["oper_margin"].get(ticker, []))
        pfcf_label = _trend_label(histories["pfcf_ratio"].get(ticker, []), lower_is_better=True)
        fund_label = _fundamental_label([rev_label, gross_label, oper_label, pfcf_label])
        closes = (price_series.get(ticker) or {}).get("close") or []
        sr = _support_resistance(closes)
        short_current = _num(metrics, "short_float")
        short_hist = histories["short_float"].get(ticker, [])
        short_label = _short_label(short_current, short_hist)
        sector = _sector_trend(sector_etf, price_series)
        overall = _overall(fund_label, sr["label"], short_label, sector["label"])
        out[ticker] = {
            "context_only": True,
            "not_scored": True,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "overall": overall,
            "summary": _summary(overall, fund_label, sr["label"], short_label, sector["label"]),
            "fundamental_stabilization": {
                "label": fund_label,
                "revenue_trend": rev_label,
                "gross_margin_trend": gross_label,
                "operating_margin_trend": oper_label,
                "fcf_proxy_trend": pfcf_label,
                "fcf_proxy_note": "Uses P/FCF trend as a proxy because raw historical FCF is not consistently stored.",
            },
            "support_resistance": sr,
            "short_pressure": {
                "label": short_label,
                "short_float": short_current,
                "short_float_trend": _trend_label(short_hist, lower_is_better=True),
                "days_to_cover": None,
                "days_to_cover_note": "Not available from the current free-data pipeline.",
            },
            "sector_trend": sector,
            "note": "Context only - not used in FairEntry score, Buy/Watch/Avoid, or backtest verdicts.",
        }
    return out


def _summary(overall: str, fund: str, sr: str, short: str, sector: str) -> str:
    bits = [
        f"fundamentals {fund}",
        f"price setup {sr}",
        f"short pressure {short}",
        f"sector {sector}",
    ]
    prefix = {
        "confirmed": "Breakout setup is confirmed",
        "building": "Breakout setup is building",
        "early": "Breakout setup is still early",
        "failed": "Breakout setup is failing",
        "unknown": "Breakout setup has insufficient data",
    }.get(overall, "Breakout setup is still early")
    return prefix + ": " + ", ".join(bits) + "."
