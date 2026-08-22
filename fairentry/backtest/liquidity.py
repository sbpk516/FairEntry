"""Post-hoc liquidity threshold comparison on one common point-in-time replay."""
from __future__ import annotations

import statistics
import math

DEFAULT_THRESHOLDS = (5_000_000, 10_000_000, 15_000_000, 20_000_000)


def _financial_strength(observation):
    for category in observation.get("categories") or []:
        if category.get("id") == "survival":
            return category.get("score")
    return None


def _high_confidence(observation):
    strength = _financial_strength(observation)
    return (observation.get("verdict") == "Buy"
            and isinstance(strength, (int, float)) and strength >= 70
            and not bool(observation.get("vetoes")))


def _wilson90(successes, total):
    if not total:
        return None
    z, rate = 1.6448536269514722, successes / total
    den = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / den
    margin = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total)) / den
    return [round((center - margin) * 100, 1), round((center + margin) * 100, 1)]


def _outcome_summary(observations):
    completed, successes, drawdowns = [], [], []
    for observation in observations:
        outcome = observation.get("_tuning_outcome") or {}
        hit, observed, terminal = (outcome.get("first_hit_secondary_days"),
                                   outcome.get("last_observed_days"), outcome.get("terminal_days"))
        is_complete = ((isinstance(observed, int) and observed >= 365)
                       or isinstance(terminal, int) and terminal <= 365)
        if is_complete:
            completed.append(observation)
            if isinstance(hit, int) and hit <= 365:
                successes.append(observation)
        drawdown = outcome.get("max_drawdown_pct")
        if isinstance(drawdown, (int, float)):
            drawdowns.append(drawdown)
    failures = len(completed) - len(successes)
    return {
        "episodes": len(observations),
        "unique_stocks": len({o.get("security_id") or o.get("ticker") for o in observations}),
        "completed_episodes": len(completed), "successes": len(successes), "failures": failures,
        "success_rate_pct": round(len(successes) / len(completed) * 100, 1) if completed else None,
        "failure_rate_pct": round(failures / len(completed) * 100, 1) if completed else None,
        "success_rate_ci90_pct": _wilson90(len(successes), len(completed)),
        "median_max_drawdown_pct": round(statistics.median(drawdowns), 1) if drawdowns else None,
        "worst_max_drawdown_pct": round(min(drawdowns), 1) if drawdowns else None,
    }


def compare_liquidity_thresholds(observations, thresholds=DEFAULT_THRESHOLDS):
    rows = []
    for threshold in thresholds:
        eligible = [o for o in observations if isinstance(o.get("avg_dollar_volume"), (int, float))
                    and o["avg_dollar_volume"] >= threshold]
        buys = [o for o in eligible if o.get("verdict") == "Buy"]
        overall = _outcome_summary(buys)
        high = [o for o in buys if _high_confidence(o)]
        high_summary = _outcome_summary(high)
        periods = {}
        for label, start, end in (("1998-2007", 1998, 2007), ("2008-2015", 2008, 2015),
                                  ("2016-2020", 2016, 2020), ("2021-2026", 2021, 2026)):
            periods[label] = _outcome_summary([
                o for o in high if start <= int(str(o.get("decision_date", "0"))[:4] or 0) <= end
            ])
        high_summary["by_period"] = periods
        high_summary["rule"] = "Buy + Financial Strength >=70 + no hard veto"
        rows.append({
            "threshold_usd": threshold, "candidate_observations": len(eligible),
            "unique_candidate_stocks": len({o.get("security_id") or o.get("ticker") for o in eligible}),
            "buy_episodes": overall["episodes"], "unique_buy_stocks": overall["unique_stocks"],
            "completed_buy_episodes": overall["completed_episodes"],
            "successes_30pct_within_one_year": overall["successes"],
            "failures_30pct_within_one_year": overall["failures"],
            "success_rate_pct": overall["success_rate_pct"],
            "failure_rate_pct": overall["failure_rate_pct"],
            "success_rate_ci90_pct": overall["success_rate_ci90_pct"],
            "median_max_drawdown_pct": overall["median_max_drawdown_pct"],
            "worst_max_drawdown_pct": overall["worst_max_drawdown_pct"],
            "high_confidence": high_summary,
            "historical_bid_ask_spread": None,
        })
    return {
        "method": "Each cutoff is applied to one common $5M-floor point-in-time SFA replay, holding scores and model rules constant.",
        "target": "+30% within 365 days after costs",
        "drawdown_definition": "Worst and median first-year drawdown observed for Buy episodes.",
        "bid_ask_spread_status": "unavailable",
        "bid_ask_spread_reason": "Sharadar SEP supplies historical OHLCV, not historical bid and ask quotes.",
        "high_confidence_policy": "Buy + Financial Strength >=70 + no hard veto; no score or weight change.",
        "rows": rows,
    }
