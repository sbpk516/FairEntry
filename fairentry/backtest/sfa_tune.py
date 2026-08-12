"""Target-based, chronological weight challenger for the SFA replay.

Weights are selected only on older observations. The objective is one Buy
episode per issuer, not repeated weekly Buy signals. A challenger must improve
the chance of touching +25% within one year while preserving +30%, loss,
drawdown and SPY guardrails on later untouched dates. Nothing in this module
changes the production preset automatically.
"""
from __future__ import annotations

import math
import random
import statistics
from datetime import date


DEFAULT_POLICY = {
    "objective": "one_year_buy_episode_targets",
    "primary_gain_pct": 25.0,
    "secondary_gain_pct": 30.0,
    "horizon_days": 365,
    "large_loss_pct": -20.0,
    "severe_drawdown_pct": -30.0,
    "minimum_completed_episodes": 50,
    "minimum_unique_issuers": 30,
    "minimum_split_episodes": 10,
    "candidate_count": 1000,
    "weight_ranges": {
        "quality": (15.0, 30.0),
        "survival": (3.0, 10.0),
        "growth": (25.0, 45.0),
        "valuation": (12.0, 30.0),
        "confirmation": (10.0, 25.0),
    },
}


def policy_from_strategy(strategy) -> dict:
    """Copy the operational tuning contract from BacktestStrategy."""
    return {
        "objective": strategy.tuning_objective,
        "primary_gain_pct": strategy.tuning_primary_gain_pct,
        "secondary_gain_pct": strategy.tuning_secondary_gain_pct,
        "horizon_days": strategy.tuning_horizon_days,
        "large_loss_pct": strategy.tuning_large_loss_pct,
        "severe_drawdown_pct": strategy.tuning_severe_drawdown_pct,
        "minimum_completed_episodes": strategy.tuning_minimum_completed_episodes,
        "minimum_unique_issuers": strategy.tuning_minimum_unique_issuers,
        "minimum_split_episodes": strategy.tuning_minimum_split_episodes,
        "candidate_count": strategy.tuning_candidate_count,
        "weight_ranges": strategy.tuning_weight_ranges,
    }


def _policy(overrides: dict | None) -> dict:
    result = {**DEFAULT_POLICY, "weight_ranges": dict(DEFAULT_POLICY["weight_ranges"])}
    for key, value in (overrides or {}).items():
        if key == "weight_ranges":
            result[key] = {name: tuple(bounds) for name, bounds in value.items()}
        else:
            result[key] = value
    return result


def _tested_categories(cfg) -> list[str]:
    return [
        category_id
        for category_id, category in cfg.categories.items()
        if any(item.get("decision_status", "tested") == "tested"
               for item in category.get("items", []))
    ]


def _normalize(weights: dict, keys: list[str]) -> dict:
    total = sum(max(0.0, float(weights.get(key, 0))) for key in keys)
    if not total:
        return {key: round(100 / len(keys), 4) for key in keys}
    return {key: round(max(0.0, float(weights.get(key, 0))) / total * 100, 4)
            for key in keys}


def _verdict(observation: dict, weights: dict, bands: dict) -> str:
    categories = {row["id"]: row.get("score") for row in observation.get("categories", [])}
    available = [(weights.get(key, 0), score) for key, score in categories.items()
                 if isinstance(score, (int, float)) and weights.get(key, 0) > 0]
    score = (sum(weight * value for weight, value in available) /
             sum(weight for weight, _ in available)) if available else 0
    if any(row.get("result") is True for row in observation.get("vetoes", [])):
        return "Avoid"
    verdict = "Buy" if score >= bands["buy"] else "Watch" if score >= bands["watch"] else "Avoid"
    if verdict == "Buy" and observation.get("soft_gates"):
        return "Watch"
    return verdict


