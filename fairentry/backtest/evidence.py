"""Point-in-time provenance and forward outcome evaluation."""
from __future__ import annotations

import statistics
import math
import random
import json
from datetime import date, timedelta
from pathlib import Path


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
                  *, terminal_date: str | None = None,
                  terminal_event: dict | None = None) -> dict:
    if not series or not entry_price:
        return {"status": "data_unavailable", "horizons": {},
                "targets": {k: {**v, "status": "data_unavailable", "hit_date": None,
                                "days_to_target": None} for k, v in targets.items()}}
    start = date.fromisoformat(entry)
    if terminal_event and not terminal_date:
        terminal_date = str(terminal_event.get("date") or "")[:10] or None
    terminal_policy = _terminal_outcome_policy(terminal_event)
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
        observed_days = (date.fromisoformat(series[-1]["date"]) - start).days
        expired = observed_days >= expiry_days
        terminal_days = (
            max(0, (date.fromisoformat(str(terminal_date)[:10]) - start).days)
            if terminal_date else None
        )
        ended_early = (
            isinstance(terminal_days, (int, float))
            and terminal_days <= expiry_days
            and not expired
        )
        status = ("unavailable" if not value else
                  "already_above_at_entry" if already_above else
                  "reached" if hits else "expired" if expired else "active")
        if status == "active" and ended_early:
            status = ("expired" if terminal_policy["counts_as_failure"]
                      else "closed_early_excluded")
        first = hits[0] if hits else None
        target_results[model] = {**target, "status": status,
            "hit_date": entry if already_above else first["date"] if first else None,
            "days_to_target": 0 if already_above else
                (date.fromisoformat(first["date"]) - start).days if first else None,
            "last_observed_days": observed_days,
            "last_observed_date": str(series[-1]["date"])[:10],
            "terminal_days": terminal_days,
            "counted_in_success_rate": status in {"reached", "expired"},
            "counting_reason": (
                terminal_policy["counting_reason"] if ended_early
                else "The full target period was observed."
                if expired else "The target was reached."
                if status == "reached" else "The result is still incomplete."
            ),
        }
        if model == "practical":
            target_results[model]["performance_test"] = _practical_target_test(
                series, entry_price, target, entry, terminal_date=terminal_date,
                terminal_event=terminal_event,
            )
    return {"status": "complete", "horizons": horizon_results, "targets": target_results,
            "max_gain_pct": round((max(closes) / entry_price - 1) * 100, 2),
            "max_drawdown_pct": round((min(closes) / entry_price - 1) * 100, 2),
            "last_date": series[-1]["date"], "last_price": round(series[-1]["close"], 2),
            "path": [[p["date"], round(p["close"], 2)] for p in series]}


def _practical_target_test(series: list[dict], entry_price: float, target: dict,
                           entry: str, *, terminal_date: str | None = None,
                           terminal_event: dict | None = None) -> dict:
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
                "days_to_target": None, "max_drawdown_before_result_pct": None,
                "highest_gain_before_deadline_pct": None,
                "highest_price_before_deadline": None}
    start = date.fromisoformat(entry)
    if terminal_event and not terminal_date:
        terminal_date = str(terminal_event.get("date") or "")[:10] or None
    terminal_policy = _terminal_outcome_policy(terminal_event)
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
    closed_early_excluded = terminal_before_deadline and not mature and not terminal_policy["counts_as_failure"]
    status = ("already_above_at_entry" if already_above else
              "reached" if reached_on_time else
              "reached_late" if first_any and mature else
              "expired" if mature or (terminal_before_deadline and terminal_policy["counts_as_failure"]) else
              "closed_early_excluded" if closed_early_excluded else "active")
    result_day = (first_any[0] if first_any else
                  min([x for x in (observed_days, terminal_days, deadline["deadline_days"])
                       if isinstance(x, (int, float))], default=None))
    drawdown_rows = [row for row in rows if result_day is None or row[0] <= result_day]
    drawdown = (round((min(row[2] for row in drawdown_rows) / entry_price - 1) * 100, 2)
                if drawdown_rows else None)
    evidence_end = min(
        [x for x in (deadline["deadline_days"], terminal_days) if isinstance(x, (int, float))],
        default=deadline["deadline_days"],
    )
    deadline_rows = [row for row in rows if row[0] <= evidence_end]
    highest_price = max((row[2] for row in deadline_rows), default=None)
    highest_gain = (
        round((highest_price / entry_price - 1) * 100, 2)
        if highest_price is not None else None
    )
    return {
        **base,
        "status": status,
        "hit_date": entry if already_above else first_any[1] if first_any else None,
        "days_to_target": 0 if already_above else first_any[0] if first_any else None,
        "last_observed_days": observed_days,
        "last_observed_date": rows[-1][1] if rows else None,
        "terminal_days": terminal_days,
        "deadline_date": (start + timedelta(days=deadline["deadline_days"])).isoformat(),
        "counted_in_success_rate": status in {"reached", "reached_late", "expired"},
        "counting_reason": (
            terminal_policy["counting_reason"] if terminal_before_deadline and not mature
            else "The full promised period was observed."
            if mature else "The target was reached."
            if status == "reached" else "The result is still incomplete."
        ),
        "max_drawdown_before_result_pct": drawdown,
        "highest_gain_before_deadline_pct": highest_gain,
        "highest_price_before_deadline": round(highest_price, 2)
        if highest_price is not None else None,
    }


