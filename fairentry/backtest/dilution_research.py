"""Point-in-time research for a substantial share-dilution hard veto."""

from __future__ import annotations

from datetime import date, timedelta
import math
import statistics


DILUTION_THRESHOLDS_PCT = (5.0, 10.0, 15.0, 20.0)
SELECTED_DILUTION_VETO_PCT = 10.0


def dilution_yoy_pct(observation: dict) -> float | None:
    """Return the frozen share-count YoY value from an observation."""
    for category in observation.get("categories", []):
        if category.get("id") != "survival":
            continue
        for item in category.get("items", []):
            if item.get("id") != "dilution":
                continue
            value = item.get("actual")
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                return float(value)
    factors = observation.get("research_factors") or {}
    value = factors.get("share_count_change_yoy_pct")
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _terminal_failure(observation: dict) -> bool:
    event = observation.get("terminal_event") or {}
    action = str(event.get("action") or "").lower()
    policy = str(event.get("terminal_return_policy") or "").lower()
    return policy == "zero" or "bankrupt" in action or "liquidat" in action


def _episode_result(observation: dict) -> dict:
    milestone = observation.get("return_milestones") or {}
    tuning = observation.get("_tuning_outcome") or {}
    hit_days = (
        (milestone.get("first_hit_days") or {}).get("30")
        if milestone
        else (tuning.get("first_hit_days_by_target") or {}).get("30")
    )
    observed_days = (
        milestone.get("last_observed_days")
        if milestone
        else tuning.get("last_observed_days")
    )
    success = isinstance(hit_days, (int, float)) and hit_days <= 365
    completed = (
        success
        or (isinstance(observed_days, (int, float)) and observed_days >= 365)
        or _terminal_failure(observation)
    )
    return {
        "issuer_key": observation.get("issuer_key") or observation.get("ticker"),
        "ticker": observation.get("ticker"),
        "entry_date": observation.get("entry_date"),
        "success": success,
        "completed": completed,
        "days_to_hit": int(hit_days) if success else None,
        "drawdown_pct": (
            (milestone.get("max_drawdown_pct_by_horizon") or {}).get("365")
            if milestone
            else tuning.get("max_drawdown_pct")
        ),
        "dilution_yoy_pct": dilution_yoy_pct(observation),
    }


def _episodes(
    observations: list[dict], threshold_pct: float | None, max_gap_days: int
) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for observation in observations:
        key = observation.get("issuer_key") or observation.get("security_id") or observation.get("ticker")
        if key and observation.get("entry_date"):
            grouped.setdefault(str(key), []).append(observation)

    episodes: list[dict] = []
    for rows in grouped.values():
        root = None
        prior_date = None
        for observation in sorted(rows, key=lambda row: row["entry_date"]):
            current_date = date.fromisoformat(str(observation["entry_date"])[:10])
            if prior_date is not None and (current_date - prior_date).days > max_gap_days:
                if root is not None:
                    episodes.append(_episode_result(root))
                root = None
            dilution = dilution_yoy_pct(observation)
            vetoed = (
                threshold_pct is not None
                and dilution is not None
                and dilution > threshold_pct
            )
            baseline_verdict = observation.get("pre_dilution_verdict") or observation.get("verdict")
            eligible_buy = (
                baseline_verdict == "Buy"
                and (observation.get("return_milestones") or observation.get("_tuning_outcome"))
                and not vetoed
            )
            if eligible_buy:
                if root is None:
                    root = observation
            elif root is not None:
                episodes.append(_episode_result(root))
                root = None
            prior_date = current_date
        if root is not None:
            episodes.append(_episode_result(root))
    return sorted(episodes, key=lambda row: (row["entry_date"], row.get("ticker") or ""))


