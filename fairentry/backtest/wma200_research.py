"""Research-only 200-week moving-average proximity study.

The study asks whether fundamentally strong companies observed near their
point-in-time 200-week average subsequently bounced and reached fixed return
targets more often than equally strong companies observed away from it.  It
never changes a score or verdict.
"""
from __future__ import annotations

from datetime import date
import math
import statistics

from fairentry.backtest.evidence import _fixed_horizon_evaluation
from fairentry.backtest.research_cycle import _number, _wilson


STRONG_BUSINESS_RULE = {
    "quality_min": 70,
    "financial_strength_min": 70,
    "growth_min": 70,
    "no_tested_veto": True,
}
PROXIMITY_THRESHOLDS_PCT = (3.0, 5.0, 10.0)
TARGETS_PCT = (10, 20, 25, 30)


def _category_score(row: dict, category_id: str):
    for category in row.get("categories", []):
        if category.get("id") == category_id:
            return _number(category.get("score"))
    return None


def _distance(row: dict):
    return _number((row.get("research_factors") or {}).get("dist_200wma_pct"))


def _strong_business(row: dict) -> bool:
    quality = _category_score(row, "quality")
    survival = _category_score(row, "survival")
    growth = _category_score(row, "growth")
    return (
        quality is not None and quality >= STRONG_BUSINESS_RULE["quality_min"]
        and survival is not None
        and survival >= STRONG_BUSINESS_RULE["financial_strength_min"]
        and growth is not None and growth >= STRONG_BUSINESS_RULE["growth_min"]
        and not row.get("vetoes")
    )


def attach_wma200_factors(observations: list[dict], connection) -> dict:
    """Attach a point-in-time 1,000-session (~200-week) distance to observations."""
    rows = [
        row for row in observations
        if row.get("observation_id") and row.get("ticker") and row.get("decision_date")
        and _distance(row) is None
    ]
    if not rows:
        return {"observations": len(observations), "enriched": 0, "already_present": True}

    import pandas as pd

    frame = pd.DataFrame({
        "observation_id": [row["observation_id"] for row in rows],
        "ticker": [row["ticker"] for row in rows],
        "decision_date": [row["decision_date"] for row in rows],
    })
    connection.register("wma200_observations", frame)
    try:
        result = connection.execute("""
        SELECT o.observation_id,p.close,p.wma200_proxy,p.history_sessions
        FROM wma200_observations o
        LEFT JOIN LATERAL (
          SELECT close,wma200_proxy,history_sessions
          FROM sfa_price_features p
          WHERE p.ticker=o.ticker AND p.date<=CAST(o.decision_date AS DATE)
          ORDER BY p.date DESC LIMIT 1
        ) p ON true
        """).fetchall()
    finally:
        connection.unregister("wma200_observations")

    by_id = {}
    for observation_id, close, wma, sessions in result:
        close, wma, sessions = _number(close), _number(wma), _number(sessions)
        distance = None
        if close is not None and wma not in {None, 0} and (sessions or 0) >= 900:
            distance = round((close / wma - 1) * 100, 4)
        by_id[observation_id] = distance

    enriched = 0
    for row in rows:
        value = by_id.get(row["observation_id"])
        if value is None:
            continue
        row.setdefault("research_factors", {})["dist_200wma_pct"] = value
        enriched += 1
    return {"observations": len(rows), "enriched": enriched, "already_present": False}


def _event_roots(observations: list[dict], predicate, max_gap_days: int) -> list[dict]:
    """Count the first observation when an issuer enters a qualifying state."""
    grouped: dict[str, list[dict]] = {}
    for row in observations:
        key = row.get("issuer_key") or row.get("security_id") or row.get("ticker")
        if key and row.get("entry_date"):
            grouped.setdefault(str(key), []).append(row)

    events = []
    for rows in grouped.values():
        rows.sort(key=lambda row: str(row.get("entry_date")))
        active = False
        previous_date = None
        for row in rows:
            current_date = date.fromisoformat(str(row["entry_date"])[:10])
            if previous_date is not None and (current_date - previous_date).days > max_gap_days:
                active = False
            qualifies = bool(predicate(row))
            if qualifies and not active:
                events.append(row)
            active = qualifies
            previous_date = current_date
    return sorted(events, key=lambda row: str(row.get("entry_date")))


