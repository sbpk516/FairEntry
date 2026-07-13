"""Multi-method fair value (Phase A). A single fair price is fragile, so we
estimate it several ways and blend — each method is transparent (drill-down).

Methods (each yields a fair price + upside, or is skipped if data is missing):
  - Analyst target        (sanity anchor)
  - Growth-justified/Lynch (fair P/E ~= growth rate; PEG≈1)
  - Peer P/E              (sector-median forward P/E)
  - Peer P/S             (sector-median price/sales)
  - FCF value            (growth-adjusted fair P/FCF)
  - Asset / book         (sector-median, or ~2x, P/B)

fair_base = median of the methods; fair_low/high = the spread. Buy zone applies
the user's margin of safety. valuation_label reflects how many methods agree.
"""
from __future__ import annotations

import statistics


def _num(m, k):
    v = m.get(k, {})
    v = v.get("value") if isinstance(v, dict) else v
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fair_value(metrics: dict, mos_pct: float = 15.0, sector_med: dict | None = None) -> dict:
    sector_med = sector_med or {}
    price = _num(metrics, "price")
    unknown = {"intrinsic_gap_pct": 0.0, "upside_pct": 0.0, "valuation_label": "unknown",
               "fair_low": price, "fair_base": price, "fair_high": price, "buy_zone": None,
               "methods": [], "method_count": 0, "margin_of_safety_pct": mos_pct}
    if not price or price <= 0:
        return unknown

    fpe = _num(metrics, "fwd_pe")
    g = _num(metrics, "eps_growth_next_y")
    ps = _num(metrics, "ps_ratio")
    pb = _num(metrics, "pb_ratio")
    pfcf = _num(metrics, "pfcf_ratio")

    methods = []

    def add(name, fair):
        if fair and fair > 0:
            methods.append({"name": name, "fair": round(fair, 2),
                            "upside": round((fair / price - 1) * 100, 1)})

    add("Analyst target", _num(metrics, "target_price"))
    if g and fpe and fpe > 0 and g > 0:                       # Lynch: fair P/E ~ growth
        add("Growth-justified (Lynch)", price * max(8.0, min(g, 40.0)) / fpe)
    if fpe and fpe > 0 and (sector_med.get("fwd_pe") or 0) > 0:
        add("Peer forward P/E", price * sector_med["fwd_pe"] / fpe)
    if ps and ps > 0 and (sector_med.get("ps_ratio") or 0) > 0:
        add("Peer P/S", price * sector_med["ps_ratio"] / ps)
    if pfcf and pfcf > 0:                                     # growth lifts a fair P/FCF
        add("FCF value", price * (18.0 + (min(g, 30.0) / 2 if g and g > 0 else 0)) / pfcf)
    if pb and pb > 0:
        fair_pb = sector_med.get("pb_ratio") if (sector_med.get("pb_ratio") or 0) > 0 else 2.0
        add("Asset / book", price * fair_pb / pb)

    if not methods:
        return unknown

    fairs = sorted(m["fair"] for m in methods)
    fair_base = statistics.median(fairs)
    upside = (fair_base / price - 1) * 100
    cheap = sum(1 for m in methods if m["upside"] >= 15)
    exp = sum(1 for m in methods if m["upside"] < -5)
    if upside >= 20 or cheap >= max(1, len(methods) * 0.6):
        label = "cheap"
    elif upside < -5 or exp >= max(1, len(methods) * 0.6):
        label = "expensive"
    else:
        label = "fair"

    return {
        "intrinsic_gap_pct": round(upside, 1), "upside_pct": round(upside, 1),
        "valuation_label": label,
        "fair_low": round(fairs[0], 2), "fair_base": round(fair_base, 2),
        "fair_high": round(fairs[-1], 2),
        "buy_zone": round(fair_base * (1 - mos_pct / 100), 2),
        "methods": methods, "method_count": len(methods),
        "margin_of_safety_pct": mos_pct,
    }