def _wilson_ci(successes: int, total: int, z: float = 1.6448536269514722) -> list[float] | None:
    if not total:
        return None
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(
        proportion * (1 - proportion) / total + z * z / (4 * total * total)
    ) / denominator
    return [round(max(0, centre - margin) * 100, 1),
            round(min(1, centre + margin) * 100, 1)]


def _candidate_weights(base: dict, ranges: dict, count: int, seed: int) -> list[tuple[str, dict]]:
    """Sample a bounded 100% simplex without weakening protected categories."""
    keys = list(base)
    bounds = {
        key: tuple(float(value) for value in ranges.get(
            key, (max(0, base[key] - 8), min(100, base[key] + 8))
        ))
        for key in keys
    }
    candidates = [("current", dict(base))]
    seen = {tuple(base[key] for key in keys)}
    rng = random.Random(seed)
    attempts = 0
    while len(candidates) < count + 1 and attempts < count * 20:
        attempts += 1
        order = list(keys)
        rng.shuffle(order)
        remaining = 100.0
        raw = {}
        possible = True
        for index, key in enumerate(order):
            rest = order[index + 1:]
            low, high = bounds[key]
            feasible_low = max(low, remaining - sum(bounds[name][1] for name in rest))
            feasible_high = min(high, remaining - sum(bounds[name][0] for name in rest))
            if feasible_low > feasible_high + 1e-9:
                possible = False
                break
            value = remaining if not rest else rng.uniform(feasible_low, feasible_high)
            raw[key] = value
            remaining -= value
        if not possible:
            continue
        candidate = {key: round(raw[key], 4) for key in keys}
        # Put rounding residue into the final sampled category.
        candidate[order[-1]] = round(candidate[order[-1]] + 100 - sum(candidate.values()), 4)
        identity = tuple(candidate[key] for key in keys)
        if identity in seen:
            continue
        seen.add(identity)
        candidates.append((f"challenger:{len(candidates)}", candidate))
    return candidates


def _episode_roots(observations: list[dict], weights: dict, bands: dict,
                   max_gap_days: int, *, use_recorded_verdict: bool = False) -> list[dict]:
    grouped = {}
    for observation in observations:
        key = observation.get("issuer_key") or observation.get("security_id") or observation.get("ticker")
        if key and observation.get("entry_date"):
            grouped.setdefault(str(key), []).append(observation)
    episodes = []

    def finish(rows):
        if not rows:
            return
        root = dict(rows[0])
        root["_episode"] = {
            "started": rows[0]["entry_date"],
            "last_buy": rows[-1]["entry_date"],
            "buy_signals": len(rows),
        }
        episodes.append(root)

    for rows in grouped.values():
        active, previous = [], None
        for observation in sorted(rows, key=lambda row: row["entry_date"]):
            current = date.fromisoformat(observation["entry_date"])
            if previous is not None and (current - previous).days > max_gap_days:
                finish(active)
                active = []
            verdict = (observation.get("verdict") if use_recorded_verdict
                       else _verdict(observation, weights, bands))
            if verdict == "Buy":
                active.append(observation)
            else:
                finish(active)
                active = []
            previous = current
        finish(active)
    return sorted(episodes, key=lambda row: (row["entry_date"], row.get("ticker", "")))


def _is_complete(outcome: dict, horizon: int) -> bool:
    last = outcome.get("last_observed_days")
    terminal = outcome.get("terminal_days")
    return ((isinstance(last, (int, float)) and last >= horizon) or
            (isinstance(terminal, (int, float)) and terminal <= horizon))


