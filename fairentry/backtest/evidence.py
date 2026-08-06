"""Point-in-time provenance and forward outcome evaluation."""
from __future__ import annotations

import statistics
from datetime import date, timedelta


def quality_for(metrics: dict) -> dict:
    counts = {"point_in_time": 0, "reconstructed": 0, "approximated": 0, "current_proxy": 0, "missing": 0}
    fields = []
    for field_id, item in sorted(metrics.items()):
        source = (item.get("source") or "unknown") if isinstance(item, dict) else "unknown"
        if source == "sec_hist": quality = "reconstructed"
        elif source in {"seed_price", "seed_hist"} and field_id not in {"fwd_pe", "ps_ratio", "pb_ratio", "pfcf_ratio"}: quality = "point_in_time"
        elif source in {"seed_price_scaled", "seed_hist"}: quality = "approximated"
        elif source == "seed_const": quality = "current_proxy"
        else: quality = "approximated"
        counts[quality] += 1
        fields.append({"field": field_id, "value": item.get("value") if isinstance(item, dict) else item,
                       "source": source, "effective_at": item.get("fetched_at") if isinstance(item, dict) else None,
                       "quality": quality})
    proxy = counts["current_proxy"]
    grade = "strict" if proxy == 0 and counts["approximated"] == 0 else "mostly_point_in_time" if proxy <= max(2, len(fields) // 5) else "experimental"
    return {"grade": grade, "counts": counts, "fields": fields}


def price_series(store, ticker: str, entry: str, days: int) -> list[dict]:
    # Include the first weekly observation after the requested boundary. Without
    # this grace window, a 365-day target whose weekly sample lands on day 364
    # remains "active" forever instead of becoming an honest failure.
    end = (date.fromisoformat(entry) + timedelta(days=days + 14)).isoformat()
    return [{"date": r["d"], "close": r["value_num"]} for r in store.con.execute(
        "SELECT substr(fetched_at,1,10) d,value_num FROM metrics_history "
        "WHERE ticker=? AND field_id='price' AND substr(fetched_at,1,10)>=? "
        "AND substr(fetched_at,1,10)<=? ORDER BY fetched_at", (ticker, entry, end)) if r["value_num"]]


def evaluate_path(series: list[dict], entry_price: float, targets: dict, horizons: tuple[int, ...], entry: str) -> dict:
    if not series or not entry_price:
        return {"status": "data_unavailable", "horizons": {},
                "targets": {k: {**v, "status": "data_unavailable", "hit_date": None,
                                "days_to_target": None} for k, v in targets.items()}}
    start = date.fromisoformat(entry)
    horizon_results = {}
    for h in horizons:
        eligible = [p for p in series if (date.fromisoformat(p["date"]) - start).days >= h]
        if eligible:
            p = eligible[0]
            horizon_results[str(h)] = {"date": p["date"], "price": round(p["close"], 2),
                                       "return_pct": round((p["close"] / entry_price - 1) * 100, 2)}
        else:
            horizon_results[str(h)] = {"status": "insufficient_forward_history"}
    closes = [p["close"] for p in series]
    target_results = {}
    for model, target in targets.items():
        value = target.get("price")
        hits = [p for p in series if value and p["close"] >= value]
        expired = (date.fromisoformat(series[-1]["date"]) - start).days >= target.get("expiry_days", 365)
        status = "unavailable" if not value else "reached" if hits else "expired" if expired else "active"
        first = hits[0] if hits else None
        target_results[model] = {**target, "status": status,
            "hit_date": first["date"] if first else None,
            "days_to_target": (date.fromisoformat(first["date"]) - start).days if first else None}
    return {"status": "complete", "horizons": horizon_results, "targets": target_results,
            "max_gain_pct": round((max(closes) / entry_price - 1) * 100, 2),
            "max_drawdown_pct": round((min(closes) / entry_price - 1) * 100, 2),
            "last_date": series[-1]["date"], "last_price": round(series[-1]["close"], 2),
            "path": [[p["date"], round(p["close"], 2)] for p in series]}


def summarize_targets(observations: list[dict], primary="fundamental") -> dict:
    buys = [o for o in observations if o.get("verdict") == "Buy"]
    rows = [o["outcome"]["targets"].get(primary, {}) for o in buys if o.get("outcome")]
    evaluable = [r for r in rows if r.get("available") and r.get("status") in {"reached", "expired"}]
    reached = [r for r in evaluable if r.get("status") == "reached"]
    days = [r["days_to_target"] for r in reached if r.get("days_to_target") is not None]
    return {"primary_model": primary, "buy_recommendations": len(buys), "evaluable": len(evaluable),
            "reached": len(reached), "expired": len(evaluable) - len(reached),
            "active_or_unavailable": len(rows) - len(evaluable),
            "hit_rate_pct": round(len(reached) / len(evaluable) * 100, 1) if evaluable else None,
            "median_days_to_target": round(statistics.median(days), 1) if days else None}


def summarize_methods(observations: list[dict]) -> dict:
    """Hit-rate and timing for every disclosed target among historical Buys."""
    buys = [o for o in observations if o.get("verdict") == "Buy"]
    names = sorted({k for o in buys for k in o.get("outcome", {}).get("targets", {})})
    out = {}
    for name in names:
        rows = [o["outcome"]["targets"][name] for o in buys
                if name in o.get("outcome", {}).get("targets", {})
                and o["outcome"]["targets"][name].get("role") != "reference"
                and o["outcome"]["targets"][name].get("relevance") != "low"
                and not o["outcome"]["targets"][name].get("excluded")]
        completed = [r for r in rows if r.get("status") in {"reached", "expired"}]
        reached = [r for r in completed if r.get("status") == "reached"]
        days = [r["days_to_target"] for r in reached if r.get("days_to_target") is not None]
        upsides = [r["upside_pct"] for r in rows if isinstance(r.get("upside_pct"), (int, float))]
        out[name] = {"observations": len(rows), "evaluable": len(completed),
                     "reached": len(reached), "expired": len(completed) - len(reached),
                     "active": sum(1 for r in rows if r.get("status") == "active"),
                     "hit_rate_pct": round(len(reached) / len(completed) * 100, 1) if completed else None,
                     "median_days_to_target": round(statistics.median(days), 1) if days else None,
                     "median_upside_pct": round(statistics.median(upsides), 2) if upsides else None}
    return out
