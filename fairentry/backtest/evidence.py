"""Point-in-time provenance and forward outcome evaluation."""
from __future__ import annotations

import statistics
import math
import random
from datetime import date, timedelta


def _wilson_ci(successes: int, total: int, z: float = 1.644854) -> list[float] | None:
    """90% Wilson interval for a binomial hit rate, returned as percentages."""
    if total <= 0:
        return None
    p = successes / total
    den = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / den
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / den
    return [round(max(0, centre - margin) * 100, 1),
            round(min(1, centre + margin) * 100, 1)]


def _cohort_hit_ci(pairs: list[tuple[dict, dict]], samples: int = 1000,
                   seed: int = 42) -> list[float] | None:
    """90% interval from resampling whole entry cohorts, not individual rows."""
    by_cohort: dict[str, list[dict]] = {}
    for observation, target in pairs:
        if target.get("status") in {"reached", "expired"}:
            by_cohort.setdefault(observation.get("entry_date", "unknown"), []).append(target)
    cohorts = list(by_cohort)
    if len(cohorts) < 4:
        return None
    rng = random.Random(seed)
    rates = []
    for _ in range(samples):
        rows = [row for _ in cohorts for row in by_cohort[rng.choice(cohorts)]]
        if rows:
            rates.append(sum(r.get("status") == "reached" for r in rows) / len(rows) * 100)
    rates.sort()
    return [round(rates[int(.05 * len(rates))], 1),
            round(rates[int(.95 * len(rates)) - 1], 1)] if rates else None


def _year_calibration(pairs: list[tuple[dict, dict]]) -> dict:
    out = {}
    for observation, target in pairs:
        year = str(observation.get("entry_date", "unknown"))[:4]
        row = out.setdefault(year, {"observations": 0, "evaluable": 0, "reached": 0})
        row["observations"] += 1
        if target.get("status") in {"reached", "expired"}:
            row["evaluable"] += 1
            row["reached"] += target.get("status") == "reached"
    for row in out.values():
        row["hit_rate_pct"] = round(row["reached"] / row["evaluable"] * 100, 1) if row["evaluable"] else None
    return out


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
        expiry_days = target.get("expiry_days", 365)
        # A value at or below the entry was already exceeded when the position
        # was opened. Preserve that Day 0 fact, but do not describe it as a
        # post-entry target hit (or include it in target hit-rate statistics).
        already_above = bool(value and value <= entry_price)
        # The price-series query includes a small post-boundary grace window so
        # weekly data can prove that a target expired. A price first observed
        # after the contractual expiry must not be converted into a successful
        # hit merely because it is present in that grace window.
        hits = [] if already_above else [
            p for p in series
            if value and p["close"] >= value
            and (date.fromisoformat(p["date"]) - start).days <= expiry_days
        ]
        expired = (date.fromisoformat(series[-1]["date"]) - start).days >= expiry_days
        status = ("unavailable" if not value else
                  "already_above_at_entry" if already_above else
                  "reached" if hits else "expired" if expired else "active")
        first = hits[0] if hits else None
        target_results[model] = {**target, "status": status,
            "hit_date": entry if already_above else first["date"] if first else None,
            "days_to_target": 0 if already_above else
                (date.fromisoformat(first["date"]) - start).days if first else None}
    return {"status": "complete", "horizons": horizon_results, "targets": target_results,
            "max_gain_pct": round((max(closes) / entry_price - 1) * 100, 2),
            "max_drawdown_pct": round((min(closes) / entry_price - 1) * 100, 2),
            "last_date": series[-1]["date"], "last_price": round(series[-1]["close"], 2),
            "path": [[p["date"], round(p["close"], 2)] for p in series]}


def summarize_targets(observations: list[dict], primary="fundamental") -> dict:
    buys = [o for o in observations if o.get("verdict") == "Buy"]
    pairs = [(o, o["outcome"]["targets"].get(primary, {}))
             for o in buys if o.get("outcome")]
    rows = [r for _, r in pairs]
    evaluable = [r for r in rows if r.get("available") and r.get("status") in {"reached", "expired"}]
    reached = [r for r in evaluable if r.get("status") == "reached"]
    days = [r["days_to_target"] for r in reached if r.get("days_to_target") is not None]
    unique = {o.get("ticker") for o, r in pairs if r.get("available") and o.get("ticker")}
    return {"primary_model": primary, "buy_recommendations": len(buys), "evaluable": len(evaluable),
            "reached": len(reached), "expired": len(evaluable) - len(reached),
            "active_or_unavailable": len(rows) - len(evaluable),
            "hit_rate_pct": round(len(reached) / len(evaluable) * 100, 1) if evaluable else None,
            "hit_rate_ci90": _wilson_ci(len(reached), len(evaluable)),
            "hit_rate_cohort_ci90": _cohort_hit_ci(pairs),
            "unique_stocks": len(unique),
            "unique_cohorts": len({o.get("entry_date") for o, r in pairs if r.get("available")}),
            "per_entry_year": _year_calibration(pairs),
            "median_days_to_target": round(statistics.median(days), 1) if days else None}


def summarize_methods(observations: list[dict]) -> dict:
    """Hit-rate and timing for every disclosed target among historical Buys."""
    buys = [o for o in observations if o.get("verdict") == "Buy"]
    names = sorted({k for o in buys for k in o.get("outcome", {}).get("targets", {})})
    out = {}
    for name in names:
        disclosed = [(o, o["outcome"]["targets"][name]) for o in buys
                     if name in o.get("outcome", {}).get("targets", {})
                     and o["outcome"]["targets"][name].get("relevance") != "low"
                     and not o["outcome"]["targets"][name].get("excluded")]
        references = [(o, r) for o, r in disclosed if r.get("role") == "reference"
                      or r.get("status") == "already_above_at_entry"]
        eligible = [(o, r) for o, r in disclosed if r.get("role") != "reference"
                    and r.get("status") != "already_above_at_entry"]
        rows = [r for _, r in eligible]
        completed = [r for r in rows if r.get("status") in {"reached", "expired"}]
        reached = [r for r in completed if r.get("status") == "reached"]
        days = [r["days_to_target"] for r in reached if r.get("days_to_target") is not None]
        upsides = [r["upside_pct"] for r in rows if isinstance(r.get("upside_pct"), (int, float))]
        expiries = [r["expiry_days"] for r in rows if isinstance(r.get("expiry_days"), (int, float))]
        out[name] = {"observations": len(rows), "evaluable": len(completed),
                     "reached": len(reached), "expired": len(completed) - len(reached),
                     "active": sum(1 for r in rows if r.get("status") == "active"),
                     "already_above_at_entry": len(references),
                     "unique_stocks": len({o.get("ticker") for o, _ in eligible if o.get("ticker")}),
                     "hit_rate_pct": round(len(reached) / len(completed) * 100, 1) if completed else None,
                     "hit_rate_ci90": _wilson_ci(len(reached), len(completed)),
                     "hit_rate_cohort_ci90": _cohort_hit_ci(eligible),
                     "unique_cohorts": len({o.get("entry_date") for o, _ in eligible}),
                     "per_entry_year": _year_calibration(eligible),
                     "median_days_to_target": round(statistics.median(days), 1) if days else None,
                     "median_upside_pct": round(statistics.median(upsides), 2) if upsides else None,
                     "expiry_days_range": [min(expiries), max(expiries)] if expiries else None}
    return out
