"""Point-in-time research for a Rare Capability and Execution Moat selector.

The first version deliberately separates measurable filing evidence from facts
the warehouse does not yet contain (credible competitors, switching costs and
dated technical milestones).  It ranks only existing Buy episodes and never
changes FairEntry's official score or verdict.
"""
from __future__ import annotations

import math
import statistics

from fairentry.backtest.research_cycle import _partition, _split_dates, _summary
from fairentry.backtest.sfa_tune import _episode_roots


VERSION = "capability_moat_research_v1"
COMPONENT_WEIGHTS = {
    "proven_execution": 30,
    "commercial_product_proof": 20,
    "replication_barrier_proxy": 20,
    "competitive_scarcity": 15,
    "productive_reinvestment": 15,
}
POLICY = {
    "development_share": .60,
    "validation_share": .20,
    "minimum_coverage_pct": 70,
    "minimum_execution_score": 60,
    "minimum_commercial_score": 60,
    "minimum_completed_episodes": 100,
    "minimum_unique_issuers": 50,
    "minimum_final_test_episodes": 20,
    "minimum_final_test_success_rate_pct": 60,
    "minimum_final_test_improvement_pp": 10,
    "maximum_drawdown_deterioration_pp": 2,
}


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _linear(value, floor, full):
    value = _number(value)
    if value is None:
        return None
    if full == floor:
        return None
    return round(max(0.0, min(100.0, (value - floor) / (full - floor) * 100)), 1)


def _average(parts):
    available = [(weight, value) for weight, value in parts if value is not None]
    if not available:
        return None
    return round(sum(weight * value for weight, value in available)
                 / sum(weight for weight, _ in available), 1)


def _competitive_scarcity(row):
    """Score only dated, source-backed standardized competition evidence."""
    evidence = row.get("capability_evidence") or {}
    observed_at = str(evidence.get("observed_at") or "")[:10]
    decision_date = str(row.get("decision_date") or row.get("entry_date") or "")[:10]
    sources = [source for source in evidence.get("sources") or [] if source]
    if not observed_at or not decision_date or observed_at > decision_date or not sources:
        return None, "Unavailable: no dated, source-backed competition record on the Buy date."
    competitors = evidence.get("credible_competitors")
    competitor_score = None
    if isinstance(competitors, int) and not isinstance(competitors, bool) and competitors >= 0:
        competitor_score = (100 if competitors == 0 else 90 if competitors == 1
                            else 75 if competitors == 2 else 60 if competitors == 3
                            else 45 if competitors == 4 else 25)
    position_score = {
        "leader": 100, "top_three": 75, "established": 50, "challenger": 30,
    }.get(str(evidence.get("market_position") or "").lower())
    switching_score = {
        "high": 100, "medium": 65, "low": 25,
    }.get(str(evidence.get("switching_cost") or "").lower())
    score = _average(((50, competitor_score), (30, position_score),
                      (20, switching_score)))
    return score, ("Standardized competition evidence available."
                   if score is not None else
                   "Unavailable: the dated record lacks standardized competition fields.")