def _summary(episodes: list[dict], start: str | None = None, end: str | None = None) -> dict:
    selected = [
        row for row in episodes
        if (start is None or row["entry_date"] >= start)
        and (end is None or row["entry_date"] <= end)
    ]
    completed = [row for row in selected if row["completed"]]
    successes = [row for row in completed if row["success"]]
    drawdowns = [
        float(row["drawdown_pct"])
        for row in completed
        if isinstance(row.get("drawdown_pct"), (int, float))
    ]
    hit_days = [row["days_to_hit"] for row in successes if row["days_to_hit"] is not None]
    return {
        "episodes": len(selected),
        "completed": len(completed),
        "successes": len(successes),
        "failures": len(completed) - len(successes),
        "success_rate_pct": round(len(successes) / len(completed) * 100, 1) if completed else None,
        "median_days_to_30": round(statistics.median(hit_days), 1) if hit_days else None,
        "median_drawdown_pct": round(statistics.median(drawdowns), 1) if drawdowns else None,
        "worst_drawdown_pct": round(min(drawdowns), 1) if drawdowns else None,
        "unique_issuers": len({row["issuer_key"] for row in completed}),
    }


def run_dilution_veto_research(
    observations: list[dict],
    *,
    step_days: int = 30,
    thresholds_pct: tuple[float, ...] = DILUTION_THRESHOLDS_PCT,
    selected_threshold_pct: float = SELECTED_DILUTION_VETO_PCT,
    production_effect: str = "research_only",
) -> dict:
    """Compare frozen dilution vetoes without changing the supplied verdicts."""
    max_gap_days = max(step_days + 1, int(math.ceil(step_days * 1.5)))
    baseline = _episodes(observations, None, max_gap_days)
    entry_dates = sorted({row["entry_date"] for row in baseline})
    if entry_dates:
        validation_start = entry_dates[min(len(entry_dates) - 1, int(len(entry_dates) * .60))]
        final_start = entry_dates[min(len(entry_dates) - 1, int(len(entry_dates) * .80))]
    else:
        validation_start = final_start = None

    baseline_summary = _summary(baseline)
    baseline_final = _summary(baseline, start=final_start)
    buy_rows = [
        row for row in observations
        if (row.get("pre_dilution_verdict") or row.get("verdict")) == "Buy"
    ]
    missing_dilution = sum(dilution_yoy_pct(row) is None for row in buy_rows)
    variants = []
    for threshold in thresholds_pct:
        episodes = _episodes(observations, float(threshold), max_gap_days)
        full = _summary(episodes)
        development = _summary(
            episodes,
            end=(validation_start if validation_start is None else
                 (date.fromisoformat(validation_start) - timedelta(days=1)).isoformat()),
        )
        validation = _summary(
            episodes,
            start=validation_start,
            end=(final_start if final_start is None else
                 (date.fromisoformat(final_start) - timedelta(days=1)).isoformat()),
        )
        final = _summary(episodes, start=final_start)
        removed_signals = sum(
            dilution_yoy_pct(row) is not None and dilution_yoy_pct(row) > threshold
            for row in buy_rows
        )
        variants.append({
            "threshold_pct": threshold,
            "rule": f"Force Avoid when point-in-time share-count growth is greater than {threshold:g}% year over year.",
            "removed_buy_signals": removed_signals,
            "full_history": full,
            "development": development,
            "validation": validation,
            "final_unseen": final,
            "full_history_change_pp": (
                round(full["success_rate_pct"] - baseline_summary["success_rate_pct"], 1)
                if None not in (full["success_rate_pct"], baseline_summary["success_rate_pct"])
                else None
            ),
            "final_unseen_change_pp": (
                round(final["success_rate_pct"] - baseline_final["success_rate_pct"], 1)
                if None not in (final["success_rate_pct"], baseline_final["success_rate_pct"])
                else None
            ),
        })

    selected = next(
        (row for row in variants if row["threshold_pct"] == selected_threshold_pct),
        None,
    )
    return {
        "version": 1,
        "objective": "+30% within one year",
        "factor": "Point-in-time year-over-year share-count change",
        "information_boundary": "Only share-count data filed and available on the recommendation date.",
        "thresholds_tested_pct": list(thresholds_pct),
        "selected_threshold_pct": selected_threshold_pct,
        "missing_policy": "Missing dilution data does not trigger the veto.",
        "partitions": {
            "method": "Chronological 60% development, 20% validation, 20% final unseen by baseline episode-entry dates.",
            "validation_start": validation_start,
            "final_unseen_start": final_start,
        },
        "buy_signals_checked": len(buy_rows),
        "buy_signals_missing_dilution": missing_dilution,
        "baseline": {
            "full_history": baseline_summary,
            "final_unseen": baseline_final,
        },
        "variants": variants,
        "selected": selected,
        "production_effect": production_effect,
    }
