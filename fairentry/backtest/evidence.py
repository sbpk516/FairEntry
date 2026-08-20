"""Point-in-time provenance and forward outcome evaluation."""
from __future__ import annotations

import statistics
import math
import random
from datetime import date, timedelta


RETURN_THRESHOLDS_PCT = (10, 15, 20, 25, 30, 40, 50, 75, 100, 150, 200)
RETURN_HORIZONS_DAYS = (90, 180, 270, 365, 730, 1095, 1825)
PRACTICAL_TARGET_DEADLINES = (
    (30.0, 365, 1),
    (70.0, 730, 2),
    (120.0, 1095, 3),
    (271.0, 1825, 5),
)


def practical_target_deadline(upside_pct: float | int | None) -> dict | None:
    """Map a target gain to the deadline promised by the product.

    The displayed boundaries are deliberately rounded for a family audience:
    30% in one year, about 70% in two, 120% in three and 271% in five.
    Targets beyond that range get an honest calculated year count rather than
    being squeezed into an unrealistic five-year promise.
    """
    if not isinstance(upside_pct, (int, float)) or upside_pct <= 0:
        return None
    for maximum, days, years in PRACTICAL_TARGET_DEADLINES:
        if float(upside_pct) <= maximum:
            return {
                "deadline_days": days,
                "deadline_years": years,
                "band_maximum_gain_pct": maximum,
                "within_standard_five_year_range": True,
            }
    years = max(6, math.ceil(math.log1p(float(upside_pct) / 100) / math.log(1.3)))
    return {
        "deadline_days": years * 365,
        "deadline_years": years,
        "band_maximum_gain_pct": round((1.3 ** years - 1) * 100, 1),
        "within_standard_five_year_range": False,
    }


def source_quality(source: str | None, field_id: str | None = None) -> str:
    """Classify provenance once so policy enforcement and UI labels agree."""
    source = source or "unknown"
    if source == "seed_const" or "current_proxy" in source:
        return "current_proxy"
    if "proxy" in source or source == "seed_price_scaled":
        return "approximated"
    if source == "sec_hist":
        return "reconstructed"
    if source.startswith("sharadar_") or source in {
        "seed_price", "seed_hist", "computed", "FairEntry breakout_v2"
    }:
        return "point_in_time"
    return "approximated"


def metrics_for_policy(metrics: dict, strategy) -> dict:
    """Apply the declared source policy before screening and scoring."""
    if strategy.data_quality_mode == "experimental":
        return dict(metrics)
    excluded = set(strategy.strict_excluded_sources)
    filtered = {
        key: value for key, value in metrics.items()
        if isinstance(value, dict)
        and value.get("source") not in excluded
        and source_quality(value.get("source"), key) not in excluded
    }
    filtered = {
        key: value for key, value in filtered.items()
        if source_quality(value.get("source"), key) != "current_proxy"
    }
    if strategy.data_quality_mode == "strict":
        filtered = {
            key: value for key, value in filtered.items()
            if source_quality(value.get("source"), key) in {
                "point_in_time", "reconstructed"
            }
        }
    return filtered


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


def _two_way_hit_ci(pairs: list[tuple[dict, dict]], samples: int = 1000,
                    seed: int = 43) -> list[float] | None:
    """Pigeonhole bootstrap across both entry cohorts and securities."""
    rows = [(o, t) for o, t in pairs if t.get("status") in {"reached", "expired"}]
    cohorts = sorted({o.get("decision_date") or o.get("entry_date") for o, _ in rows})
    securities = sorted({o.get("security_id") or o.get("ticker") for o, _ in rows})
    if len(cohorts) < 4 or len(securities) < 4:
        return None
    rng, rates = random.Random(seed), []
    for _ in range(samples):
        cohort_weights = {key: 0 for key in cohorts}
        security_weights = {key: 0 for key in securities}
        for _ in cohorts:
            cohort_weights[rng.choice(cohorts)] += 1
        for _ in securities:
            security_weights[rng.choice(securities)] += 1
        reached = total = 0
        for observation, target in rows:
            weight = (
                cohort_weights.get(observation.get("decision_date") or observation.get("entry_date"), 0)
                * security_weights.get(observation.get("security_id") or observation.get("ticker"), 0)
            )
            total += weight
            reached += weight * (target.get("status") == "reached")
        if total:
            rates.append(reached / total * 100)
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