def score_capability_moat(row: dict) -> dict:
    """Build one deterministic score from information frozen at the Buy date."""
    factors = row.get("research_factors") or {}
    execution = _average((
        (40, _number(factors.get("revenue_growth_positive_quarters_pct"))),
        (35, _number(factors.get("positive_eps_quarters_pct"))),
        (25, _linear(factors.get("operating_margin_change_qoq_pp"), -5, 2)),
    ))
    commercial = _average((
        (40, _linear(factors.get("fcf_margin_pct"), 0, 15)),
        (35, _linear(factors.get("gross_margin_pct"), 20, 60)),
        (25, _linear(factors.get("revenue_growth_min_pct"), -10, 15)),
    ))
    research = _number(factors.get("rnd_to_revenue_pct"))
    capex = _number(factors.get("capex_to_revenue_pct"))
    investment_intensity = (_linear(sum(max(0.0, value) for value in (research, capex)
                                        if value is not None), 2, 20)
                            if research is not None or capex is not None else None)
    roic = _linear(factors.get("roic_pct"), 0, 20)
    gross_productivity = _linear(factors.get("gross_profitability_pct"), 5, 30)
    replication = _average(((40, investment_intensity), (35, roic),
                            (25, gross_productivity)))
    productive_reinvestment = _average((
        (30, investment_intensity),
        (45, roic),
        (25, _linear(factors.get("revenue_growth_change_pp"), -10, 10)),
    ))
    competitive, competitive_reason = _competitive_scarcity(row)
    direct = row.get("capability_evidence") or {}
    direct_product_evidence = (
        isinstance(direct.get("technical_milestones_completed"), int)
        and not isinstance(direct.get("technical_milestones_completed"), bool)
        and direct.get("technical_milestones_completed") >= 1
        and str(direct.get("commercial_product_status") or "").lower()
        in {"operating", "scaled"}
    )
    components = {
        "proven_execution": execution,
        "commercial_product_proof": commercial,
        "replication_barrier_proxy": replication,
        "competitive_scarcity": competitive,
        "productive_reinvestment": productive_reinvestment,
    }
    available_weight = sum(COMPONENT_WEIGHTS[key] for key, value in components.items()
                           if value is not None)
    score = (round(sum(COMPONENT_WEIGHTS[key] * value
                       for key, value in components.items() if value is not None)
                   / available_weight, 1) if available_weight else None)
    coverage = float(available_weight)
    gates = {
        "coverage": coverage >= POLICY["minimum_coverage_pct"],
        "proven_execution": execution is not None and execution >= POLICY["minimum_execution_score"],
        "commercial_product_proof": commercial is not None and commercial >= POLICY["minimum_commercial_score"],
    }
    eligible = score is not None and all(gates.values())
    confidence = "high" if coverage >= 90 else "medium" if coverage >= 70 else "low"
    details = {
        "proven_execution": "Revenue consistency, positive-EPS consistency and margin delivery.",
        "commercial_product_proof": "Financial commercial-proof proxy: cash flow, gross margin and a positive recent growth floor.",
        "replication_barrier_proxy": "Capability investment paired with ROIC and gross productivity; spending alone cannot earn a high score.",
        "competitive_scarcity": competitive_reason,
        "productive_reinvestment": "R&D/capital intensity paired with ROIC and revenue-growth improvement.",
    }
    return {
        "version": VERSION,
        "score": score,
        "evidence_coverage_pct": coverage,
        "confidence": confidence,
        "eligible_for_selector": eligible,
        "gates": gates,
        "components": [
            {"id": key, "weight": COMPONENT_WEIGHTS[key], "score": components[key],
             "available": components[key] is not None, "definition": details[key]}
            for key in COMPONENT_WEIGHTS
        ],
        "direct_competition_evidence_available": competitive is not None,
        "direct_product_and_milestone_evidence_available": direct_product_evidence,
        "full_capability_definition_complete": competitive is not None and direct_product_evidence,
        "future_data_used": False,
        "score_effect": 0,
        "verdict_effect": "none",
    }


def _portfolio_summary(rows):
    result = _summary(rows)
    mature = []
    for row in rows:
        outcome = row.get("_tuning_outcome") or {}
        if isinstance(outcome.get("return_pct"), (int, float)):
            mature.append(outcome)
    returns = [float(row["return_pct"]) for row in mature]
    alphas = [float(row["alpha_pct"]) for row in mature
              if isinstance(row.get("alpha_pct"), (int, float))]
    drawdowns = [float(row["max_drawdown_pct"]) for row in mature
                 if isinstance(row.get("max_drawdown_pct"), (int, float))]
    result["portfolio"] = {
        "mean_one_year_return_pct": round(statistics.fmean(returns), 2) if returns else None,
        "mean_one_year_alpha_pct": round(statistics.fmean(alphas), 2) if alphas else None,
        "large_loss_rate_pct": round(sum(value <= -20 for value in returns) / len(returns) * 100, 1)
        if returns else None,
        "median_max_drawdown_pct": round(statistics.median(drawdowns), 1) if drawdowns else None,
    }
    return result


def _select_by_buy_date(episodes, mode):
    groups = {}
    for row in episodes:
        date = row.get("decision_date") or row.get("entry_date")
        if date:
            groups.setdefault(date, []).append(row)
    selected = []
    for date in sorted(groups):
        eligible = [row for row in groups[date]
                    if (row.get("capability_moat") or {}).get("eligible_for_selector")]
        ranked = sorted(eligible, key=lambda row: (
            -(row["capability_moat"].get("score") or -1),
            -(row.get("score") or -1), str(row.get("ticker") or "")))
        count = (max(1, math.ceil(len(groups[date]) / 2)) if mode == "top_half"
                 else min(3, len(ranked)) if mode == "top_three" else 1)
        selected.extend(ranked[:count])
    return selected


def _split_summary(rows, split):
    output = {name: _portfolio_summary(_partition(rows, dates))
              for name, dates in split.items()}
    output["all"] = _portfolio_summary(rows)
    return output


def _check(check_id, label, passed, actual, required):
    return {"id": check_id, "label": label, "passed": bool(passed),
            "actual": actual, "required": required}


