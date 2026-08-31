from datetime import date, timedelta
from pathlib import Path

from fairentry.backtest.capability_moat_research import (
    run_capability_moat_research,
    score_capability_moat,
)


def _factors(strong=True):
    if strong:
        return {
            "revenue_growth_positive_quarters_pct": 100,
            "positive_eps_quarters_pct": 100,
            "operating_margin_change_qoq_pp": 2,
            "fcf_margin_pct": 18,
            "gross_margin_pct": 70,
            "revenue_growth_min_pct": 20,
            "rnd_to_revenue_pct": 15,
            "capex_to_revenue_pct": 8,
            "roic_pct": 25,
            "gross_profitability_pct": 35,
            "revenue_growth_change_pp": 10,
        }
    return {
        "revenue_growth_positive_quarters_pct": 25,
        "positive_eps_quarters_pct": 25,
        "operating_margin_change_qoq_pp": -5,
        "fcf_margin_pct": -5,
        "gross_margin_pct": 15,
        "revenue_growth_min_pct": -20,
        "rnd_to_revenue_pct": 25,
        "capex_to_revenue_pct": 20,
        "roic_pct": -5,
        "gross_profitability_pct": 2,
        "revenue_growth_change_pp": -15,
    }


def _competition(observed):
    return {
        "observed_at": observed,
        "sources": ["dated SEC filing"],
        "credible_competitors": 2,
        "market_position": "leader",
        "switching_cost": "high",
    }


def _observation(period, strong):
    observed = date(2000, 1, 1) + timedelta(days=period * 40)
    hit = 100 if strong else None
    drawdown = -8 if strong else -30
    return {
        "observation_id": f"{observed}:{'S' if strong else 'W'}{period}",
        "issuer_key": f"{'strong' if strong else 'weak'}-{period}",
        "ticker": f"{'S' if strong else 'W'}{period}",
        "company": "Strong capability" if strong else "Unproven capability",
        "sector": "Industrials",
        "decision_date": observed.isoformat(),
        "entry_date": observed.isoformat(),
        "entry_price": 100,
        "score": 75,
        "verdict": "Buy",
        "research_factors": _factors(strong),
        "capability_evidence": _competition(observed.isoformat()),
        "return_milestones": {
            "first_hit_days": {str(target): hit for target in (20, 25, 28, 30, 35)},
            "last_observed_days": 1200,
            "last_observed_date": (observed + timedelta(days=1200)).isoformat(),
            "terminal_days": None,
            "max_drawdown_pct_by_horizon": {
                "365": drawdown, "730": drawdown, "1095": drawdown,
            },
        },
        "_tuning_outcome": {
            "return_pct": 40 if strong else -25,
            "alpha_pct": 25 if strong else -30,
            "max_drawdown_pct": drawdown,
        },
    }


def test_score_requires_execution_and_commercial_proof_not_spending_alone():
    strong = _observation(0, True)
    weak = _observation(0, False)

    strong_result = score_capability_moat(strong)
    weak_result = score_capability_moat(weak)

    assert strong_result["score"] >= 80
    assert strong_result["eligible_for_selector"] is True
    assert strong_result["evidence_coverage_pct"] == 100
    assert weak_result["eligible_for_selector"] is False
    assert weak_result["gates"]["proven_execution"] is False
    assert weak_result["gates"]["commercial_product_proof"] is False
    assert strong_result["score_effect"] == 0
    assert strong_result["verdict_effect"] == "none"


def test_future_or_unsourced_competition_evidence_is_unavailable():
    row = _observation(0, True)
    row["capability_evidence"]["observed_at"] = "2099-01-01"

    result = score_capability_moat(row)

    competition = next(component for component in result["components"]
                       if component["id"] == "competitive_scarcity")
    assert competition["available"] is False
    assert result["evidence_coverage_pct"] == 85
    assert result["full_capability_definition_complete"] is False


def test_selector_reduces_each_buy_cohort_and_reports_unseen_precision():
    observations = [
        _observation(period, strong)
        for period in range(20)
        for strong in (True, False)
    ]

    result = run_capability_moat_research(observations, step_days=30)

    assert result["ok"] is True
    assert result["production_effect"] == "none"
    assert result["baseline_all_buys"]["all"]["primary"]["success_rate_pct"] == 50.0
    assert result["selectors"]["top_half"]["all"]["primary"]["success_rate_pct"] == 100.0
    assert result["selectors"]["top_half"]["test"]["primary"]["success_rate_pct"] == 100.0
    assert result["final_test_top_half_improvement_pp"] == 50.0
    assert result["coverage"]["buy_episodes"] == 40
    assert result["coverage"]["selector_eligible"] == 20
    assert result["score_effect"] == 0
    assert result["verdict_effect"] == "none"


def test_backtest_ui_displays_capability_selector_without_score_effect():
    html = (Path(__file__).resolve().parents[1] / "web" / "backtest.html").read_text(
        encoding="utf-8"
    )

    assert "function capabilityMoatResearch" in html
    assert "Can rare capability improve Buy precision?" in html
    assert "Official recommendation effect" in html
