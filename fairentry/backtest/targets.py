"""Transparent, point-in-time target models used by the evidence backtest."""
from __future__ import annotations

import statistics


def _bounded(price, target, minimum, maximum):
    if not price or not target or target <= 0:
        return None
    upside = (target / price - 1) * 100
    if upside < minimum:
        return None
    return round(price * (1 + min(upside, maximum) / 100), 2)


def targets_for(record: dict, metrics: dict, strategy) -> dict:
    """Return independent targets. Analyst targets are intentionally excluded
    from the primary fundamental model because seeded history cannot prove they
    were known at entry."""
    price = record.get("price")
    valuation = record.get("valuation") or {}
    methods = [m for m in valuation.get("methods", []) if m.get("key") != "analyst"]
    fundamental_raw = statistics.median([m["fair"] for m in methods]) if methods else None
    fundamental = _bounded(price, fundamental_raw, strategy.minimum_upside_pct,
                           strategy.maximum_upside_pct)

    # A deliberately simple, reproducible technical objective: the stronger of
    # 10% continuation and a retest/recovery to the 200-week mean, capped at 35%.
    sma = (metrics.get("sma200") or {}).get("value")
    technical_raw = max(price * 1.10, sma) if price and isinstance(sma, (int, float)) else (price * 1.10 if price else None)
    technical = _bounded(price, technical_raw, strategy.minimum_upside_pct, 35)
    components = [x for x in (fundamental, technical) if x]
    blended = _bounded(price, statistics.median(components) if components else None,
                       strategy.minimum_upside_pct, strategy.maximum_upside_pct)

    out = {}
    for model, value, basis in (
        ("fundamental", fundamental, "Median of non-analyst fair-value methods available at entry"),
        ("technical", technical, "10% continuation or recovery to 200-week mean, capped at 35%"),
        ("blended", blended, "Median of available fundamental and technical targets"),
    ):
        out[model] = {"price": value, "upside_pct": round((value / price - 1) * 100, 2) if value and price else None,
                      "available": value is not None, "basis": basis,
                      "expiry_days": strategy.target_expiry_days}
    return out

