"""Frozen, research-only business-health scoring challenger.

The challenger replaces only Business Quality and Growth scores. Survival,
valuation, market confirmation, vetoes, and soft gates remain the production
definitions so the experiment isolates business-health changes. It never
mutates a production verdict.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "config" / "business_health_challenger.json"


def load_policy(path: Path = REGISTRY_PATH) -> dict:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("production_effect") != "none":
        raise ValueError("business-health challenger must remain research-only")
    return policy


def _number(value):
    return float(value) if isinstance(value, (int, float)) else None


def _linear(value, floor: float, full: float) -> float | None:
    value = _number(value)
    if value is None:
        return None
    if floor == full:
        return 100.0 if value >= full else 0.0
    return round(max(0.0, min(100.0, (value - floor) / (full - floor) * 100)), 2)


def _lower(value, full: float, floor: float) -> float | None:
    value = _number(value)
    if value is None:
        return None
    return _linear(value, floor, full)


def _weighted(parts: dict[str, tuple[float, float | None]]) -> dict:
    available = {key: (weight, score) for key, (weight, score) in parts.items()
                 if score is not None}
    configured = sum(weight for weight, _ in parts.values())
    observed = sum(weight for weight, _ in available.values())
    score = (sum(weight * score for weight, score in available.values()) / observed
             if observed else None)
    return {
        "score": round(score, 1) if score is not None else None,
        "coverage_pct": round(observed / configured * 100, 1) if configured else 0,
        "components": {
            key: {"weight": weight, "score": score}
            for key, (weight, score) in parts.items()
        },
    }


def _result(score: float | None) -> dict:
    return {"score": score, "coverage_pct": 100.0 if score is not None else 0.0}


def _weighted_nested(parts: dict[str, tuple[float, dict]]) -> dict:
    """Combine composites without hiding missing evidence through renormalization."""
    configured = sum(weight for weight, _ in parts.values())
    observed = 0.0
    numerator = 0.0
    components = {}
    for key, (weight, result) in parts.items():
        coverage = float(result.get("coverage_pct") or 0)
        effective_weight = weight * coverage / 100
        score = result.get("score")
        if score is not None and effective_weight:
            observed += effective_weight
            numerator += effective_weight * score
        components[key] = {"weight": weight, "score": score,
                           "coverage_pct": coverage}
    score = numerator / observed if observed else None
    return {
        "score": round(score, 1) if score is not None else None,
        "coverage_pct": round(observed / configured * 100, 1) if configured else 0,
        "components": components,
    }


def _growth_rate_score(current, long_term, threshold: dict,
                       *, recovery=False) -> dict:
    current_score = _linear(current, threshold["floor"], threshold["full"])
    long_score = _linear(long_term, threshold["floor"], threshold["full"])
    if recovery and current_score is None:
        current_score = 70.0
    return _weighted({"three_year": (60, long_score), "recent_yoy": (40, current_score)})


def score_factors(factors: dict, policy: dict | None = None) -> dict:
    """Score one point-in-time factor bundle without issuing a live verdict."""
    policy = policy or load_policy()
    t = policy["thresholds"]

    roic_level = _linear(factors.get("roic_pct"), **t["roic_level_pct"])
    enough_roic_history = (_number(factors.get("roic_5y_observations")) or 0) >= 12
    roic_median = (_linear(factors.get("roic_5y_median_pct"),
                           **t["roic_level_pct"])
                   if enough_roic_history else None)
    roic_stability = (_lower(
        factors.get("roic_5y_stddev_pct"),
        t["roic_stability_stddev_pct"]["full"],
        t["roic_stability_stddev_pct"]["floor"],
    ) if enough_roic_history else None)
    consistency = _weighted({"median_level": (70, roic_median),
                             "stability": (30, roic_stability)})
    roic_trend = _linear(factors.get("roic_change_3y_pp"),
                         **t["roic_trend_3y_pp"])
    roic = _weighted_nested({"current_level": (50, _result(roic_level)),
                             "five_year_consistency": (30, consistency),
                             "recent_trend": (20, _result(roic_trend))})

    operating_level = _linear(factors.get("operating_margin_pct"),
                              **t["operating_margin_pct"])
    margin_trend = _linear(factors.get("operating_margin_change_yoy_pp"),
                           **t["operating_margin_trend_yoy_pp"])
    enough_fcf_history = (_number(factors.get("fcf_history_observations")) or 0) >= 8
    fcf_quality = _weighted_nested({
        "fcf_margin": (50, _result(_linear(factors.get("fcf_margin_pct"),
                                            **t["fcf_margin_pct"]))),
        "cash_conversion": (30, _result(_linear(factors.get("cash_conversion_pct"),
                                                 **t["cash_conversion_pct"]))),
        "positive_history": (20, _result(
            _linear(factors.get("positive_fcf_history_pct"),
                    **t["positive_fcf_history_pct"])
            if enough_fcf_history else None)),
    })
    qw = policy["quality_weights"]
    quality = _weighted_nested({
        "roic_quality": (qw["roic_quality"], roic),
        "operating_margin_level": (qw["operating_margin_level"], _result(operating_level)),
        "margin_trend": (qw["margin_trend"], _result(margin_trend)),
        "fcf_quality": (qw["fcf_quality"], fcf_quality),
    })

    revenue = _growth_rate_score(
        factors.get("revenue_growth_yoy_pct"), factors.get("revenue_cagr_3y_pct"),
        t["revenue_growth_pct"],
    )
    eps = _growth_rate_score(
        factors.get("eps_growth_yoy_pct"), factors.get("eps_cagr_3y_pct"),
        t["eps_growth_pct"], recovery=bool(factors.get("eps_recovery")),
    )
    fcf = _growth_rate_score(
        factors.get("fcf_growth_yoy_pct"), factors.get("fcf_cagr_3y_pct"),
        t["fcf_growth_pct"], recovery=bool(factors.get("fcf_recovery")),
    )
    acceleration = _weighted({
        "revenue": (50, _linear(factors.get("revenue_growth_change_pp"),
                                **t["growth_acceleration_pp"])),
        "eps": (50, _linear(factors.get("eps_growth_change_pp"),
                            **t["growth_acceleration_pp"])),
    })
    gw = policy["growth_weights"]
    growth = _weighted_nested({
        "revenue_growth": (gw["revenue_growth"], revenue),
        "eps_growth": (gw["eps_growth"], eps),
        "fcf_growth": (gw["fcf_growth"], fcf),
        "growth_acceleration": (gw["growth_acceleration"], acceleration),
    })
    return {
        "model_id": policy["id"],
        "production_effect": "none",
        "quality": quality,
        "growth": growth,
        "detail": {"roic": roic, "fcf_quality": fcf_quality,
                   "revenue_growth": revenue, "eps_growth": eps,
                   "fcf_growth": fcf, "growth_acceleration": acceleration},
        "market_share": policy["market_share"],
    }


def _categories(row: dict) -> dict:
    return {category.get("id"): category.get("score")
            for category in row.get("categories", [])}


def score_observation(row: dict, policy: dict | None = None) -> dict:
    policy = policy or load_policy()
    factor_score = score_factors(row.get("research_factors") or {}, policy)
    cats = _categories(row)
    cats.update({"quality": factor_score["quality"]["score"],
                 "growth": factor_score["growth"]["score"]})
    minimum = float(policy["minimum_category_coverage_pct"])
    coverage_ok = (factor_score["quality"]["coverage_pct"] >= minimum
                   and factor_score["growth"]["coverage_pct"] >= minimum)
    weights = policy["top_level_weights"]
    available = {key: score for key, score in cats.items()
                 if key in weights and score is not None}
    den = sum(weights[key] for key in available)
    final = (sum(weights[key] * score for key, score in available.items()) / den
             if den else None)
    vetoes = row.get("vetoes") or []
    gates = row.get("soft_gates") or []
    score_band = "Buy" if final is not None and final >= policy["buy_threshold"] else (
        "Watch" if final is not None and final >= 50 else "Avoid")
    verdict = ("Avoid" if vetoes else "Watch" if score_band == "Buy" and gates
               else score_band)
    if not coverage_ok and verdict == "Buy":
        verdict = "Watch"
    return {
        **factor_score,
        "score": round(final, 1) if final is not None else None,
        "score_band_verdict": score_band,
        "verdict": verdict,
        "coverage_eligible": coverage_ok,
        "coverage_requirement_pct": minimum,
        "preserved_veto_count": len(vetoes),
        "preserved_soft_gate_count": len(gates),
    }


def _hit_30_within_year(row: dict) -> bool | None:
    milestones = row.get("return_milestones") or {}
    days = (milestones.get("first_hit_days") or {}).get("30")
    last = milestones.get("last_observed_days")
    terminal = milestones.get("terminal_days")
    if isinstance(days, (int, float)) and days <= 365:
        return True
    if ((isinstance(last, (int, float)) and last >= 365)
            or (isinstance(terminal, (int, float)) and terminal <= 365)):
        return False
    return None


def _summary(rows: list[dict], verdict_key: str) -> dict:
    if verdict_key == "verdict":
        buys = [row for row in rows if row.get("verdict") == "Buy"]
    else:
        buys = [row for row in rows
                if (row.get("business_health_challenger") or {}).get("verdict") == "Buy"]
    outcomes = [_hit_30_within_year(row) for row in buys]
    complete = [value for value in outcomes if value is not None]
    return {
        "buy_observations": len(buys),
        "completed_one_year_observations": len(complete),
        "reached_30pct_within_one_year": sum(value is True for value in complete),
        "hit_rate_pct": round(sum(value is True for value in complete) / len(complete) * 100, 1)
        if complete else None,
    }


def run_challenger(observations: list[dict], policy: dict | None = None) -> dict:
    policy = policy or load_policy()
    scored = 0
    coverage_eligible = 0
    for row in observations:
        if not row.get("research_factors"):
            continue
        result = score_observation(row, policy)
        row["business_health_challenger"] = result
        scored += 1
        coverage_eligible += bool(result["coverage_eligible"])
    return {
        "model_id": policy["id"],
        "status": "research_only",
        "production_effect": "none",
        "observations": len(observations),
        "scored": scored,
        "coverage_eligible": coverage_eligible,
        "baseline": _summary(observations, "verdict"),
        "challenger": _summary(observations, "business_health_challenger.verdict"),
        "policy": policy,
        "interpretation": "A frozen challenger comparison; it cannot alter the official Buy list.",
    }
