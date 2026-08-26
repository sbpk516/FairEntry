"""Transparent multi-method fair value.

Only methods that the SFA replay can reproduce are allowed to determine the
tested fair-value range used by Buy / Watch / Avoid. Current analyst targets and
forecast-based methods remain visible as information-only estimates.
"""
from __future__ import annotations

import statistics

from .valuation_agreement import evaluate_method_agreement


def _num(metrics, key):
    value = metrics.get(key, {})
    value = value.get("value") if isinstance(value, dict) else value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fair_value(metrics: dict, mos_pct: float = 15.0,
               sector_med: dict | None = None, *, sector: str | None = None,
               industry: str | None = None) -> dict:
    sector_med = sector_med or {}
    price = _num(metrics, "price")
    unknown = {
        "intrinsic_gap_pct": 0.0,
        "upside_pct": 0.0,
        "valuation_label": "unknown",
        "fair_low": price,
        "fair_base": price,
        "fair_high": price,
        "buy_zone": None,
        "methods": [],
        "method_count": 0,
        "context_method_count": 0,
        "method_agreement": None,
        "margin_of_safety_pct": mos_pct,
    }
    if not price or price <= 0:
        return unknown

    forward_pe = _num(metrics, "fwd_pe")
    forecast_growth = _num(metrics, "eps_growth_next_y")
    price_sales = _num(metrics, "ps_ratio")
    price_book = _num(metrics, "pb_ratio")
    price_fcf = _num(metrics, "pfcf_ratio")
    methods = []

    def add(name, key, fair, basis, decision_status="tested"):
        if fair and fair > 0:
            methods.append({
                "name": name,
                "key": key,
                "fair": round(fair, 2),
                "upside": round((fair / price - 1) * 100, 1),
                "basis": basis,
                "decision_status": decision_status,
            })

    analyst_target = _num(metrics, "target_price")
    if analyst_target and analyst_target > 0:
        add(
            "Analyst target", "analyst", analyst_target,
            f"Wall-Street 12-month mean price target = ${analyst_target:.2f}.",
            "information_only",
        )
    if forecast_growth and forward_pe and forward_pe > 0 and forecast_growth > 0:
        fair_pe = max(8.0, min(forecast_growth, 40.0))
        fair = price * fair_pe / forward_pe
        add(
            "Growth-justified (Lynch)", "lynch", fair,
            f"Forecast growth {forecast_growth:.0f}% suggests a {fair_pe:.0f}x fair P/E; "
            f"${price:.2f} x {fair_pe:.0f} / {forward_pe:.1f} = ${fair:.2f}.",
            "information_only",
        )
    if forward_pe and forward_pe > 0 and (sector_med.get("fwd_pe") or 0) > 0:
        sector_pe = sector_med["fwd_pe"]
        fair = price * sector_pe / forward_pe
        add(
            "Peer forward P/E", "peer_pe", fair,
            f"${price:.2f} x sector P/E {sector_pe:.1f} / company P/E "
            f"{forward_pe:.1f} = ${fair:.2f}.",
            "information_only",
        )

    if price_sales and price_sales > 0 and (sector_med.get("ps_ratio") or 0) > 0:
        sector_ps = sector_med["ps_ratio"]
        fair = price * sector_ps / price_sales
        add(
            "Peer P/S", "peer_ps", fair,
            f"${price:.2f} x sector P/S {sector_ps:.2f} / company P/S "
            f"{price_sales:.2f} = ${fair:.2f}.",
        )
    if price_fcf and price_fcf > 0:
        fair_multiple = 18.0
        fair = price * fair_multiple / price_fcf
        add(
            "FCF value", "fcf", fair,
            f"Fixed fair price/free-cash-flow of {fair_multiple:.0f}x versus the "
            f"company's {price_fcf:.1f}x: ${fair:.2f}.",
        )
    if price_book and price_book > 0:
        fair_pb = (sector_med.get("pb_ratio")
                   if (sector_med.get("pb_ratio") or 0) > 0 else 2.0)
        fair = price * fair_pb / price_book
        add(
            "Asset / book", "book", fair,
            f"${price:.2f} x fair P/B {fair_pb:.2f} / company P/B "
            f"{price_book:.2f} = ${fair:.2f}.",
        )

    decision_methods = [m for m in methods if m["decision_status"] == "tested"]
    agreement = evaluate_method_agreement(
        methods,
        price,
        sector=sector,
        industry=industry,
        roe_pct=_num(metrics, "roe"),
    )
    if not decision_methods:
        unknown["methods"] = methods
        unknown["context_method_count"] = len(methods)
        unknown["method_agreement"] = agreement
        return unknown

    fair_prices = sorted(m["fair"] for m in decision_methods)
    fair_base = statistics.median(fair_prices)
    upside = (fair_base / price - 1) * 100
    cheap = sum(1 for m in decision_methods if m["upside"] >= 15)
    expensive = sum(1 for m in decision_methods if m["upside"] < -5)
    if upside >= 20 or cheap >= max(1, len(decision_methods) * 0.6):
        label = "cheap"
    elif upside < -5 or expensive >= max(1, len(decision_methods) * 0.6):
        label = "expensive"
    else:
        label = "fair"

    return {
        "intrinsic_gap_pct": round(upside, 1),
        "upside_pct": round(upside, 1),
        "valuation_label": label,
        "fair_low": round(fair_prices[0], 2),
        "fair_base": round(fair_base, 2),
        "fair_high": round(fair_prices[-1], 2),
        "buy_zone": round(fair_base * (1 - mos_pct / 100), 2),
        "methods": methods,
        "method_count": len(decision_methods),
        "context_method_count": len(methods) - len(decision_methods),
        "method_agreement": agreement,
        "margin_of_safety_pct": mos_pct,
    }