def _summary(episodes: list[dict], policy: dict, *, groups: bool = True) -> dict:
    horizon = int(policy["horizon_days"])
    mature = [row for row in episodes
              if _is_complete(row.get("_tuning_outcome") or {}, horizon)]
    primary_evaluable, secondary_evaluable = [], []
    for row in episodes:
        outcome = row.get("_tuning_outcome") or {}
        primary = outcome.get("first_hit_primary_days")
        secondary = outcome.get("first_hit_secondary_days")
        is_mature = _is_complete(outcome, horizon)
        if is_mature or (isinstance(primary, (int, float)) and primary <= horizon):
            primary_evaluable.append(row)
        if is_mature or (isinstance(secondary, (int, float)) and secondary <= horizon):
            secondary_evaluable.append(row)
    primary_days, secondary_days, returns, alphas, drawdowns = [], [], [], [], []
    for row in primary_evaluable:
        outcome = row.get("_tuning_outcome") or {}
        primary = outcome.get("first_hit_primary_days")
        if isinstance(primary, (int, float)) and primary <= horizon:
            primary_days.append(float(primary))
    for row in secondary_evaluable:
        outcome = row.get("_tuning_outcome") or {}
        secondary = outcome.get("first_hit_secondary_days")
        if isinstance(secondary, (int, float)) and secondary <= horizon:
            secondary_days.append(float(secondary))
    for row in mature:
        outcome = row.get("_tuning_outcome") or {}
        if isinstance(outcome.get("return_pct"), (int, float)):
            returns.append(float(outcome["return_pct"]))
        if isinstance(outcome.get("alpha_pct"), (int, float)):
            alphas.append(float(outcome["alpha_pct"]))
        if isinstance(outcome.get("max_drawdown_pct"), (int, float)):
            drawdowns.append(float(outcome["max_drawdown_pct"]))
    total = len(primary_evaluable)
    secondary_total = len(secondary_evaluable)
    large_losses = sum(value <= float(policy["large_loss_pct"]) for value in returns)
    severe_drawdowns = sum(value <= float(policy["severe_drawdown_pct"]) for value in drawdowns)
    result = {
        "episodes": len(episodes),
        "completed_episodes": total,
        "active_episodes": len(episodes) - total,
        "mature_episodes": len(mature),
        "unique_issuers": len({row.get("issuer_key") or row.get("ticker")
                               for row in primary_evaluable}),
        "primary_reached": len(primary_days),
        "primary_hit_rate_pct": round(len(primary_days) / total * 100, 1) if total else None,
        "primary_hit_ci90": _wilson_ci(len(primary_days), total),
        "secondary_reached": len(secondary_days),
        "secondary_evaluable": secondary_total,
        "secondary_hit_rate_pct": round(len(secondary_days) / secondary_total * 100, 1)
        if secondary_total else None,
        "secondary_hit_ci90": _wilson_ci(len(secondary_days), secondary_total),
        "median_days_to_primary": round(statistics.median(primary_days), 1) if primary_days else None,
        "mean_one_year_return_pct": round(statistics.mean(returns), 2) if returns else None,
        "mean_one_year_alpha_pct": round(statistics.mean(alphas), 2) if alphas else None,
        "large_loss_rate_pct": round(large_losses / len(returns) * 100, 1) if returns else None,
        "median_max_drawdown_pct": round(statistics.median(drawdowns), 2) if drawdowns else None,
        "severe_drawdown_rate_pct": round(severe_drawdowns / len(drawdowns) * 100, 1)
        if drawdowns else None,
    }
    if groups:
        for label, key in (
            ("by_year", lambda row: row.get("entry_date", "")[:4]),
            ("by_sector", lambda row: row.get("sector") or "Unknown"),
            ("by_regime", lambda row: row.get("regime") or "Unknown"),
        ):
            grouped = {}
            for row in primary_evaluable:
                grouped.setdefault(key(row), []).append(row)
            result[label] = [
                {"key": name, **_summary(rows, policy, groups=False)}
                for name, rows in sorted(grouped.items())
            ]
    return result


