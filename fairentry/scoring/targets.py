"""Shared target engine for live recommendations and historical replay.

Every caller receives all calculable target methods plus exactly one practical
target when a valid price exists. Historical callers get the same calculations;
only their point-in-time inputs differ.
"""
from __future__ import annotations

import statistics

_METHOD_METRICS = {
    "analyst": ("target_price",),
    "lynch": ("fwd_pe", "eps_growth_next_y"),
    "peer_pe": ("fwd_pe",),
    "peer_ps": ("ps_ratio",),
    "fcf": ("pfcf_ratio",),
    "book": ("pb_ratio",),
}


def _relevance(method, sector):
    sector = str(sector or "").lower()
    if method == "book" and any(x in sector for x in ("technology", "communication", "consumer")):
        return "low"
    if method == "peer_ps" and any(x in sector for x in ("technology", "communication")):
        return "high"
    if method in {"fcf", "peer_pe", "technical", "blended", "fundamental"}:
        return "high"
    return "medium"


def _metric(metrics, key):
    item = metrics.get(key, {})
    return item if isinstance(item, dict) else {"value": item}


def _quality(metrics, keys):
    sources = [str(_metric(metrics, k).get("source") or "unknown") for k in keys]
    if any(s == "seed_const" for s in sources):
        return "current_proxy"
    if any(s in {"seed_price_scaled", "seed_hist"} for s in sources):
        return "approximated"
    if any(s == "unknown" for s in sources):
        return "unknown"
    if any(s == "sec_hist" for s in sources):
        return "reconstructed"
    return "current"


def _timeframe(upside):
    if upside <= 15:
        return 180
    if upside <= 40:
        return 365
    if upside <= 100:
        return 730
    return 1460