def quality_for(metrics: dict, expected_fields: set[str] | None = None) -> dict:
    counts = {"point_in_time": 0, "reconstructed": 0, "approximated": 0, "current_proxy": 0, "missing": 0}
    fields = []
    for field_id, item in sorted(metrics.items()):
        source = (item.get("source") or "unknown") if isinstance(item, dict) else "unknown"
        quality = source_quality(source, field_id)
        counts[quality] += 1
        fields.append({"field": field_id, "value": item.get("value") if isinstance(item, dict) else item,
                       "source": source, "effective_at": item.get("fetched_at") if isinstance(item, dict) else None,
                       "quality": quality})
    missing = sorted((expected_fields or set()) - set(metrics))
    counts["missing"] = len(missing)
    fields.extend({"field": field_id, "value": None, "source": None,
                   "effective_at": None, "quality": "missing"}
                  for field_id in missing)
    proxy = counts["current_proxy"]
    grade = "strict" if proxy == 0 and counts["approximated"] == 0 and not missing else "mostly_point_in_time" if proxy <= max(2, len(fields) // 5) else "experimental"
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


def evaluate_path(series: list[dict], entry_price: float, targets: dict,
                  horizons: tuple[int, ...], entry: str,
                  *, terminal_date: str | None = None) -> dict:
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
        if model == "practical":
            target_results[model]["performance_test"] = _practical_target_test(
                series, entry_price, target, entry, terminal_date=terminal_date
            )
    return {"status": "complete", "horizons": horizon_results, "targets": target_results,
            "max_gain_pct": round((max(closes) / entry_price - 1) * 100, 2),
            "max_drawdown_pct": round((min(closes) / entry_price - 1) * 100, 2),
            "last_date": series[-1]["date"], "last_price": round(series[-1]["close"], 2),
            "path": [[p["date"], round(p["close"], 2)] for p in series]}


def _practical_target_test(series: list[dict], entry_price: float, target: dict,
                           entry: str, *, terminal_date: str | None = None) -> dict:
    """Test a frozen Practical Target using the 30%-per-year deadline ladder."""
    value = target.get("price")
    upside = target.get("upside_pct")
    deadline = practical_target_deadline(upside)
    base = {
        "rule": "deadline_from_30_pct_annual_compounding",
        "target_price": value,
        "target_upside_pct": upside,
        **(deadline or {}),
    }
    if not deadline or not isinstance(value, (int, float)) or value <= 0 or not entry_price:
        return {**base, "status": "unavailable", "hit_date": None,
                "days_to_target": None, "max_drawdown_before_result_pct": None}
    start = date.fromisoformat(entry)
    rows = []
    for point in series:
        observed = point.get("date")
        close = point.get("close")
        if not observed or not isinstance(close, (int, float)):
            continue
        elapsed = (date.fromisoformat(str(observed)[:10]) - start).days
        if elapsed >= 0:
            rows.append((elapsed, str(observed)[:10], float(close)))
    rows.sort()
    already_above = value <= entry_price
    first_any = None if already_above else next((row for row in rows if row[2] >= value), None)
    reached_on_time = bool(first_any and first_any[0] <= deadline["deadline_days"])
    observed_days = rows[-1][0] if rows else None
    terminal_days = None
    if terminal_date:
        terminal_days = max(0, (date.fromisoformat(str(terminal_date)[:10]) - start).days)
    mature = (isinstance(observed_days, (int, float))
              and observed_days >= deadline["deadline_days"])
    terminal_before_deadline = (isinstance(terminal_days, (int, float))
                                and terminal_days <= deadline["deadline_days"])
    status = ("already_above_at_entry" if already_above else
              "reached" if reached_on_time else
              "reached_late" if first_any and mature else
              "expired" if mature or terminal_before_deadline else "active")
    result_day = (first_any[0] if first_any else
                  min([x for x in (observed_days, terminal_days, deadline["deadline_days"])
                       if isinstance(x, (int, float))], default=None))
    drawdown_rows = [row for row in rows if result_day is None or row[0] <= result_day]
    drawdown = (round((min(row[2] for row in drawdown_rows) / entry_price - 1) * 100, 2)
                if drawdown_rows else None)
    return {
        **base,
        "status": status,
        "hit_date": entry if already_above else first_any[1] if first_any else None,
        "days_to_target": 0 if already_above else first_any[0] if first_any else None,
        "last_observed_days": observed_days,
        "terminal_days": terminal_days,
        "max_drawdown_before_result_pct": drawdown,
    }


def fixed_return_milestones(
    series: list[dict],
    entry_closeadj: float,
    entry: str,
    *,
    entry_cost_bps: float = 0,
    exit_cost_bps: float = 0,
    terminal_date: str | None = None,
    thresholds: tuple[int, ...] = RETURN_THRESHOLDS_PCT,
) -> dict:
    """Record first daily-close attainment for fixed investment returns.

    The calculation uses dividend-adjusted closes and both sides of configured
    execution costs. It stores only derived milestone days, never the vendor
    price path, so the aggregate can be published safely.
    """
    if not entry_closeadj or entry_closeadj <= 0:
        return {"status": "data_unavailable", "first_hit_days": {}}
    start = date.fromisoformat(entry)
    entry_basis = entry_closeadj * (1 + entry_cost_bps / 10000)
    rows = []
    for point in series:
        observed = point.get("date")
        closeadj = point.get("closeadj", point.get("close"))
        if not observed or not isinstance(closeadj, (int, float)) or closeadj < 0:
            continue
        elapsed = (date.fromisoformat(str(observed)) - start).days
        if elapsed < 0:
            continue
        net_return = (
            (float(closeadj) * (1 - exit_cost_bps / 10000)) / entry_basis - 1
        ) * 100
        rows.append((elapsed, net_return, str(observed)))
    rows.sort()
    first_hits = {}
    for threshold in thresholds:
        first = next((elapsed for elapsed, value, _ in rows if value >= threshold), None)
        first_hits[str(threshold)] = first
    terminal_days = None
    if terminal_date:
        terminal_day = date.fromisoformat(str(terminal_date)[:10])
        terminal_days = max(0, (terminal_day - start).days)
    return {
        "status": "observed" if rows else "data_unavailable",
        "return_basis": "dividend_adjusted_close_with_entry_and_exit_costs",
        "first_hit_days": first_hits,
        "max_drawdown_before_first_hit_pct": {
            str(threshold): (
                round(min(value for elapsed, value, _ in rows
                          if elapsed <= first_hits[str(threshold)]), 2)
                if first_hits.get(str(threshold)) is not None else None
            )
            for threshold in thresholds
        },
        "last_observed_days": rows[-1][0] if rows else None,
        "last_observed_date": rows[-1][2] if rows else None,
        "terminal_days": terminal_days,
        "max_return_pct": round(max((value for _, value, _ in rows), default=0), 2),
        "max_return_pct_by_horizon": {
            str(horizon): round(
                max((value for elapsed, value, _ in rows if elapsed <= horizon), default=0),
                2,
            )
            for horizon in RETURN_HORIZONS_DAYS
        },
        "max_drawdown_pct_by_horizon": {
            str(horizon): round(
                min((value for elapsed, value, _ in rows if elapsed <= horizon), default=0),
                2,
            )
            for horizon in RETURN_HORIZONS_DAYS
        },
    }


def _percentile(values: list[int], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return float(ordered[lower])
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _buy_episode_roots(observations: list[dict], max_gap_days: int) -> list[dict]:
    """Collapse a contiguous run of Buy recommendations into one entry episode.

    The first Buy remains the investment entry used for outcome calculations.
    A small derived summary retains the later Buy dates so the UI can explain
    the complete recommendation story without counting them as new investments.
    """
    grouped: dict[str, list[dict]] = {}
    for observation in observations:
        key = (observation.get("issuer_key") or observation.get("security_id")
               or observation.get("ticker"))
        if key and observation.get("entry_date"):
            grouped.setdefault(str(key), []).append(observation)
    episodes = []

    def finish(rows: list[dict]):
        if not rows:
            return
        root = dict(rows[0])
        prices = [float(row["entry_price"]) for row in rows
                  if isinstance(row.get("entry_price"), (int, float))]
        milestone = root.get("return_milestones") or {}
        days_to_30 = (milestone.get("first_hit_days") or {}).get("30")
        practical = ((root.get("outcome") or {}).get("targets") or {}).get("practical", {})
        practical_test = practical.get("performance_test") or {}
        fixed_status = (
            "within_one_year" if isinstance(days_to_30, (int, float)) and days_to_30 <= 365 else
            "during_year_two" if isinstance(days_to_30, (int, float)) and days_to_30 <= 730 else
            "during_year_three" if isinstance(days_to_30, (int, float)) and days_to_30 <= 1095 else
            "after_three_years" if isinstance(days_to_30, (int, float)) else
            "never_within_three_years" if (
                isinstance(milestone.get("last_observed_days"), (int, float))
                and milestone.get("last_observed_days") >= 1095
            ) or isinstance(milestone.get("terminal_days"), (int, float)) else
            "still_waiting"
        )
        entry_price = (float(root["entry_price"])
                       if isinstance(root.get("entry_price"), (int, float)) else None)
        root["episode"] = {
            "issuer_key": root.get("issuer_key"),
            "ticker": root.get("ticker"),
            "company": root.get("company"),
            "strategy": root.get("strategy_key") or root.get("primary_strategy"),
            "started": rows[0]["entry_date"],
            "last_buy": rows[-1]["entry_date"],
            "buy_signals": len(rows),
            "entry_price_low": round(min(prices), 2) if prices else None,
            "entry_price_high": round(max(prices), 2) if prices else None,
            "entry_price": round(entry_price, 2) if entry_price else None,
            "highest_gain_within_one_year_pct": (
                milestone.get("max_return_pct_by_horizon", {}).get("365")
            ),
            "fixed_30_target_price": round(entry_price * 1.3, 2) if entry_price else None,
            "fixed_30_status": fixed_status,
            "days_to_30_pct": (int(days_to_30)
                               if isinstance(days_to_30, (int, float)) else None),
            "fixed_30_hit_date": (
                (date.fromisoformat(rows[0]["entry_date"]) + timedelta(days=int(days_to_30))).isoformat()
                if isinstance(days_to_30, (int, float)) else None
            ),
            "max_drawdown_before_30_pct": (
                milestone.get("max_drawdown_before_first_hit_pct", {}).get("30")
            ),
            "max_drawdown_within_one_year_pct": (
                milestone.get("max_drawdown_pct_by_horizon", {}).get("365")
            ),
            "practical_target": {
                "price": practical.get("price"),
                "upside_pct": practical.get("upside_pct"),
                "selected_method": practical.get("selected_method"),
                "basis": practical.get("basis"),
                "reason": practical.get("reason"),
                **practical_test,
            } if practical else None,
        }
        episodes.append(root)

    for rows in grouped.values():
        current_episode = []
        previous_date = None
        for observation in sorted(rows, key=lambda row: row["entry_date"]):
            current = date.fromisoformat(observation["entry_date"])
            if previous_date is not None and (current - previous_date).days > max_gap_days:
                finish(current_episode)
                current_episode = []
            if observation.get("verdict") == "Buy":
                if observation.get("return_milestones"):
                    current_episode.append(observation)
            else:
                finish(current_episode)
                current_episode = []
            previous_date = current
        finish(current_episode)
    return sorted(episodes, key=lambda row: (row["entry_date"], row.get("ticker", "")))


def _return_attainment_view(
    rows: list[dict], thresholds: tuple[int, ...], horizons: tuple[int, ...]
) -> dict:
    matrix = {}
    for threshold in thresholds:
        horizon_rows = {}
        for horizon in horizons:
            completed, reached, days = [], [], []
            for row in rows:
                milestone = row.get("return_milestones") or {}
                first = (milestone.get("first_hit_days") or {}).get(str(threshold))
                observed_days = milestone.get("last_observed_days")
                terminal_days = milestone.get("terminal_days")
                hit = isinstance(first, (int, float)) and first <= horizon
                mature = (
                    isinstance(observed_days, (int, float)) and observed_days >= horizon
                ) or (
                    isinstance(terminal_days, (int, float)) and terminal_days <= horizon
                )
                if hit or mature:
                    completed.append(row)
                    if hit:
                        reached.append(row)
                        days.append(int(first))
            total = len(completed)
            hits = len(reached)
            horizon_rows[str(horizon)] = {
                "threshold_pct": threshold,
                "horizon_days": horizon,
                "reached": hits,
                "evaluable": total,
                "expired": total - hits,
                "active": len(rows) - total,
                "hit_rate_pct": round(hits / total * 100, 1) if total else None,
                "hit_rate_ci90": _wilson_ci(hits, total),
                "median_days_to_hit": round(statistics.median(days), 1) if days else None,
                "p25_days_to_hit": round(_percentile(days, .25), 1) if days else None,
                "p75_days_to_hit": round(_percentile(days, .75), 1) if days else None,
                "unique_issuers": len({
                    row.get("issuer_key") or row.get("security_id") or row.get("ticker")
                    for row in completed
                }),
            }
        matrix[str(threshold)] = horizon_rows
    return {"observations": len(rows), "matrix": matrix}


def summarize_buy_return_achievement(
    observations: list[dict],
    step_days: int,
    *,
    thresholds: tuple[int, ...] = RETURN_THRESHOLDS_PCT,
    horizons: tuple[int, ...] = RETURN_HORIZONS_DAYS,
) -> dict:
    """Summarize fixed-return attainment for raw signals and Buy episodes."""
    buys = [row for row in observations
            if row.get("verdict") == "Buy" and row.get("return_milestones")]
    max_gap_days = max(step_days + 1, int(math.ceil(step_days * 1.5)))
    episodes = _buy_episode_roots(observations, max_gap_days)
    details = [row["episode"] for row in episodes]
    fixed_one_year = _return_attainment_view(
        episodes, (30,), (365,)
    )["matrix"]["30"]["365"]
    practical_rows = [row["practical_target"] for row in details
                      if row.get("practical_target")
                      and row["practical_target"].get("status")
                      not in {None, "unavailable", "already_above_at_entry"}]
    practical_completed = [row for row in practical_rows
                           if row.get("status") in {"reached", "reached_late", "expired"}]
    practical_reached = [row for row in practical_completed if row.get("status") == "reached"]
    practical_late = [row for row in practical_completed if row.get("status") == "reached_late"]
    practical_days = [row["days_to_target"] for row in practical_reached
                      if isinstance(row.get("days_to_target"), (int, float))]
    practical_by_deadline = {}
    for years in sorted({row.get("deadline_years") for row in practical_rows
                         if isinstance(row.get("deadline_years"), int)}):
        band = [row for row in practical_rows if row.get("deadline_years") == years]
        completed = [row for row in band
                     if row.get("status") in {"reached", "reached_late", "expired"}]
        reached = [row for row in completed if row.get("status") == "reached"]
        practical_by_deadline[str(years)] = {
            "deadline_years": years,
            "observations": len(band),
            "evaluable": len(completed),
            "reached": len(reached),
            "reached_late": sum(row.get("status") == "reached_late" for row in completed),
            "expired": sum(row.get("status") == "expired" for row in completed),
            "active": sum(row.get("status") == "active" for row in band),
            "hit_rate_pct": (round(len(reached) / len(completed) * 100, 1)
                             if completed else None),
        }
    return {
        "version": 2,
        "thresholds_pct": list(thresholds),
        "horizons_days": list(horizons),
        "return_basis": "dividend_adjusted_close_with_entry_and_exit_costs",
        "hit_rule": "first daily adjusted close at or above fixed net return",
        "episode_definition": (
            "Consecutive Buy recommendations for one issuer are one episode; "
            "a non-Buy observation or a gap longer than 1.5 cohort intervals starts a new episode."
        ),
        "episode_max_gap_days": max_gap_days,
        "active_policy": (
            "Reached observations are immediately evaluable; otherwise the full horizon "
            "or a terminal security event must be observed. Incomplete recent observations are active."
        ),
        "views": {
            "episodes": _return_attainment_view(episodes, thresholds, horizons),
            "signals": _return_attainment_view(buys, thresholds, horizons),
        },
        "primary_success_contract": {
            "fixed_30_within_one_year": fixed_one_year,
            "fixed_30_timing": {
                key: sum(row.get("fixed_30_status") == key for row in details)
                for key in ("within_one_year", "during_year_two", "during_year_three",
                            "after_three_years", "never_within_three_years", "still_waiting")
            },
            "practical_target_with_compounding_deadline": {
                "observations": len(practical_rows),
                "reached": len(practical_reached),
                "reached_late": len(practical_late),
                "evaluable": len(practical_completed),
                "expired": sum(row.get("status") == "expired" for row in practical_completed),
                "active": sum(row.get("status") == "active" for row in practical_rows),
                "hit_rate_pct": (round(len(practical_reached) / len(practical_completed) * 100, 1)
                                 if practical_completed else None),
                "hit_rate_ci90": _wilson_ci(len(practical_reached), len(practical_completed)),
                "median_days_to_hit": (round(statistics.median(practical_days), 1)
                                       if practical_days else None),
                "outside_five_year_range": sum(
                    row.get("within_standard_five_year_range") is False for row in practical_rows
                ),
                "by_deadline_years": practical_by_deadline,
                "deadline_rule": [
                    {"maximum_gain_pct": maximum, "deadline_days": days,
                     "deadline_years": years}
                    for maximum, days, years in PRACTICAL_TARGET_DEADLINES
                ],
            },
        },
        "episode_details": details,
    }


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
            "hit_rate_two_way_ci90": _two_way_hit_ci(pairs),
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
                     "hit_rate_two_way_ci90": _two_way_hit_ci(eligible),
                     "unique_cohorts": len({o.get("entry_date") for o, _ in eligible}),
                     "per_entry_year": _year_calibration(eligible),
                     "median_days_to_target": round(statistics.median(days), 1) if days else None,
                     "median_upside_pct": round(statistics.median(upsides), 2) if upsides else None,
                     "expiry_days_range": [min(expiries), max(expiries)] if expiries else None}
    return out