def _score(summary: dict, base: dict, weights: dict, policy: dict):
    if summary["completed_episodes"] < int(policy["minimum_split_episodes"]):
        return None
    primary_ci = summary.get("primary_hit_ci90") or [None, None]
    secondary_ci = summary.get("secondary_hit_ci90") or [None, None]
    if primary_ci[0] is None or secondary_ci[0] is None:
        return None
    def number(key, default):
        value = summary.get(key)
        return value if isinstance(value, (int, float)) else default
    drift = sum(abs(weights[key] - base[key]) for key in base)
    return (
        primary_ci[0],
        number("primary_hit_rate_pct", 0),
        secondary_ci[0],
        -number("large_loss_rate_pct", 100),
        number("mean_one_year_alpha_pct", -100),
        number("median_max_drawdown_pct", -100),
        -number("median_days_to_primary", 10000),
        -drift,
    )


def _check(check_id: str, label: str, passed: bool, current, challenger, rule: str) -> dict:
    return {"id": check_id, "label": label, "passed": bool(passed),
            "current": current, "challenger": challenger, "rule": rule}


def _later_period_checks(current: dict, challenger: dict, policy: dict) -> list[dict]:
    minimum = int(policy["minimum_split_episodes"])
    current_primary = current.get("primary_hit_rate_pct")
    challenger_primary = challenger.get("primary_hit_rate_pct")
    current_lower = (current.get("primary_hit_ci90") or [None])[0]
    challenger_lower = (challenger.get("primary_hit_ci90") or [None])[0]
    current_secondary = current.get("secondary_hit_rate_pct")
    challenger_secondary = challenger.get("secondary_hit_rate_pct")
    current_loss = current.get("large_loss_rate_pct")
    challenger_loss = challenger.get("large_loss_rate_pct")
    current_alpha = current.get("mean_one_year_alpha_pct")
    challenger_alpha = challenger.get("mean_one_year_alpha_pct")
    current_drawdown = current.get("median_max_drawdown_pct")
    challenger_drawdown = challenger.get("median_max_drawdown_pct")
    return [
        _check("enough_episodes", "Enough completed Buy episodes",
               challenger["completed_episodes"] >= minimum,
               current["completed_episodes"], challenger["completed_episodes"], f"at least {minimum}"),
        _check("primary_rate", f"Higher +{policy['primary_gain_pct']:g}% one-year success rate",
               None not in (current_primary, challenger_primary) and challenger_primary > current_primary,
               current_primary, challenger_primary, "challenger must be higher"),
        _check("primary_likely_floor", "No weaker lower end of the likely success range",
               None not in (current_lower, challenger_lower) and challenger_lower >= current_lower,
               current_lower, challenger_lower, "challenger must be at least current"),
        _check("secondary_rate", f"Protect +{policy['secondary_gain_pct']:g}% one-year success",
               None not in (current_secondary, challenger_secondary)
               and challenger_secondary >= current_secondary - 2,
               current_secondary, challenger_secondary, "no more than 2 percentage points worse"),
        _check("large_losses", "Do not materially increase large losses",
               None not in (current_loss, challenger_loss) and challenger_loss <= current_loss + 2,
               current_loss, challenger_loss, "no more than 2 percentage points worse"),
        _check("spy", "One-year Buy return remains ahead of SPY",
               challenger_alpha is not None and challenger_alpha >= 0
               and (current_alpha is None or challenger_alpha >= current_alpha - .5),
               current_alpha, challenger_alpha, "at least 0% and no more than 0.5 points worse"),
        _check("drawdown", "Do not materially worsen the typical decline",
               None not in (current_drawdown, challenger_drawdown)
               and challenger_drawdown >= current_drawdown - 2,
               current_drawdown, challenger_drawdown, "no more than 2 percentage points worse"),
    ]


