from datetime import date, timedelta

import pytest

from fairentry.backtest.research_cycle import (
    run_predictive_rule_research,
    validate_rules,
)


def _observation(index: int):
    decision = date(2000, 1, 1) + timedelta(days=index * 35)
    succeeds = index % 2 == 0
    growth = 85 if succeeds else 45
    hit = {"20": 80, "25": 100, "30": 120} if succeeds else {
        "20": None, "25": None, "30": None,
    }
    return {
        "observation_id": f"{decision}:S{index}",
        "issuer_key": f"ISSUER{index}",
        "ticker": f"S{index}",
        "company": f"Stock {index}",
        "decision_date": decision.isoformat(),
        "entry_date": (decision + timedelta(days=1)).isoformat(),
        "verdict": "Buy",
        "vetoes": [],
        "growth_qualification": {"qualified": succeeds},
        "categories": [
            {"id": "survival", "score": 80, "items": []},
            {"id": "growth", "score": growth, "items": []},
            {"id": "valuation", "score": 75, "items": []},
            {"id": "confirmation", "score": 60, "items": []},
        ],
        "return_milestones": {
            "first_hit_days": hit,
            "last_observed_days": 1200,
            "last_observed_date": (decision + timedelta(days=1201)).isoformat(),
            "terminal_days": None,
            "max_drawdown_pct_by_horizon": {
                "365": -10 if succeeds else -30,
                "730": -10 if succeeds else -30,
                "1095": -10 if succeeds else -30,
            },
        },
    }


def test_predictive_rule_is_selected_before_validation_and_reports_all_targets():
    rules = [{
        "id": "durable_growth",
        "label": "Durable growth",
        "conditions": [
            {"field": "category.growth", "op": ">=", "value": 70},
            {"field": "growth_qualified", "op": "==", "value": True},
        ],
    }]
    result = run_predictive_rule_research(
        [_observation(index) for index in range(20)],
        step_days=30,
        rules=rules,
        policy={
            "minimum_completed_episodes": 8,
            "minimum_unique_issuers": 8,
            "minimum_final_test_episodes": 2,
            "minimum_validation_improvement_pp": 10,
            "minimum_test_improvement_pp": 10,
            "minimum_test_success_rate_pct": 70,
        },
    )

    assert result["ok"] is True
    assert result["selected_rule"]["id"] == "durable_growth"
    assert result["baseline"]["all"]["primary"]["success_rate_pct"] == 50.0
    assert result["candidate"]["test"]["primary"]["success_rate_pct"] == 100.0
    assert result["candidate"]["all"]["target_matrix"]["25"]["365"]["reached"] == 10
    assert result["candidate"]["all"]["target_matrix"]["30"]["1095"]["success_rate_pct"] == 100.0
    assert result["promotion_eligible"] is True
    assert result["default_changed"] is False
    assert result["promotion"] == "manual_only"


def test_predictive_rules_reject_future_outcome_fields():
    with pytest.raises(ValueError, match="future/outcome"):
        validate_rules([{
            "id": "leaky",
            "conditions": [{"field": "return_milestones.max_return_pct",
                            "op": ">=", "value": 30}],
        }])


def test_missing_point_in_time_factor_excludes_candidate_instead_of_imputing_future_data():
    result = run_predictive_rule_research(
        [_observation(index) for index in range(10)],
        rules=[{
            "id": "eps",
            "conditions": [{"field": "metric.eps_growth_next_y", "op": ">=", "value": 15}],
        }],
        policy={"minimum_completed_episodes": 1, "minimum_unique_issuers": 1,
                "minimum_final_test_episodes": 1},
    )
    assert result["candidate"]["all"]["episodes"] == 0
    assert result["promotion_eligible"] is False
    assert result["leakage_audit"]["passed"] is True