def run_capability_moat_research(observations: list[dict], *, step_days=30) -> dict:
    """Rank existing Buy episodes and compare predeclared concentration policies."""
    buys = [row for row in observations if row.get("verdict") == "Buy"]
    max_gap = max(step_days + 1, int(math.ceil(step_days * 1.5)))
    episodes = _episode_roots(buys, {}, {"buy": 0, "watch": 0}, max_gap,
                              use_recorded_verdict=True)
    for row in episodes:
        row["capability_moat"] = score_capability_moat(row)
    split = _split_dates(episodes, POLICY)
    if not split:
        return {"ok": False, "version": VERSION,
                "reason": "Fewer than three historical Buy dates.",
                "production_effect": "none", "score_effect": 0,
                "verdict_effect": "none"}
    selectors = {
        mode: _select_by_buy_date(episodes, mode)
        for mode in ("top_half", "top_three", "top_one")
    }
    baseline = _split_summary(episodes, split)
    results = {mode: _split_summary(rows, split) for mode, rows in selectors.items()}
    baseline_test = baseline["test"]["primary"]
    primary_test = results["top_half"]["test"]["primary"]
    old_rate = baseline_test.get("success_rate_pct")
    new_rate = primary_test.get("success_rate_pct")
    improvement = round(new_rate - old_rate, 1) if None not in (old_rate, new_rate) else None
    old_drawdown = baseline_test.get("median_max_drawdown_pct")
    new_drawdown = primary_test.get("median_max_drawdown_pct")
    drawdown_change = (round(new_drawdown - old_drawdown, 1)
                       if None not in (old_drawdown, new_drawdown) else None)
    all_primary = results["top_half"]["all"]["primary"]
    checks = [
        _check("completed_episodes", "Enough completed selected episodes",
               all_primary["completed_episodes"] >= POLICY["minimum_completed_episodes"],
               all_primary["completed_episodes"], POLICY["minimum_completed_episodes"]),
        _check("unique_issuers", "Enough distinct selected companies",
               all_primary["unique_issuers"] >= POLICY["minimum_unique_issuers"],
               all_primary["unique_issuers"], POLICY["minimum_unique_issuers"]),
        _check("final_test_episodes", "Enough completed final-test episodes",
               primary_test["completed_episodes"] >= POLICY["minimum_final_test_episodes"],
               primary_test["completed_episodes"], POLICY["minimum_final_test_episodes"]),
        _check("final_test_rate", "Final-test success rate",
               new_rate is not None and new_rate >= POLICY["minimum_final_test_success_rate_pct"],
               new_rate, POLICY["minimum_final_test_success_rate_pct"]),
        _check("final_test_improvement", "Final-test improvement over all Buys",
               improvement is not None and improvement >= POLICY["minimum_final_test_improvement_pp"],
               improvement, POLICY["minimum_final_test_improvement_pp"]),
        _check("drawdown", "Median drawdown does not materially deteriorate",
               drawdown_change is not None and drawdown_change >= -POLICY["maximum_drawdown_deterioration_pp"],
               drawdown_change, f">= -{POLICY['maximum_drawdown_deterioration_pp']} pp"),
    ]
    statistically_eligible = all(row["passed"] for row in checks)
    full_evidence = bool(selectors["top_half"]) and all(
        (row.get("capability_moat") or {}).get("full_capability_definition_complete")
        for row in selectors["top_half"]
    )
    promotion_allowed = statistically_eligible and full_evidence
    return {
        "ok": True,
        "version": VERSION,
        "objective": "Improve +30%-within-one-year precision by ranking only existing Buy episodes.",
        "primary_selector": "top_half",
        "component_weights": COMPONENT_WEIGHTS,
        "policy": POLICY,
        "chronological_split": {"development_pct": 60, "validation_pct": 20,
                                "final_unseen_pct": 20},
        "baseline_all_buys": baseline,
        "selectors": results,
        "final_test_top_half_improvement_pp": improvement,
        "promotion_checks": checks,
        "statistically_eligible": statistically_eligible,
        "full_capability_evidence_complete": full_evidence,
        "promotion_allowed": promotion_allowed,
        "research_status": (
            "ready_for_manual_review" if promotion_allowed else "closed_rejected"
        ),
        "decision": (
            "Ready for manual promotion review."
            if promotion_allowed else
            "Do not promote: the frozen top-half selector reduced final-test "
            "precision and the required dated competition evidence is unavailable."
        ),
        "next_reconsideration": (
            "Only after genuinely new shadow outcomes and dated standardized "
            "competition, switching-cost and technical-milestone evidence exist."
        ),
        "coverage": {
            "buy_episodes": len(episodes),
            "selector_eligible": sum(row["capability_moat"]["eligible_for_selector"]
                                     for row in episodes),
            "direct_competition_evidence": sum(
                row["capability_moat"]["direct_competition_evidence_available"]
                for row in episodes),
        },
        "episode_scores": [{
            "ticker": row.get("ticker"), "company": row.get("company"),
            "entry_date": row.get("entry_date"), "sector": row.get("sector"),
            "official_score": row.get("score"), **row["capability_moat"],
        } for row in episodes],
        "missing_for_full_replay": [
            "dated standardized credible-competitor counts",
            "dated product switching-cost and market-position evidence",
            "dated technical milestone completion evidence",
        ],
        "production_effect": "none",
        "score_effect": 0,
        "verdict_effect": "none",
        "interpretation": "This is a concentration challenger among existing Buys. Missing competition evidence cannot be guessed, and no result changes an official recommendation without a separately reviewed promotion.",
    }