def _stability_check(current: dict, challenger: dict) -> dict:
    comparisons = []
    for dimension in ("by_year", "by_sector"):
        old = {row["key"]: row for row in current.get(dimension, [])}
        new = {row["key"]: row for row in challenger.get(dimension, [])}
        for key in sorted(set(old) & set(new)):
            if min(old[key]["completed_episodes"], new[key]["completed_episodes"]) < 5:
                continue
            old_rate = old[key].get("primary_hit_rate_pct")
            new_rate = new[key].get("primary_hit_rate_pct")
            if None in (old_rate, new_rate):
                continue
            comparisons.append({"dimension": dimension, "key": key,
                                "current": old_rate, "challenger": new_rate,
                                "not_materially_worse": new_rate >= old_rate - 10})
    safe = sum(row["not_materially_worse"] for row in comparisons)
    share = round(safe / len(comparisons) * 100, 1) if comparisons else None
    return {
        "comparisons": comparisons,
        "not_materially_worse_pct": share,
        "passed": share is not None and share >= 70,
        "rule": "At least 70% of comparable years and sectors must be within 10 percentage points of current.",
    }


def tune_sfa_observations(observations: list[dict], cfg, *, step_days: int = 30,
                          candidates: int | None = None, seed: int = 42,
                          policy: dict | None = None, progress=None) -> dict:
    """Recommend one shared challenger; never mutate the configured default."""
    policy = _policy(policy)
    if candidates is not None:
        policy["candidate_count"] = int(candidates)
    dates = sorted({row["decision_date"] for row in observations if row.get("decision_date")})
    if len(dates) < 15:
        return {"ok": False, "reason": "fewer than 15 historical decision dates"}
    development_end = max(1, int(len(dates) * .6))
    validation_end = max(development_end + 1, int(len(dates) * .8))
    split_dates = {
        "development": set(dates[:development_end]),
        "validation": set(dates[development_end:validation_end]),
        "test": set(dates[validation_end:]),
    }
    split = {name: {"first": min(values), "last": max(values), "cohorts": len(values)}
             for name, values in split_dates.items()}
    active = _tested_categories(cfg)
    preset_name = cfg.defaults.get("strategy_presets", {}).get("quality_growth", "backtest_recommended")
    configured = cfg.scoring.get("presets", {}).get(preset_name) or {
        key: cfg.categories[key]["weight"] for key in active
    }
    base = _normalize(configured, active)
    ranges = {key: tuple(policy["weight_ranges"].get(key, (0, 100))) for key in active}
    max_gap_days = max(step_days + 1, int(math.ceil(step_days * 1.5)))

    current_episodes = _episode_roots(
        observations, base, cfg.verdict_bands, max_gap_days,
        use_recorded_verdict=True,
    )
    current = {
        name: _summary([row for row in current_episodes
                        if row["decision_date"] in values], policy)
        for name, values in split_dates.items()
    }
    current["all"] = _summary(current_episodes, policy)

    evaluated = []
    generated = _candidate_weights(base, ranges, int(policy["candidate_count"]), seed)
    for index, (name, weights) in enumerate(generated, 1):
        episodes = (current_episodes if name == "current" else _episode_roots(
            observations, weights, cfg.verdict_bands, max_gap_days
        ))
        development = _summary(
            [row for row in episodes if row["decision_date"] in split_dates["development"]],
            policy,
        )
        rank = _score(development, base, weights, policy)
        if rank is not None:
            # Keep only compact aggregate results while searching. Retaining
            # every candidate's stock-level episode list would multiply memory
            # usage by the number of candidates on a full-history replay.
            evaluated.append((rank, name, weights, development))
        if progress and (index == 1 or index == len(generated) or index % 25 == 0):
            progress({"checked": index, "total": len(generated),
                      "valid_candidates": len(evaluated)})
    if not evaluated:
        return {
            "ok": False,
            "reason": "no candidate produced enough completed Buy episodes in the development dates",
            "method": "chronological_episode_target_challenger_v1",
            "split": split,
            "policy": policy,
            "current_weights": base,
        }
    evaluated.sort(key=lambda row: row[0], reverse=True)
    _, selected_name, selected_weights, selected_development = evaluated[0]
    selected_episodes = _episode_roots(
        observations, selected_weights, cfg.verdict_bands, max_gap_days,
        use_recorded_verdict=(selected_name == "current"),
    )
    challenger = {
        "development": selected_development,
        "validation": _summary([row for row in selected_episodes
                                if row["decision_date"] in split_dates["validation"]], policy),
        "test": _summary([row for row in selected_episodes
                          if row["decision_date"] in split_dates["test"]], policy),
        "all": _summary(selected_episodes, policy),
    }
    validation_checks = _later_period_checks(current["validation"], challenger["validation"], policy)
    test_checks = _later_period_checks(current["test"], challenger["test"], policy)
    sample_checks = [
        _check("all_episodes", "Enough completed episodes across the full history",
               challenger["all"]["completed_episodes"] >= int(policy["minimum_completed_episodes"]),
               current["all"]["completed_episodes"], challenger["all"]["completed_episodes"],
               f"at least {policy['minimum_completed_episodes']}"),
        _check("all_issuers", "Enough different companies across the full history",
               challenger["all"]["unique_issuers"] >= int(policy["minimum_unique_issuers"]),
               current["all"]["unique_issuers"], challenger["all"]["unique_issuers"],
               f"at least {policy['minimum_unique_issuers']}"),
        _check("years", "Evidence covers several entry years",
               len(challenger["all"].get("by_year", [])) >= 4,
               len(current["all"].get("by_year", [])),
               len(challenger["all"].get("by_year", [])), "at least 4 years"),
        _check("sectors", "Evidence covers more than one preferred business area",
               len(challenger["all"].get("by_sector", [])) >= 2,
               len(current["all"].get("by_sector", [])),
               len(challenger["all"].get("by_sector", [])), "at least 2 sectors"),
    ]
    stability = _stability_check(current["all"], challenger["all"])
    promotion_eligible = (
        selected_name != "current"
        and all(row["passed"] for row in validation_checks)
        and all(row["passed"] for row in test_checks)
        and all(row["passed"] for row in sample_checks)
        and stability["passed"]
    )
    segment_comparison = {}
    for strategy_key in sorted({row.get("strategy_key") or "unknown" for row in observations}):
        segment_comparison[strategy_key] = {
            "current": _summary([row for row in current_episodes
                                 if (row.get("strategy_key") or "unknown") == strategy_key], policy),
            "challenger": _summary([row for row in selected_episodes
                                    if (row.get("strategy_key") or "unknown") == strategy_key], policy),
        }
    return {
        "ok": True,
        "method": "chronological_episode_target_challenger_v1",
        "promotion": "manual",
        "default_changed": False,
        "objective": (
            f"Improve the lower end of the likely +{policy['primary_gain_pct']:g}% one-year "
            f"Buy-episode success range; protect +{policy['secondary_gain_pct']:g}%, losses, "
            "drawdown and one-year return versus SPY."
        ),
        "policy": policy,
        "active_categories": active,
        "inactive_categories": [key for key in cfg.categories if key not in active],
        "split": split,
        "current_preset": preset_name,
        "current_weights": base,
        "selected_candidate": selected_name,
        "challenger_weights": selected_weights,
        "candidates_requested": int(policy["candidate_count"]),
        "candidates_generated": len(generated) - 1,
        "candidates_evaluated": len(evaluated),
        "current": current,
        "challenger": challenger,
        "validation_checks": validation_checks,
        "test_checks": test_checks,
        "sample_checks": sample_checks,
        "stability": stability,
        "segment_comparison": segment_comparison,
        "promotion_eligible": promotion_eligible,
        "decision": "Ready for manual review" if promotion_eligible else "Keep current weights",
        "promotion_note": (
            "The configured default is unchanged. A person must review and explicitly promote "
            "an eligible challenger; failed or incomplete checks always keep the current weights."
        ),
    }
