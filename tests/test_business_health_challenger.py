from fairentry.backtest.business_health_challenger import (
    load_policy,
    run_challenger,
    score_factors,
    score_observation,
)


def _strong_factors():
    return {
        "roic_pct": 22,
        "roic_5y_median_pct": 20,
        "roic_5y_stddev_pct": 1.5,
        "roic_5y_observations": 20,
        "roic_change_3y_pp": 2,
        "operating_margin_pct": 24,
        "operating_margin_change_yoy_pp": 3,
        "fcf_margin_pct": 18,
        "cash_conversion_pct": 110,
        "positive_fcf_history_pct": 100,
        "fcf_history_observations": 12,
        "revenue_growth_yoy_pct": 25,
        "revenue_cagr_3y_pct": 25,
        "eps_growth_yoy_pct": 30,
        "eps_cagr_3y_pct": 30,
        "fcf_growth_yoy_pct": 30,
        "fcf_cagr_3y_pct": 30,
        "revenue_growth_change_pp": 10,
        "eps_growth_change_pp": 10,
    }


def _categories(score=80):
    return [{"id": key, "score": score} for key in (
        "quality", "survival", "growth", "valuation", "confirmation"
    )]


def test_frozen_policy_is_research_only_and_market_share_has_no_weight():
    policy = load_policy()
    assert policy["production_effect"] == "none"
    assert policy["market_share"]["decision_status"] == "information_only"
    assert round(sum(policy["growth_weights"].values()), 3) == 100


def test_strong_complete_business_health_scores_high_with_full_coverage():
    result = score_factors(_strong_factors())
    assert result["quality"]["score"] == 100
    assert result["growth"]["score"] == 100
    assert result["quality"]["coverage_pct"] == 100
    assert result["growth"]["coverage_pct"] == 100


def test_short_history_does_not_masquerade_as_roic_or_fcf_durability():
    factors = _strong_factors()
    factors["roic_5y_observations"] = 2
    factors["fcf_history_observations"] = 2
    result = score_factors(factors)
    roic_parts = result["detail"]["roic"]["components"]
    fcf_parts = result["detail"]["fcf_quality"]["components"]
    assert roic_parts["five_year_consistency"]["score"] is None
    assert fcf_parts["positive_history"]["score"] is None
    assert result["quality"]["coverage_pct"] < 100


def test_challenger_preserves_production_gates_and_never_mutates_verdict():
    row = {
        "verdict": "Watch",
        "categories": _categories(),
        "research_factors": _strong_factors(),
        "vetoes": [],
        "soft_gates": [{"id": "upside_below_target"}],
    }
    result = score_observation(row)
    assert result["score_band_verdict"] == "Buy"
    assert result["verdict"] == "Watch"
    assert row["verdict"] == "Watch"
    assert result["production_effect"] == "none"


def test_run_challenger_attaches_separate_result_and_reports_comparison():
    row = {
        "ticker": "TEST",
        "verdict": "Buy",
        "categories": _categories(),
        "research_factors": _strong_factors(),
        "vetoes": [],
        "soft_gates": [],
        "return_milestones": {
            "first_hit_days": {"30": 120},
            "last_observed_days": 365,
            "terminal_days": None,
        },
    }
    report = run_challenger([row])
    assert row["business_health_challenger"]["verdict"] == "Buy"
    assert report["baseline"]["hit_rate_pct"] == 100
    assert report["challenger"]["hit_rate_pct"] == 100
    assert report["production_effect"] == "none"
