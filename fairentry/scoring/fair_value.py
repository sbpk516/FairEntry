"""Multi-signal fair value (first-pass; v1 intrinsic/Lynch models plug in later).
Produces the intrinsic gap, upside, and a cheap/fair/expensive label the
valuation category and soft gates consume.
"""
from __future__ import annotations


def _num(m, k):
    v = m.get(k, {})
    v = v.get("value") if isinstance(v, dict) else v
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fair_value(metrics: dict, mos_pct: float = 15.0) -> dict:
    price = _num(metrics, "price")
    target = _num(metrics, "target_price")
    fwd_pe = _num(metrics, "fwd_pe")
    ps = _num(metrics, "ps_ratio")

    # Analyst target as the base fair-value anchor (conservative single method).
    if price and target and target > 0:
        fair_base = target
        upside = (fair_base / price - 1) * 100
    else:
        fair_base = price
        upside = 0.0
    fair_low = round(fair_base * 0.85, 2) if fair_base else None
    fair_high = round(fair_base * 1.15, 2) if fair_base else None
    buy_zone = round(fair_base * (1 - mos_pct / 100), 2) if fair_base else None

    # Label from forward valuation (sector-agnostic first pass).
    label = "unknown"
    if fwd_pe is not None:
        if fwd_pe <= 15 or upside >= 25:
            label = "cheap"
        elif fwd_pe >= 35 and upside < 12:
            label = "expensive"
        else:
            label = "fair"
    elif upside >= 25:
        label = "cheap"
    elif upside < 0:
        label = "expensive"
    else:
        label = "fair"

    return {
        "intrinsic_gap_pct": round(upside, 1),
        "upside_pct": round(upside, 1),
        "valuation_label": label,
        "fair_low": fair_low, "fair_base": round(fair_base, 2) if fair_base else None,
        "fair_high": fair_high, "buy_zone": buy_zone,
        "margin_of_safety_pct": mos_pct,
    }
