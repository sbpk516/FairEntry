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


def _weighted_median(methods: list[dict]) -> float:
    ordered = sorted(methods, key=lambda m: m["fair"])
    half = sum(m["weight"] for m in ordered) / 2
    running = 0.0
    for method in ordered:
        running += method["weight"]
        if running >= half:
            return method["fair"]
    return ordered[-1]["fair"]


def fair_value(metrics: dict, mos_pct: float = 15.0, sector_med: dict | None = None,
               company_context: dict | None = None,
               features: dict | None = None) -> dict:
    sector_med = sector_med or {}
    company_context = company_context or {}
    features = features or {}
    # These experimental valuation changes are available to the ablation
    # runner but stay out of production scoring until they beat the validated
    # baseline out of sample.
    pb_applicability = features.get("pb_applicability", False)
    valuation_weights = features.get("valuation_weights", False)
    sector = str(company_context.get("sector") or "").lower()
    model = str(company_context.get("business_model") or "").lower()
    asset_heavy = bool(company_context.get("asset_heavy")) or any(
        word in sector for word in ("financial", "real estate", "bank", "insurance")
    ) or model in {"asset_heavy", "financial", "real_estate"}
    price = _num(metrics, "price")
    unknown = {"intrinsic_gap_pct": 0.0, "upside_pct": 0.0, "valuation_label": "unknown",
               "fair_low": price, "fair_base": price, "fair_high": price, "buy_zone": None,
               "methods": [], "excluded_methods": [], "method_count": 0,
               "dispersion_pct": None, "method_agreement_pct": None,
               "valuation_confidence": "low", "warnings": [],
               "margin_of_safety_pct": mos_pct}
    if not price or price <= 0:
        return unknown

    fpe = _num(metrics, "fwd_pe")
    g = _num(metrics, "eps_growth_next_y")
    ps = _num(metrics, "ps_ratio")
    pb = _num(metrics, "pb_ratio")
    pfcf = _num(metrics, "pfcf_ratio")

    methods, excluded, suitability_warnings = [], [], []

    def add(name, key, fair, basis, weight=1.0):
        if fair and fair > 0:
            methods.append({"name": name, "key": key, "fair": round(fair, 2),
                            "upside": round((fair / price - 1) * 100, 1),
                            "weight": weight, "basis": basis})

    tp = _num(metrics, "target_price")
    if tp and tp > 0:
        add("Analyst target", "analyst", tp,
            f"Wall-Street 12-month mean price target = ${tp:.2f}.", .75)
    if g and fpe and fpe > 0 and g > 0:                       # Lynch: fair P/E ~ growth
        fair_pe = max(8.0, min(g, 40.0)); f = price * fair_pe / fpe
        add("Growth-justified (Lynch)", "lynch", f,
            f"Fair P/E ≈ EPS growth {g:.0f}% (capped to 8–40) = {fair_pe:.0f}×. "
            f"Current fwd P/E is {fpe:.1f}× → ${price:.2f} × {fair_pe:.0f} ÷ {fpe:.1f} = ${f:.2f}.")
    if fpe and fpe > 0 and (sector_med.get("fwd_pe") or 0) > 0:
        spe = sector_med["fwd_pe"]; f = price * spe / fpe
        add("Peer forward P/E", "peer_pe", f,
            f"Re-rate to the sector-median forward P/E {spe:.1f}× (vs its own {fpe:.1f}×): "
            f"${price:.2f} × {spe:.1f} ÷ {fpe:.1f} = ${f:.2f}.")
    if ps and ps > 0 and (sector_med.get("ps_ratio") or 0) > 0:
        sps = sector_med["ps_ratio"]; f = price * sps / ps
        add("Peer P/S", "peer_ps", f,
            f"At the sector-median price/sales {sps:.2f}× (vs its own {ps:.2f}×): "
            f"${price:.2f} × {sps:.2f} ÷ {ps:.2f} = ${f:.2f}.", .6)
    if pfcf and pfcf > 0:                                     # growth lifts a fair P/FCF
        mult = 18.0 + (min(g, 30.0) / 2 if g and g > 0 else 0); f = price * mult / pfcf
        add("FCF value", "fcf", f,
            f"Fair price/free-cash-flow of {mult:.0f}× (base 18, lifted by growth) vs its own {pfcf:.1f}×: "
            f"${price:.2f} × {mult:.0f} ÷ {pfcf:.1f} = ${f:.2f}.", 1.25)
    if pb and pb > 0 and (asset_heavy or not pb_applicability):
        fair_pb = sector_med.get("pb_ratio") if (sector_med.get("pb_ratio") or 0) > 0 else 2.0
        f = price * fair_pb / pb
        add("Asset / book", "book", f,
            f"At a fair price/book of {fair_pb:.2f}× ({'sector median' if (sector_med.get('pb_ratio') or 0) > 0 else '~2× default'}) "
            f"vs its own {pb:.2f}×: ${price:.2f} × {fair_pb:.2f} ÷ {pb:.2f} = ${f:.2f}.")
        if not asset_heavy:
            suitability_warnings.append(
                "P/B may be poorly suited to this asset-light/non-financial profile; retained for backtested-baseline compatibility."
            )
    elif pb and pb > 0:
        excluded.append({"name": "Asset / book", "key": "book",
                         "reason": "P/B is not decision-useful for this asset-light/non-financial business profile."})

    if not methods:
        return unknown

    fairs = sorted(m["fair"] for m in methods)
    fair_base = (_weighted_median(methods) if valuation_weights
                 else statistics.median(fairs))
    dispersion = (fairs[-1] - fairs[0]) / fair_base * 100 if fair_base else 0
    agreement = sum(1 for f in fairs if abs(f / fair_base - 1) <= .25) / len(fairs) * 100
    warnings = list(suitability_warnings)
    if dispersion > 75:
        warnings.append("Valuation methods disagree widely; treat base fair value as low confidence.")
    confidence = "high" if dispersion <= 40 and agreement >= 75 and len(methods) >= 3 \
        else "medium" if dispersion <= 75 and agreement >= 50 else "low"
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
        "methods": methods, "excluded_methods": excluded, "method_count": len(methods),
        "dispersion_pct": round(dispersion, 1),
        "method_agreement_pct": round(agreement, 1),
        "valuation_confidence": confidence, "warnings": warnings,
        "margin_of_safety_pct": mos_pct,
    }