def build_target_plan(record: dict, metrics: dict, *, minimum_upside_pct=10.0,
                      maximum_upside_pct=100.0, expiry_days=365,
                      historical=False, practical_policy="nearest_credible") -> dict:
    price = record.get("price")
    valuation = record.get("valuation") or {}
    if not isinstance(price, (int, float)) or price <= 0:
        return {"version": 2, "practical": None, "targets": {},
                "status": "no_valid_price", "explanation": "A valid price is required."}

    targets = {}
    sector = record.get("sector")
    for method in valuation.get("methods", []):
        value = method.get("fair")
        if not isinstance(value, (int, float)) or value <= 0:
            continue
        key = method.get("key") or "unknown"
        upside = (value / price - 1) * 100
        quality = _quality(metrics, _METHOD_METRICS.get(key, ()))
        excluded = historical and quality == "current_proxy"
        targets[key] = {
            "method": key, "label": method.get("name", key), "price": round(value, 2),
            "upside_pct": round(upside, 2), "timeframe_days": _timeframe(max(0, upside)),
            "quality": quality, "confidence": "low" if quality in {"current_proxy", "approximated", "unknown"} else "moderate",
            "available": True, "applicable": not excluded, "excluded": excluded,
            "relevance": _relevance(key, sector),
            "reason": ("Not point-in-time; retained for disclosure only" if excluded else None),
            "role": "supporting", "basis": method.get("basis", "Existing fair-value method"),
        }

    non_analyst = [t["price"] for k, t in targets.items()
                   if k != "analyst" and not t["excluded"] and t["relevance"] != "low"]
    if non_analyst:
        value = statistics.median(non_analyst)
        upside = (value / price - 1) * 100
        targets["fundamental"] = {
            "method": "fundamental", "label": "Fundamental median", "price": round(value, 2),
            "upside_pct": round(upside, 2), "timeframe_days": _timeframe(max(0, upside)),
            "quality": "mixed", "confidence": "moderate", "applicable": True, "excluded": False,
            "available": True,
            "relevance": "high",
            "reason": None, "role": "supporting",
            "basis": "Median of applicable non-analyst fair-value methods",
        }

    distance_200wma = _metric(metrics, "dist_200wma_pct").get("value")
    wma200 = (price / (1 + distance_200wma / 100)
              if isinstance(distance_200wma, (int, float)) and distance_200wma > -100 else None)
    continuation = price * (1 + minimum_upside_pct / 100)
    raw_technical = max(continuation, wma200) if isinstance(wma200, (int, float)) and wma200 > 0 else continuation
    technical = min(raw_technical, price * (1 + max(35, minimum_upside_pct) / 100))
    technical_upside = (technical / price - 1) * 100
    targets["technical"] = {
        "method": "technical", "label": "Technical objective", "price": round(technical, 2),
        "upside_pct": round(technical_upside, 2), "timeframe_days": _timeframe(technical_upside),
        "quality": _quality(metrics, ("price", "dist_200wma_pct")), "confidence": "moderate",
        "available": True, "applicable": True, "excluded": False, "reason": None, "role": "supporting",
        "relevance": "high",
        "basis": f"{minimum_upside_pct:g}% continuation or recovery to the 200-week mean, capped at {max(35, minimum_upside_pct):g}%",
    }

    blend_parts = [t["price"] for k, t in targets.items()
                   if k in {"fundamental", "technical"} and not t["excluded"] and t["price"] > price]
    if blend_parts:
        value = statistics.median(blend_parts)
        upside = (value / price - 1) * 100
        targets["blended"] = {
            "method": "blended", "label": "Blended objective", "price": round(value, 2),
            "upside_pct": round(upside, 2), "timeframe_days": _timeframe(upside),
            "quality": "mixed", "confidence": "moderate", "applicable": True, "excluded": False,
            "available": True,
            "relevance": "high",
            "reason": None, "role": "supporting", "basis": "Median of fundamental and technical objectives",
        }

    # The practical target is the nearest credible target that clears the
    # configured minimum. The technical continuation target guarantees a target
    # for every valid price, while stronger fundamental agreement can win when
    # it is closer and still meaningful.
    candidates = [t for key, t in targets.items() if t["applicable"] and not t["excluded"]
                  and t.get("relevance") != "low"
                  and t["upside_pct"] >= minimum_upside_pct - 0.01
                  and t["upside_pct"] <= maximum_upside_pct
                  and (practical_policy != "fundamental_first"
                       or key not in {"technical", "blended"})]
    candidates.sort(key=lambda t: (1 if t["confidence"] == "low" else 0, t["upside_pct"]))
    if candidates:
        chosen = dict(candidates[0])
        chosen.update({"method": "practical", "selected_method": chosen["method"],
                       "label": "Practical recommended target", "role": "practical",
                       "expiry_days": max(expiry_days, chosen["timeframe_days"]),
                       "status": "pending", "selection_policy": practical_policy})
    elif practical_policy == "fundamental_first":
        chosen = {"method": "practical", "selected_method": None,
                  "label": "Practical recommended target", "price": None,
                  "upside_pct": None, "timeframe_days": expiry_days,
                  "quality": "unavailable", "confidence": "unavailable",
                  "available": False, "applicable": False, "excluded": False,
                  "relevance": "high", "reason": "No stock-specific fundamental target cleared the configured upside range",
                  "role": "practical", "basis": "Fundamental-first historical calibration",
                  "expiry_days": expiry_days, "status": "unavailable",
                  "selection_policy": practical_policy}
    else:
        chosen = dict(targets["technical"])
        chosen.update({"method": "practical", "selected_method": "technical",
                       "label": "Practical recommended target", "role": "practical",
                       "expiry_days": max(expiry_days, chosen["timeframe_days"]),
                       "status": "pending", "selection_policy": practical_policy})
    targets["practical"] = chosen

    for key, target in targets.items():
        target.setdefault("expiry_days", max(expiry_days, target.get("timeframe_days") or expiry_days))
        if key == "practical" or target["excluded"]:
            continue
        if target["price"] <= price:
            target["role"] = "reference"
        elif isinstance(chosen.get("price"), (int, float)) and target["price"] <= chosen["price"]:
            target["role"] = "conservative"
        elif target["upside_pct"] <= 100:
            target["role"] = "growth"
        else:
            target["role"] = "bull"

    return {"version": 2, "status": "target_available" if chosen.get("available") else "no_credible_practical_target", "practical": chosen,
            "targets": targets,
            "explanation": (f"Minimum practical expectation: {chosen['selected_method']} target at ${chosen['price']:.2f}."
                            if isinstance(chosen.get("price"), (int, float))
                            else chosen.get("reason"))}
