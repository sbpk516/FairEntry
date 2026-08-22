"""Post-hoc liquidity threshold comparison on one common point-in-time replay."""
from __future__ import annotations

import statistics

DEFAULT_THRESHOLDS = (5_000_000, 10_000_000, 15_000_000, 20_000_000)


def compare_liquidity_thresholds(observations, thresholds=DEFAULT_THRESHOLDS):
    rows = []
    for threshold in thresholds:
        eligible = [o for o in observations if isinstance(o.get("avg_dollar_volume"), (int, float))
                    and o["avg_dollar_volume"] >= threshold]
        buys = [o for o in eligible if o.get("verdict") == "Buy"]
        completed, successes, drawdowns = [], [], []
        for observation in buys:
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
        rows.append({
            "threshold_usd": threshold, "candidate_observations": len(eligible),
            "unique_candidate_stocks": len({o.get("security_id") or o.get("ticker") for o in eligible}),
            "buy_episodes": len(buys),
            "unique_buy_stocks": len({o.get("security_id") or o.get("ticker") for o in buys}),
            "completed_buy_episodes": len(completed),
            "successes_30pct_within_one_year": len(successes),
            "failures_30pct_within_one_year": failures,
            "success_rate_pct": round(len(successes) / len(completed) * 100, 1) if completed else None,
            "failure_rate_pct": round(failures / len(completed) * 100, 1) if completed else None,
            "median_max_drawdown_pct": round(statistics.median(drawdowns), 1) if drawdowns else None,
            "worst_max_drawdown_pct": round(min(drawdowns), 1) if drawdowns else None,
            "historical_bid_ask_spread": None,
        })
    return {
        "method": "Each cutoff is applied to one common $5M-floor point-in-time SFA replay, holding scores and model rules constant.",
        "target": "+30% within 365 days after costs",
        "drawdown_definition": "Worst and median first-year drawdown observed for Buy episodes.",
        "bid_ask_spread_status": "unavailable",
        "bid_ask_spread_reason": "Sharadar SEP supplies historical OHLCV, not historical bid and ask quotes.",
        "rows": rows,
    }
