from datetime import date, timedelta

from fairentry.backtest.valuation_research import (
    POLICIES,
    book_is_relevant,
    evaluate_policy,
    run_valuation_research,
)


def _row(index: int, success: bool):
    decision = date(2000, 1, 1) + timedelta(days=index * 60)
    methods = ([
        {"key": "peer_ps", "fair": 150, "decision_status": "tested"},
        {"key": "fcf", "fair": 140, "decision_status": "tested"},
        {"key": "book", "fair": 200, "decision_status": "tested"},
    ] if success else [
        {"key": "peer_ps", "fair": 110, "decision_status": "tested"},
        {"key": "book", "fair": 200, "decision_status": "tested"},
    ])
    return {
        "observation_id": f"{decision}:S{index}",
        "issuer_key": f"ISSUER{index}",
        "ticker": f"S{index}",
        "sector": "Technology",
        "industry": "Software - Application",
        "strategy_key": "quality_growth" if index % 3 else "deep_value",
        "decision_date": decision.isoformat(),
        "entry_date": (decision + timedelta(days=1)).isoformat(),
        "entry_price": 100,
        "verdict": "Buy",
        "categories": [
            {"id": "quality", "items": [
                {"id": "gross_margin_vs_sector", "score": 80, "actual": 70},
            ]},
            {"id": "growth", "items": [
                {"id": "revenue_growth", "score": 80, "actual": 20},
            ]},
        ],
        "valuation": {"methods": methods},
        "research_factors": {
            "return_on_equity_pct": 20,
            "enterprise_value_to_sales": 3,
            "enterprise_value_to_equity": 1.2,
            "sector_median_enterprise_value_to_sales": 4,
            "revenue_growth_yoy_pct": 20,
            "fcf_margin_pct": 15,
            "revenue_growth_volatility_pct": 5,
            "debt_to_assets_change_yoy_pp": -2,
        },
        "return_milestones": {
            "first_hit_days": {"20": 80 if success else None,
                               "25": 100 if success else None,
                               "30": 120 if success else None},
            "last_observed_days": 1200,
            "last_observed_date": (decision + timedelta(days=1201)).isoformat(),
            "terminal_days": None,
            "max_drawdown_pct_by_horizon": {
                "365": -10 if success else -30,
                "730": -10 if success else -30,
                "1095": -10 if success else -30,
            },
        },
    }


def test_book_relevance_is_explicit_and_sector_aware():
    assert book_is_relevant({"sector": "Financial Services", "industry": "Banks"})
    assert book_is_relevant({"sector": "Consumer Cyclical", "industry": "Auto Manufacturers"})
    assert not book_is_relevant({"sector": "Technology", "industry": "Software - Application"})


def test_pb_relevance_removes_misleading_book_value_from_buy_support():
    policy = next(row for row in POLICIES if row["id"] == "pb_relevance")
    failure = evaluate_policy(_row(1, False), policy)
    success = evaluate_policy(_row(2, True), policy)

    assert failure["methods"] == ["peer_ps"]
    assert failure["passes"] is False
    assert success["passes"] is True
    assert "book" not in success["methods"]


def test_valuation_research_preserves_baseline_and_reports_unseen_strategy_results():
    observations = [_row(index, index % 2 == 0) for index in range(20)]
    result = run_valuation_research(observations)
    relevance = next(row for row in result["challengers"]
                     if row["id"] == "pb_relevance")

    assert result["ok"] is True
    assert result["production_effect"] == "none"
    assert result["baseline_changed"] is False
    assert result["baseline"]["all"]["primary"]["success_rate_pct"] == 50.0
    assert relevance["results"]["all"]["primary"]["success_rate_pct"] == 100.0
    assert set(relevance["results"]["by_strategy"]) == {"deep_value", "quality_growth"}
    variable = next(row for row in result["challengers"]
                    if row["id"] == "variable_fcf_multiple")
    assert variable["production_eligible"] is False