def _target_summary(events: list[dict], threshold: int, horizon: int) -> dict:
    reached = failed = active = excluded = 0
    days, drawdowns = [], []
    for row in events:
        evaluation = _fixed_horizon_evaluation(row, threshold, horizon)
        result = evaluation.get("result")
        if result == "success":
            reached += 1
            hit_days = _number(
                ((row.get("return_milestones") or {}).get("first_hit_days") or {}).get(str(threshold))
            )
            if hit_days is not None:
                days.append(hit_days)
        elif result == "failure":
            failed += 1
        elif result == "active":
            active += 1
        else:
            excluded += 1
        drawdown = _number(
            ((row.get("return_milestones") or {})
             .get("max_drawdown_pct_by_horizon", {}).get(str(horizon)))
        )
        if result in {"success", "failure"} and drawdown is not None:
            drawdowns.append(drawdown)
    completed = reached + failed
    rate = round(reached / completed * 100, 1) if completed else None
    return {
        "target_pct": threshold,
        "horizon_days": horizon,
        "events": len(events),
        "completed_events": completed,
        "reached": reached,
        "failed": failed,
        "active": active,
        "excluded": excluded,
        "success_rate_pct": rate,
        "success_rate_ci90_pct": list(_wilson(reached, completed)) if completed else None,
        "median_days_to_hit": round(statistics.median(days), 1) if days else None,
        "median_max_drawdown_pct": round(statistics.median(drawdowns), 1) if drawdowns else None,
        "worst_max_drawdown_pct": round(min(drawdowns), 1) if drawdowns else None,
    }


def _cohort_summary(events: list[dict]) -> dict:
    distances = [_distance(row) for row in events if _distance(row) is not None]
    targets = {str(target): _target_summary(events, target, 365) for target in TARGETS_PCT}
    bounce = _target_summary(events, 10, 90)
    return {
        "events": len(events),
        "unique_issuers": len({row.get("issuer_key") or row.get("ticker") for row in events}),
        "median_distance_pct": round(statistics.median(distances), 2) if distances else None,
        "bounce_definition": "+10% within 90 calendar days from the observed entry close",
        "bounce": bounce,
        "one_year_targets": targets,
    }


def run_wma200_research(observations: list[dict], *, step_days: int = 30) -> dict:
    """Compare strong-business Buy observations near and away from the 200-week mean."""
    max_gap = max(step_days + 1, int(math.ceil(step_days * 1.5)))
    # The SFA artifact retains full forward daily evidence only for recorded Buy
    # observations. Watch/Avoid names remain eligible for the live scanner, but
    # cannot be included in target probabilities without inventing outcomes.
    available = [
        row for row in observations
        if row.get("verdict") == "Buy"
        and _distance(row) is not None
        and (row.get("return_milestones") or {}).get("status") == "observed"
    ]
    strong = [row for row in available if _strong_business(row)]
    rows = []
    for threshold in PROXIMITY_THRESHOLDS_PCT:
        near = _event_roots(
            available,
            lambda row, limit=threshold: _strong_business(row)
            and abs(_distance(row)) <= limit,
            max_gap,
        )
        away = _event_roots(
            available,
            lambda row, limit=threshold: _strong_business(row)
            and abs(_distance(row)) > limit,
            max_gap,
        )
        near_summary, away_summary = _cohort_summary(near), _cohort_summary(away)
        near_30 = near_summary["one_year_targets"]["30"]
        away_30 = away_summary["one_year_targets"]["30"]
        improvement = None
        if near_30["success_rate_pct"] is not None and away_30["success_rate_pct"] is not None:
            improvement = round(near_30["success_rate_pct"] - away_30["success_rate_pct"], 1)
        rows.append({
            "threshold_pct": threshold,
            "near": near_summary,
            "away": away_summary,
            "near_minus_away_30pct_pp": improvement,
        })

    primary = rows[0]
    primary_30 = primary["near"]["one_year_targets"]["30"]
    checks = {
        "enough_completed_events": primary_30["completed_events"] >= 50,
        "minimum_success_rate": (
            primary_30["success_rate_pct"] is not None
            and primary_30["success_rate_pct"] >= 55
        ),
        "meaningful_improvement": (
            primary["near_minus_away_30pct_pp"] is not None
            and primary["near_minus_away_30pct_pp"] >= 5
        ),
    }
    return {
        "ok": True,
        "version": 1,
        "objective": "Test whether strong-business Buy entries near their point-in-time 200-week moving average bounce and reach fixed targets more often.",
        "information_boundary": "Business scores and the 1,000-session moving average are frozen on or before the observation date.",
        "production_effect": "none",
        "strong_business_rule": STRONG_BUSINESS_RULE,
        "moving_average_definition": "Average of the latest 1,000 split-adjusted trading-session closes (approximately 200 weeks); at least 900 sessions required.",
        "event_definition": "First historical Buy observation entering each proximity state; contiguous Buy observations count as one event.",
        "observations_with_wma": len(available),
        "strong_business_observations": len(strong),
        "threshold_comparison": rows,
        "primary_threshold_pct": PROXIMITY_THRESHOLDS_PCT[0],
        "research_checks": checks,
        "research_signal": all(checks.values()),
        "interpretation": "This is a research candidate, not a Buy rule. A result must remain stable in chronological unseen periods before affecting scoring.",
    }
