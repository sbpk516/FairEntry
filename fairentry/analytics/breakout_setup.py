"""Strategy-aware breakout evidence and the existing breakout label.

The module owns one trace used by both scoring and progressive disclosure.  It
does not create a second score: individual, independently explainable metrics
feed the existing Market Confirmation/Growth categories, while ``overall``
remains the existing ``early/building/confirmed/failed/unknown`` label.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..adapters.cache_lite import cache_get, cache_put
from .demand_momentum import _MARKET, _PERIODS, _SECTOR_ETF, _ret, _series

_CACHE_NS = "breakout_setup_v2"
_TTL_DAYS = 1


def _num(metrics: dict, key: str):
    v = metrics.get(key, {})
    v = v.get("value") if isinstance(v, dict) else v
    return v if isinstance(v, (int, float)) else None


def _metric_as_of(metrics: dict, keys: tuple[str, ...]):
    dates = [metrics.get(k, {}).get("fetched_at") for k in keys
             if isinstance(metrics.get(k), dict) and metrics.get(k, {}).get("fetched_at")]
    return max(dates) if dates else None


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


def _avg(values: list[float], days: int, *, exclude_latest: bool = False):
    vals = values[:-1] if exclude_latest else values
    if len(vals) < days:
        return None
    return sum(vals[-days:]) / days


def _clamp(value, lo=0.0, hi=100.0):
    return max(lo, min(hi, value))


def _status(score, *, contradicted=False):
    if score is None:
        return "unknown"
    if contradicted:
        return "contradicted"
    if score >= 70:
        return "satisfied"
    if score >= 45:
        return "partial"
    return "failed"


def _factor(fid, group, label, score, actual, expected, formula, evidence,
            *, contradicted=False, source="computed from stored fundamentals",
            scoring_metric=None, observed_at=None):
    return {
        "id": fid,
        "group": group,
        "label": label,
        "status": _status(score, contradicted=contradicted),
        "actual": actual,
        "expected": expected,
        "score_metric": None if score is None else round(_clamp(score)),
        "formula": formula,
        "evidence": evidence,
        "source": source,
        "calculation_version": "breakout_v2",
        "scoring_metric": scoring_metric,
        "observed_at": observed_at,
    }


def _trend_score(label: str):
    return {"improving": 90, "stabilizing": 72, "stable": 55, "mixed": 45,
            "deteriorating": 25, "worsening": 10, "unknown": None}.get(label)


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
            series[ticker] = {
                "close": _series(frame, ticker, "Close"),
                "volume": _series(frame, ticker, "Volume"),
                "as_of": (frame.index[-1].date().isoformat()
                          if getattr(frame, "index", None) is not None and len(frame.index) else None),
            }
    cache_put(_CACHE_NS, ",".join(needed), series)
    return series


def _market_factors(closes: list[float], volumes: list[float], sector_closes: list[float],
                    spy_closes: list[float], observed_at=None):
    sr = _support_resistance(closes)
    if sr["resistance"] and closes:
        above_resistance = round((closes[-1] / sr["resistance"] - 1) * 100, 2)
        price_score = _clamp(40 + above_resistance * 20)
        if sr["breakout"]:
            price_score = max(price_score, 85)
        elif sr["label"] == "failed":
            price_score = 0
    else:
        above_resistance = None
        price_score = None

    avg50 = _avg(volumes, 50, exclude_latest=True)
    volume_ratio = round(volumes[-1] / avg50, 2) if volumes and avg50 else None
    volume_score = None if volume_ratio is None else _clamp(
        20 if volume_ratio < .8 else 40 if volume_ratio < 1.2 else
        60 if volume_ratio < 1.5 else 85 if volume_ratio < 2 else 100)

    stock_ret = _ret(closes, _PERIODS["3m"])
    sector_ret = _ret(sector_closes, _PERIODS["3m"])
    spy_ret = _ret(spy_closes, _PERIODS["3m"])
    alphas = [stock_ret - x for x in (sector_ret, spy_ret)
              if stock_ret is not None and x is not None]
    relative_alpha = round(sum(alphas) / len(alphas), 2) if alphas else None
    relative_score = None if relative_alpha is None else _clamp(50 + relative_alpha * 3)

    ma50, ma200 = _ma(closes, 50), _ma(closes, 200)
    prior50 = (sum(closes[-70:-20]) / 50) if len(closes) >= 70 else None
    trend_checks = []
    if closes and ma50:
        trend_checks.append(closes[-1] > ma50)
    if closes and ma200:
        trend_checks.append(closes[-1] > ma200)
    if ma50 and ma200:
        trend_checks.append(ma50 > ma200)
    if ma50 and prior50:
        trend_checks.append(ma50 > prior50)
    trend_score = round(sum(trend_checks) / len(trend_checks) * 100) if trend_checks else None

    factors = [
        _factor("price_breakout", "market_confirmation", "Price above resistance", price_score,
                None if above_resistance is None else f"{above_resistance:+.2f}%",
                "close at least 2% above prior resistance",
                "(latest close / prior resistance - 1) × 100",
                f"Latest price {'cleared' if sr['breakout'] else 'did not clear'} the prior resistance level.",
                contradicted=sr["label"] == "failed", source="adjusted daily price history",
                scoring_metric="breakout_price_score", observed_at=observed_at),
        _factor("breakout_volume", "market_confirmation", "Breakout volume", volume_score,
                None if volume_ratio is None else f"{volume_ratio:.2f}×",
                "at least 1.50× the prior 50-day average",
                "latest volume / average of prior 50 sessions",
                "Higher volume shows whether broad participation supports the price move.",
                source="daily market volume history", scoring_metric="breakout_volume_score",
                observed_at=observed_at),
        _factor("relative_strength", "market_confirmation", "Relative strength", relative_score,
                None if relative_alpha is None else f"{relative_alpha:+.2f}%",
                "positive 3-month return versus sector and SPY",
                "average(stock return - sector return, stock return - SPY return)",
                "Positive relative return indicates that capital is favoring the stock.",
                source="adjusted daily price history", scoring_metric="relative_strength_score",
                observed_at=observed_at),
        _factor("trend_regime", "market_confirmation", "Trend regime", trend_score,
                None if trend_score is None else f"{sum(trend_checks)} of {len(trend_checks)} checks",
                "price and moving-average trend checks mostly positive",
                "share of: above 50DMA, above 200DMA, 50DMA>200DMA, rising 50DMA",
                "Several independent trend checks reduce reliance on a one-day price spike.",
                source="adjusted daily price history", scoring_metric="trend_regime_score",
                observed_at=observed_at),
    ]
    return sr, factors, {
        "breakout_price_score": None if price_score is None else round(price_score),
        "breakout_volume_score": None if volume_score is None else round(volume_score),
        "relative_strength_score": None if relative_score is None else round(relative_score),
        "trend_regime_score": trend_score,
    }, volume_ratio, relative_alpha


def _counts(factors):
    keys = ("satisfied", "partial", "failed", "contradicted", "unknown")
    return {key: sum(1 for f in factors if f["status"] == key) for key in keys} | {
        "total": len(factors),
    }


def _decision_label(strategy: str, fund: str, sr: dict, volume_ratio, relative_alpha,
                    trend_score, sector_label: str, business_support: bool):
    if sr["label"] == "failed" or (fund in {"worsening", "deteriorating"}
                                       and sector_label == "hostile"):
        return "failed"
    market_support = sum([
        relative_alpha is not None and relative_alpha > 0,
        trend_score is not None and trend_score >= 50,
        sector_label in {"supportive", "improving"},
    ])
    if (sr["breakout"] and volume_ratio is not None and volume_ratio >= 1.5
            and market_support >= 2 and business_support):
        return "confirmed"
    if (sr["label"] in {"breakout", "basing", "near resistance"}
            and market_support >= 1 and fund not in {"worsening", "deteriorating"}):
        return "building"
    if fund == "unknown" and sr["label"] == "unknown" and sector_label == "unknown":
        return "unknown"
    return "early"


def build_context(store, records: list[tuple[dict, dict]]) -> dict:
    """Return {ticker: breakout_setup_context}.

    ``records`` is [(record, metrics_for_ticker)]. A record only needs ticker,
    sector and ``_primary``; it may be called before or after scoring.
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
        # P/FCF can improve merely because price fell, so it is shown as context
        # below but deliberately excluded from business-repair confirmation.
        fund_label = _fundamental_label([rev_label, gross_label, oper_label])
        stock_series = price_series.get(ticker) or {}
        closes = stock_series.get("close") or []
        volumes = stock_series.get("volume") or []
        sector_closes = (price_series.get(sector_etf) or {}).get("close") or []
        spy_closes = (price_series.get(_MARKET) or {}).get("close") or []
        sr, market_factors, scoring_metrics, volume_ratio, relative_alpha = _market_factors(
            closes, volumes, sector_closes, spy_closes, stock_series.get("as_of"))
        short_current = _num(metrics, "short_float")
        short_hist = histories["short_float"].get(ticker, [])
        short_label = _short_label(short_current, short_hist)
        sector = _sector_trend(sector_etf, price_series)
        strategy = rec.get("_primary") or rec.get("strategy") or "deep_value"
        rev_now, eps_now = _num(metrics, "rev_growth_qoq"), _num(metrics, "eps_growth_next_y")
        margin_score = _trend_score(_fundamental_label([gross_label, oper_label]))
        if strategy == "quality_growth":
            business_support = bool(((rev_now is not None and rev_now >= 10)
                                     or (eps_now is not None and eps_now >= 15))
                                    and fund_label not in {"worsening", "deteriorating"})
            business_expected = "durable revenue/EPS growth with no fundamental deterioration"
            business_actual = f"revenue {rev_now if rev_now is not None else 'n/a'}%, EPS {eps_now if eps_now is not None else 'n/a'}%, trend {fund_label}"
            business_score = 85 if business_support else 20 if fund_label in {"worsening", "deteriorating"} else 45
            business_label = "Growth durability"
        else:
            business_support = fund_label in {"improving", "stabilizing"}
            business_expected = "at least two revenue/margin trends improving, with no deterioration"
            business_actual = f"fundamental trend {fund_label}"
            business_score = _trend_score(fund_label)
            business_label = "Recovery evidence"
        business_factor = _factor("strategy_business_support", "business_support", business_label,
                                  business_score, business_actual, business_expected,
                                  "strategy-specific revenue, EPS and margin trend test",
                                  "Deep value requires stabilization; growth requires durable current growth.",
                                  contradicted=fund_label in {"worsening", "deteriorating"},
                                  observed_at=_metric_as_of(metrics, ("rev_growth_qoq", "eps_growth_next_y",
                                                                      "gross_margin", "oper_margin")))
        margin_factor = _factor("margin_direction", "business_support", "Margin direction",
                                margin_score,
                                f"gross margin {gross_label}, operating margin {oper_label}",
                                "stable or improving gross and operating margins",
                                "trend of stored gross-margin and operating-margin observations",
                                "Margin direction supports the existing Growth category without using price momentum.",
                                contradicted=gross_label == "worsening" and oper_label == "worsening",
                                scoring_metric="margin_trend_score",
                                observed_at=_metric_as_of(metrics, ("gross_margin", "oper_margin")))
        short_score = {"easing": 80, "low": 70, "moderate": 55, "crowded": 45,
                       "rising": 20, "unknown": None}.get(short_label)
        short_factor = _factor("short_pressure", "human_and_flow", "Short-interest pressure",
                               short_score, short_label, "easing or manageable short interest",
                               "current short float plus its stored trend",
                               "Short pressure is supporting context, not a confirmation requirement.",
                               contradicted=short_label == "rising", source="Finviz plus stored history",
                               observed_at=_metric_as_of(metrics, ("short_float",)))
        factors = market_factors + [business_factor, margin_factor, short_factor]
        scoring_metrics["margin_trend_score"] = margin_score
        overall = _decision_label(strategy, fund_label, sr, volume_ratio, relative_alpha,
                                  scoring_metrics["trend_regime_score"], sector["label"],
                                  business_support)
        out[ticker] = {
            "context_only": False,
            "not_scored": False,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "strategy": strategy,
            "overall": overall,
            "summary": _summary(overall, fund_label, sr["label"], short_label, sector["label"]),
            "counts": _counts(factors),
            "factors": factors,
            "scoring_metrics": scoring_metrics,
            "fundamental_stabilization": {
                "label": fund_label,
                "revenue_trend": rev_label,
                "gross_margin_trend": gross_label,
                "operating_margin_trend": oper_label,
                "fcf_proxy_trend": pfcf_label,
                "fcf_proxy_note": "P/FCF trend is displayed only as context and is excluded from confirmation because price changes can move the ratio without any cash-flow improvement.",
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
            "note": "The listed quantitative factor metrics feed existing Growth/Market Confirmation categories. The label is a rule outcome, not a second score.",
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