def fixed_return_milestones(
    series: list[dict],
    entry_closeadj: float,
    entry: str,
    *,
    entry_cost_bps: float = 0,
    exit_cost_bps: float = 0,
    terminal_date: str | None = None,
    terminal_event: dict | None = None,
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
    if terminal_event and not terminal_date:
        terminal_date = str(terminal_event.get("date") or "")[:10] or None
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
        "terminal_policy": _terminal_outcome_policy(terminal_event),
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


_TERMINAL_REASON = {
    "acquisitionby": ("acquired", "The company was acquired before the required test period ended."),
    "mergerto": ("merged", "The company merged into another company before the required test period ended."),
    "bankruptcyliquidation": (
        "bankruptcy_or_liquidation",
        "The company entered bankruptcy or liquidation before the required test period ended.",
    ),
    "regulatorydelisting": (
        "regulatory_delisting", "The stock was removed from its exchange by a regulator."
    ),
    "voluntarydelisting": (
        "voluntary_delisting", "The company voluntarily stopped exchange trading."
    ),
    "delisted": (
        "unclassified_trading_end",
        "The trading record ended, but the available event data does not confirm why.",
    ),
    "delistedinferred": (
        "unclassified_trading_end",
        "The price history ended, but no confirmed ending event was found.",
    ),
}


def _terminal_outcome_policy(terminal: dict | None) -> dict:
    """Explain whether an early security ending can complete an unresolved test."""
    if not terminal:
        return {
            "category": None,
            "label": "No early trading end recorded",
            "counts_as_failure": False,
            "excluded_from_success_rate": False,
            "counting_reason": "No early trading end affected this result.",
            "event_date": None,
            "successor": None,
        }
    action = str(terminal.get("action") or "delisted").lower().replace("_", "")
    category, text = _TERMINAL_REASON.get(
        action, ("unclassified_trading_end", "The available price history ended for an unconfirmed reason.")
    )
    zero_value = terminal.get("terminal_return_policy") == "zero"
    counts_as_failure = category == "bankruptcy_or_liquidation" or zero_value
    if counts_as_failure:
        counting_reason = (
            "Counted as a failed target because the recorded event ended the investment "
            "with no continuing value."
        )
    elif category in {"acquired", "merged"}:
        counting_reason = (
            "Not counted in the target success percentage because the original stock "
            "ended through an acquisition or merger before the required period was observed."
        )
    else:
        counting_reason = (
            "Not counted in the target success percentage because trading ended before "
            "the required period and a final failure cannot be proved."
        )
    return {
        "category": category,
        "label": text,
        "counts_as_failure": counts_as_failure,
        "excluded_from_success_rate": not counts_as_failure,
        "counting_reason": counting_reason,
        "event_date": str(terminal.get("date") or "")[:10] or None,
        "successor": terminal.get("successor"),
        "action": terminal.get("action"),
    }


def _terminal_explanation(terminal: dict | None) -> dict | None:
    if not terminal:
        return None
    policy = _terminal_outcome_policy(terminal)
    code, text = policy["category"], policy["label"]
    event_date = str(terminal.get("date") or "")[:10] or None
    successor = terminal.get("successor")
    details = text
    if event_date:
        details += f" Last available or recorded event date: {event_date}."
    if successor:
        details += f" Recorded successor: {successor}."
    details += f" {policy['counting_reason']}"
    return {"code": code, "short": text, "details": details,
            "event_date": event_date, "successor": successor,
            "counted_in_success_rate": policy["counts_as_failure"]}


# A "never_within_three_years" verdict is decided over the full 3-year window,
# so its evidence must report the 3-year peak, not the first-year peak used by
# every other status. Using the wrong horizon here understated how close (or
# how far) these stocks actually got before the episode was called a miss.
_FIXED_MISS_HORIZON_DAYS = {"never_within_three_years": 1095}

_FIXED_MISS_CATEGORIES = (
    (0, "stayed_at_or_below_entry", "Never traded above the entry price"),
    (15, "modest_gain_short_of_target", "Gained, but stayed well below the target"),
    (30, "close_but_short_of_target", "Came close, but never closed at or above the target"),
)

# Controlled vocabulary for causal research.  A phrase is attached to a failed
# target only when this replay contains supporting evidence; event-driven causes
# remain available as research codes but are never guessed from price action.
TARGET_FAILURE_REASON_CATALOG = (
    {"code": "growth_below_expectations", "phrase": "Growth slowed relative to expectations", "category": "growth"},
    {"code": "earnings_or_guidance_disappointment", "phrase": "Earnings or guidance disappointed", "category": "growth"},
    {"code": "valuation_too_high", "phrase": "Valuation was previously too high", "category": "valuation"},
    {"code": "dilution_debt_or_holder_selling", "phrase": "Share dilution, debt, or major-holder selling created pressure", "category": "survival"},
    {"code": "operational_or_technology_disruption", "phrase": "Shutdown, component shortage, regulatory issue, or technology transition", "category": "risk"},
    {"code": "insufficient_cash_runway", "phrase": "Cash was insufficient relative to burn, liabilities, or required investment", "category": "survival"},
    {"code": "sector_multiple_compression", "phrase": "Sector-wide change reduced valuation multiples", "category": "valuation"},
    {"code": "adverse_government_policy", "phrase": "Adverse government policy", "category": "catalysts"},
    {"code": "external_event", "phrase": "Natural disaster, war, pandemic, or other external event", "category": "risk"},
    {"code": "target_overly_optimistic", "phrase": "Target price was overly optimistic", "category": "valuation"},
    {"code": "company_specific_underperformance", "phrase": "Company-specific underperformance versus the benchmark", "category": "confirmation"},
    {"code": "legal_or_litigation_pressure", "phrase": "Legal settlement or continuing litigation created pressure", "category": "risk"},
    {"code": "cause_not_verified", "phrase": "Cause not verified from available point-in-time evidence", "category": "risk"},
)


def _failure_research() -> dict:
    path = Path(__file__).resolve().parents[2] / "config" / "target_failure_research.json"
    try:
        return (json.loads(path.read_text(encoding="utf-8")).get("entries") or {})
    except (OSError, ValueError, TypeError):
        return {}


def _category_score(row: dict, category_id: str):
    for category in row.get("categories") or []:
        if category.get("id") == category_id:
            value = category.get("score")
            return value if isinstance(value, (int, float)) else None
    return None


def _target_failure_reasons(row: dict, *, horizon_days: int = 1095) -> list[dict]:
    """Assign only evidence-supported phrases to a confirmed failed target."""
    catalog = {item["code"]: item for item in TARGET_FAILURE_REASON_CATALOG}
    researched = _failure_research().get(str(row.get("ticker") or "").upper())
    if researched:
        code = researched.get("code") or "cause_not_verified"
        base = catalog.get(code, catalog["cause_not_verified"])
        return [{
            **base,
            "category": researched.get("category") or base["category"],
            "phrase": researched.get("reason") or base["phrase"],
            "evidence": "Company filing or authoritative source reviewed for the episode window.",
            "evidence_status": "researched",
            "sources": researched.get("sources") or [],
        }]
    found = []

    def add(code, evidence):
        if code not in {item["code"] for item in found}:
            found.append({**catalog[code], "evidence": evidence, "evidence_status": "observed"})

    valuation = _category_score(row, "valuation")
    growth = _category_score(row, "growth")
    survival = _category_score(row, "survival")
    if isinstance(valuation, (int, float)) and valuation < 40:
        add("valuation_too_high", f"Frozen Valuation category score was {valuation:.1f}/100.")
    if isinstance(growth, (int, float)) and growth < 40:
        add("growth_below_expectations", f"Frozen Growth category score was {growth:.1f}/100.")
    if isinstance(survival, (int, float)) and survival < 40:
        add("dilution_debt_or_holder_selling", f"Frozen Financial Survival category score was {survival:.1f}/100.")
    market = _benchmark_context(row, horizon_days)
    if market:
        benchmark = market.get("benchmark_return_pct")
        alpha = market.get("alpha_pct")
        if isinstance(benchmark, (int, float)) and benchmark <= -10:
            add("sector_multiple_compression", f"Broad benchmark return was {benchmark:.1f}% over the same period; sector attribution still needs review.")
        if isinstance(alpha, (int, float)) and alpha <= -10:
            add("company_specific_underperformance", f"The stock trailed the benchmark by {abs(alpha):.1f} percentage points.")
    practical = ((row.get("outcome") or {}).get("targets") or {}).get("practical") or {}
    if isinstance(practical.get("upside_pct"), (int, float)) and practical["upside_pct"] > 70:
        add("target_overly_optimistic", f"The frozen target required a {practical['upside_pct']:.1f}% gain.")
    if not found:
        add("cause_not_verified", "The replay proves the miss, but does not contain reliable causal event data.")
    return found


def _fixed_miss_category(max_gain: float | None) -> tuple[str, str] | None:
    """Bucket a confirmed miss by how far the price actually got, so misses
    can be filtered/grouped by cause rather than read one at a time."""
    if not isinstance(max_gain, (int, float)):
        return None
    for ceiling, code, label in _FIXED_MISS_CATEGORIES:
        if max_gain <= ceiling:
            return (code, label)
    return ("near_target_no_qualifying_close",
            "Came within reach of the target but never on a qualifying close")


def _benchmark_context(root: dict | None, horizon_days: int) -> dict | None:
    """Benchmark return/alpha already computed for this episode's entry, at the
    same horizon used to judge the miss, so 'why' can note a market-wide
    headwind versus stock-specific underperformance."""
    if not root:
        return None
    entry = ((root.get("outcome") or {}).get("horizons") or {}).get(str(horizon_days))
    if not entry or not isinstance(entry.get("alpha_pct"), (int, float)):
        return None
    return {
        "horizon_days": horizon_days,
        "benchmark_return_pct": entry.get("benchmark_return_pct"),
        "alpha_pct": entry.get("alpha_pct"),
    }


def _fixed_goal_reason(status: str, days, milestone: dict, terminal: dict | None,
                       root: dict | None = None) -> dict:
    horizon = _FIXED_MISS_HORIZON_DAYS.get(status, 365)
    window_label = (f"{horizon // 365} year{'s' if horizon // 365 != 1 else ''}"
                    if horizon >= 365 else f"{horizon} days")
    max_gain = (milestone.get("max_return_pct_by_horizon") or {}).get(str(horizon))
    drawdown = (milestone.get("max_drawdown_pct_by_horizon") or {}).get(str(horizon))
    observed = milestone.get("last_observed_days")
    observed_label = (
        f"during {int(observed)} observed days"
        if isinstance(observed, (int, float)) and observed < horizon else f"during {window_label}"
    )
    evidence = [
        {"label": f"Highest gain {observed_label}", "value": max_gain, "unit": "%"},
        {"label": f"Largest fall {observed_label}", "value": drawdown, "unit": "%"},
    ]
    if status == "within_one_year":
        return {"code": "reached_on_time", "short": "Reached within one year",
                "details": f"The price first reached the +30% goal after {int(days)} days.",
                "evidence": evidence}
    if isinstance(days, (int, float)):
        return {"code": "reached_late", "short": f"Reached late after {int(days)} days",
                "details": ("The stock did not reach +30% during the promised first year, "
                            f"but it eventually reached it after {int(days)} days."),
                "evidence": evidence}
    terminal_reason = _terminal_explanation(terminal)
    if terminal_reason and status in {"closed_early_excluded", "closed_early_failure"}:
        return {**terminal_reason, "evidence": evidence}
    if status == "still_waiting":
        observed = milestone.get("last_observed_days")
        return {"code": "still_waiting", "short": "Not enough time has passed",
                "details": (f"Only {int(observed)} days of later prices are available. "
                            "This episode is still waiting for a full result.")
                if isinstance(observed, (int, float)) else
                "There is not enough later price history for a final result.",
                "evidence": evidence}
    if status == "missed_within_one_year_tracking":
        # The promised one-year deadline has already passed and failed — this is
        # already counted as a miss in the +30%-within-one-year headline rate.
        # It is only "waiting" on a possible (non-qualifying) late catch-up.
        category = _fixed_miss_category(max_gain)
        shortfall = round(30 - max_gain, 2) if isinstance(max_gain, (int, float)) else None
        details = (
            f"The +30% goal was not reached within the promised first year. Its highest "
            f"gain {observed_label} was {max_gain:.2f}%, {shortfall:.2f} percentage points "
            "short of the goal. This episode is already counted as a miss in the one-year "
            "result and is now only being tracked for a possible late catch-up, which would "
            "not count as an on-time success."
            if shortfall is not None else
            "The +30% goal was not reached within the promised first year. This episode is "
            "already counted as a miss in the one-year result and is now only being tracked "
            "for a possible late catch-up, which would not count as an on-time success."
        )
        return {"code": "missed_first_year_tracking",
                "short": "Missed within one year; tracking for a late catch-up",
                "category": category[0] if category else None,
                "details": details, "evidence": evidence}
    category = _fixed_miss_category(max_gain)
    shortfall = round(30 - max_gain, 2) if isinstance(max_gain, (int, float)) else None
    market = _benchmark_context(root, horizon)
    details = (
        f"Its highest gain {observed_label} was {max_gain:.2f}%, "
        f"which was {shortfall:.2f} percentage points short of the goal."
        if shortfall is not None else
        f"The available prices {observed_label} never reached the +30% goal."
    )
    if market:
        alpha = market["alpha_pct"]
        bench = market.get("benchmark_return_pct")
        if alpha < 0 and isinstance(bench, (int, float)):
            details += (f" Over the same {window_label}, the stock also underperformed the "
                        f"benchmark by {abs(alpha):.1f} points (benchmark return {bench:.1f}%).")
        elif isinstance(bench, (int, float)):
            details += (f" The benchmark returned {bench:.1f}% over the same {window_label}; "
                        "this stock still fell short of its own target.")
    return {"code": "fell_short",
            "short": category[1] if category else "Did not reach +30% within the observed period",
            "category": category[0] if category else None,
            "details": details, "evidence": evidence, "market_context": market}


def _practical_goal_reason(target: dict, terminal: dict | None) -> dict:
    status = target.get("status") or "unavailable"
    days = target.get("days_to_target")
    deadline = target.get("deadline_days")
    gain = target.get("highest_gain_before_deadline_pct")
    drawdown = target.get("max_drawdown_before_result_pct")
    evidence = [
        {"label": "Practical Target gain", "value": target.get("upside_pct"), "unit": "%"},
        {"label": "Promised time", "value": deadline, "unit": " days"},
        {"label": "Highest gain before deadline", "value": gain, "unit": "%"},
        {"label": "Largest fall before result", "value": drawdown, "unit": "%"},
    ]
    if status in {"reached", "already_above_at_entry"}:
        return {"code": "reached_on_time", "short": "Reached within the promised time",
                "details": ("The entry price was already above this reference value."
                            if status == "already_above_at_entry" else
                            f"The price first reached the Practical Target after {int(days)} days."),
                "evidence": evidence}
    if status == "reached_late":
        return {"code": "reached_late", "short": f"Reached late after {int(days)} days",
                "details": (f"The price reached the Practical Target after {int(days)} days, "
                            "which was later than its promised time."), "evidence": evidence}
    if status == "unavailable":
        reason = target.get("reason") or "No credible Practical Target could be calculated."
        return {"code": "target_unavailable", "short": "No credible Practical Target",
                "details": reason, "evidence": evidence}
    terminal_reason = _terminal_explanation(terminal)
    terminal_days = target.get("terminal_days")
    observed_days = target.get("last_observed_days")
    ended_early = (
        isinstance(terminal_days, (int, float))
        and isinstance(deadline, (int, float))
        and terminal_days <= deadline
        and not (isinstance(observed_days, (int, float)) and observed_days >= deadline)
    )
    if terminal_reason and ended_early and status in {
        "closed_early_excluded", "closed_early_failure", "expired"
    }:
        return {**terminal_reason, "evidence": evidence}
    if status == "active":
        observed = target.get("last_observed_days")
        return {"code": "still_waiting", "short": "Still waiting for the deadline",
                "details": (f"Only {int(observed)} days have been observed; the promised "
                            f"time is {int(deadline)} days.")
                if isinstance(observed, (int, float)) and isinstance(deadline, (int, float)) else
                "The promised time has not ended yet.", "evidence": evidence}
    target_gain = target.get("upside_pct")
    shortfall = (round(float(target_gain) - float(gain), 2)
                 if isinstance(target_gain, (int, float)) and isinstance(gain, (int, float))
                 else None)
    return {"code": "fell_short", "short": "Did not reach the Practical Target in time",
            "details": (f"Its highest gain before the deadline was {gain:.2f}%, "
                        f"which was {shortfall:.2f} percentage points short of the target.")
            if shortfall is not None else
            "The available prices never reached the Practical Target before its deadline.",
            "evidence": evidence}


def _fixed_horizon_evaluation(row: dict, threshold: int, horizon: int) -> dict:
    milestone = row.get("return_milestones") or {}
    first = (milestone.get("first_hit_days") or {}).get(str(threshold))
    observed_days = milestone.get("last_observed_days")
    terminal_days = milestone.get("terminal_days")
    terminal_policy = _terminal_outcome_policy(row.get("terminal_event"))
    hit = isinstance(first, (int, float)) and first <= horizon
    full_period = isinstance(observed_days, (int, float)) and observed_days >= horizon
    ended_early = (
        isinstance(terminal_days, (int, float))
        and terminal_days <= horizon
        and not full_period
    )
    counted_failure = ended_early and terminal_policy["counts_as_failure"]
    excluded = ended_early and not terminal_policy["counts_as_failure"] and not hit
    counted = hit or full_period or counted_failure
    if hit:
        result = "success"
        reason = "Counted as a success because the gain was reached within the required time."
    elif full_period:
        result = "failure"
        reason = "Counted as a failure because the full required period was observed without reaching the gain."
    elif counted_failure:
        result = "failure"
        reason = terminal_policy["counting_reason"]
    elif excluded:
        result = "excluded"
        reason = terminal_policy["counting_reason"]
    else:
        result = "active"
        reason = "Not counted yet because the required period has not finished."
    start = date.fromisoformat(str(row.get("entry_date"))[:10])
    return {
        "result": result,
        "counted_in_success_rate": counted,
        "excluded_from_success_rate": excluded,
        "counting_reason": reason,
        "observed_days": observed_days,
        "required_days": horizon,
        "deadline_date": (start + timedelta(days=horizon)).isoformat(),
        "last_observed_date": milestone.get("last_observed_date"),
        "terminal_days": terminal_days,
        "terminal_policy": terminal_policy,
    }


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
        terminal = root.get("terminal_event")
        terminal_policy = _terminal_outcome_policy(terminal)
        fixed_evaluation = _fixed_horizon_evaluation(root, 30, 365)
        observed_days = milestone.get("last_observed_days")
        terminal_days = milestone.get("terminal_days")
        full_three_years = isinstance(observed_days, (int, float)) and observed_days >= 1095
        ended_before_three_years = (
            isinstance(terminal_days, (int, float)) and terminal_days < 1095
            and not full_three_years
        )
        # The primary +30%-within-one-year test can already be a concluded miss
        # (365+ days observed, never reached) while the broader 3-year window is
        # still open. That is a different situation from genuinely too little
        # time having passed, and must not be labeled "not enough time".
        missed_first_year = isinstance(observed_days, (int, float)) and observed_days >= 365
        fixed_status = (
            "within_one_year" if isinstance(days_to_30, (int, float)) and days_to_30 <= 365 else
            "during_year_two" if isinstance(days_to_30, (int, float)) and days_to_30 <= 730 else
            "during_year_three" if isinstance(days_to_30, (int, float)) and days_to_30 <= 1095 else
            "after_three_years" if isinstance(days_to_30, (int, float)) else
            "never_within_three_years" if full_three_years else
            "closed_early_failure" if ended_before_three_years and terminal_policy["counts_as_failure"] else
            "closed_early_excluded" if ended_before_three_years else
            "missed_within_one_year_tracking" if missed_first_year else
            "still_waiting"
        )
        entry_price = (float(root["entry_price"])
                       if isinstance(root.get("entry_price"), (int, float)) else None)
        practical_summary = {
            "price": practical.get("price"),
            "upside_pct": practical.get("upside_pct"),
            "selected_method": practical.get("selected_method"),
            "basis": practical.get("basis"),
            "reason": practical.get("reason"),
            **practical_test,
        } if practical else None
        if practical_summary:
            practical_deadline = practical_summary.get("deadline_days")
            practical_observed = practical_summary.get("last_observed_days")
            practical_terminal = practical_summary.get("terminal_days")
            practical_ended_early = (
                isinstance(practical_deadline, (int, float))
                and isinstance(practical_terminal, (int, float))
                and practical_terminal <= practical_deadline
                and not (isinstance(practical_observed, (int, float))
                         and practical_observed >= practical_deadline)
            )
            if (practical_ended_early
                    and practical_summary.get("status") == "expired"
                    and not terminal_policy["counts_as_failure"]):
                practical_summary["status"] = "closed_early_excluded"
                practical_summary["counted_in_success_rate"] = False
                practical_summary["counting_reason"] = terminal_policy["counting_reason"]
        fixed_reason = _fixed_goal_reason(
            fixed_status, days_to_30, milestone, terminal, root
        )
        if practical_summary:
            practical_summary["result_reason"] = _practical_goal_reason(
                practical_summary, terminal
            )
        target_failed = (
            fixed_status in {"never_within_three_years", "closed_early_failure"}
            or bool(practical_summary and practical_summary.get("status") == "expired")
        )
        failure_reasons = _target_failure_reasons(root) if target_failed else []
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
            "fixed_30_reason": fixed_reason,
            "fixed_30_miss_category": fixed_reason.get("category"),
            "target_failure_reasons": failure_reasons,
            "fixed_30_evaluation": fixed_evaluation,
            "practical_target": practical_summary,
            "terminal_event": terminal,
            "terminal_evaluation": terminal_policy if terminal else None,
            "currency_conversion": root.get("currency_conversion"),
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
            completed, reached, excluded, days = [], [], [], []
            for row in rows:
                milestone = row.get("return_milestones") or {}
                first = (milestone.get("first_hit_days") or {}).get(str(threshold))
                hit = isinstance(first, (int, float)) and first <= horizon
                evaluation = _fixed_horizon_evaluation(row, threshold, horizon)
                if evaluation["excluded_from_success_rate"]:
                    excluded.append(row)
                if evaluation["counted_in_success_rate"]:
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
                "active": len(rows) - total - len(excluded),
                "excluded": len(excluded),
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
            "excluded": sum(row.get("status") == "closed_early_excluded" for row in band),
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
            "Reached observations count immediately. Otherwise the full horizon must be observed. "
            "Bankruptcy with no continuing value counts as a failure; acquisitions, mergers and "
            "unconfirmed early trading endings are shown separately and are not included in the success percentage."
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
                            "after_three_years", "never_within_three_years",
                            "closed_early_failure", "closed_early_excluded",
                            "missed_within_one_year_tracking", "still_waiting")
            },
            "fixed_30_miss_breakdown": {
                "basis": "Buy episodes with fixed_30_status == never_within_three_years "
                         "(the full 3-year window observed, target never reached).",
                "counts": {
                    key: sum(row.get("fixed_30_miss_category") == key for row in details)
                    for key in ("stayed_at_or_below_entry", "modest_gain_short_of_target",
                                "close_but_short_of_target", "near_target_no_qualifying_close")
                },
            },
            "target_failure_reason_analysis": {
                "basis": "Controlled reasons appear only on Buy episodes with a confirmed failed fixed or Practical Target. Event causes are never inferred without point-in-time evidence.",
                "catalog": list(TARGET_FAILURE_REASON_CATALOG),
                "counts": {
                    item["code"]: sum(
                        any(item["code"] in (reason.get("codes") or [reason.get("code")])
                            for reason in (row.get("target_failure_reasons") or []))
                        for row in details
                    )
                    for item in TARGET_FAILURE_REASON_CATALOG
                },
            },
            "practical_target_with_compounding_deadline": {
                "observations": len(practical_rows),
                "reached": len(practical_reached),
                "reached_late": len(practical_late),
                "evaluable": len(practical_completed),
                "expired": sum(row.get("status") == "expired" for row in practical_completed),
                "active": sum(row.get("status") == "active" for row in practical_rows),
                "excluded": sum(row.get("status") == "closed_early_excluded"
                                for row in practical_rows),
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
