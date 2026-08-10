"""Chronological, per-strategy weight evaluation over SFA replay observations."""
from __future__ import annotations

import random
import statistics


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


def _evaluate(observations: list[dict], weights: dict, bands: dict,
              horizons: tuple[int, ...]) -> dict:
    by_horizon = {}
    for horizon in horizons:
        buckets = {"Buy": [], "Watch": [], "Avoid": []}
        for observation in observations:
            value = observation.get("horizons", {}).get(str(horizon), {}).get("alpha_pct")
            if isinstance(value, (int, float)):
                buckets[_verdict(observation, weights, bands)].append(value)
        means = {key: round(statistics.mean(values), 3) if values else None
                 for key, values in buckets.items()}
        spread = (means["Buy"] - means["Avoid"]
                  if means["Buy"] is not None and means["Avoid"] is not None else None)
        by_horizon[str(horizon)] = {
            "by_verdict_mean_alpha_pct": means,
            "buy_minus_avoid_pct": round(spread, 3) if spread is not None else None,
            "buy_n": len(buckets["Buy"]),
            "monotonic": (means["Buy"] >= means["Watch"] >= means["Avoid"])
            if all(means[key] is not None for key in means) else None,
        }
    valid = [row["buy_minus_avoid_pct"] for row in by_horizon.values()
             if row["buy_minus_avoid_pct"] is not None and row["buy_n"] >= 10]
    return {"horizons": by_horizon,
            "worst_spread_pct": round(min(valid), 3) if len(valid) == len(horizons) else None}


def _candidate_weights(base: dict, presets: dict, count: int, seed: int) -> list[tuple[str, dict]]:
    candidates = [("current", dict(base))]
    candidates.extend((f"preset:{name}", dict(weights)) for name, weights in presets.items())
    rng = random.Random(seed)
    keys = list(base)
    for index in range(count):
        raw = {key: max(1.0, base[key] + rng.gauss(0, 4.5)) for key in keys}
        # Defensive evidence cannot be optimized away on a return-only objective.
        for key in ("risk", "survival"):
            if key in raw:
                raw[key] = min(base[key] + 3, max(base[key] - 3, raw[key]))
        total = sum(raw.values())
        candidates.append((f"challenger:{index + 1}",
                           {key: round(value / total * 100, 4) for key, value in raw.items()}))
    return candidates


def tune_sfa_observations(observations: list[dict], cfg, *,
                          horizons=(30, 90, 180, 365, 548), candidates=300,
                          seed=42) -> dict:
    """Tune on development only; validate once; report an untouched final test."""
    output = {"method": "chronological_development_validation_test",
              "horizons_days": list(horizons), "promotion": "manual", "strategies": {}}
    bands = cfg.verdict_bands
    presets = cfg.scoring.get("presets", {})
    for offset, strategy_key in enumerate(("deep_value", "quality_growth")):
        rows = [row for row in observations if row.get("strategy_key") == strategy_key]
        dates = sorted({row["decision_date"] for row in rows})
        if len(dates) < 15:
            output["strategies"][strategy_key] = {"ok": False, "reason": "fewer than 15 cohorts"}
            continue
        development_end = max(1, int(len(dates) * .6))
        validation_end = max(development_end + 1, int(len(dates) * .8))
        split = {
            "development": set(dates[:development_end]),
            "validation": set(dates[development_end:validation_end]),
            "test": set(dates[validation_end:]),
        }
        split_rows = {name: [row for row in rows if row["decision_date"] in values]
                      for name, values in split.items()}
        preset_name = cfg.defaults.get("strategy_presets", {}).get(strategy_key)
        base = presets.get(preset_name) or {
            key: category["weight"] for key, category in cfg.categories.items()
        }
        evaluated = []
        for name, weights in _candidate_weights(base, presets, candidates, seed + offset):
            report = _evaluate(split_rows["development"], weights, bands, tuple(horizons))
            if report["worst_spread_pct"] is not None:
                evaluated.append((report["worst_spread_pct"], name, weights, report))
        evaluated.sort(key=lambda row: (-row[0], row[1]))
        if not evaluated:
            output["strategies"][strategy_key] = {"ok": False, "reason": "no evaluable challenger"}
            continue
        _, selected_name, selected, development = evaluated[0]
        current = {name: _evaluate(split_rows[name], base, bands, tuple(horizons)) for name in split}
        selected_reports = {"development": development,
                            "validation": _evaluate(split_rows["validation"], selected, bands, tuple(horizons)),
                            "test": _evaluate(split_rows["test"], selected, bands, tuple(horizons))}
        validation_gain = []
        for horizon in horizons:
            old = current["validation"]["horizons"][str(horizon)]["buy_minus_avoid_pct"]
            new = selected_reports["validation"]["horizons"][str(horizon)]["buy_minus_avoid_pct"]
            if old is None or new is None:
                validation_gain = []
                break
            validation_gain.append(new - old)
        validation_pass = bool(validation_gain and min(validation_gain) >= -0.25
                               and statistics.mean(validation_gain) > 0)
        test_spreads = [selected_reports["test"]["horizons"][str(h)]["buy_minus_avoid_pct"]
                        for h in horizons]
        test_pass = all(value is not None and value > 0 for value in test_spreads)
        output["strategies"][strategy_key] = {
            "ok": True,
            "split": {name: {"first": min(values), "last": max(values), "cohorts": len(values)}
                      for name, values in split.items()},
            "current_preset": preset_name,
            "selected_candidate": selected_name,
            "selected_weights": selected,
            "current": current,
            "selected": selected_reports,
            "validation_pass": validation_pass,
            "untouched_test_pass": test_pass,
            "promotion_eligible": validation_pass and test_pass,
            "promotion_note": "Manual review required; the test partition must remain untouched after this decision.",
        }
    output["all_strategies_promotion_eligible"] = all(
        row.get("promotion_eligible") for row in output["strategies"].values()
    )
    return output
