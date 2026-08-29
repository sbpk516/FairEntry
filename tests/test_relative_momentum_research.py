from datetime import date, timedelta

import duckdb

from fairentry.analytics.relative_momentum import calculate_relative_momentum
from fairentry.backtest.relative_momentum_research import (
    attach_relative_momentum_factors,
    calculate_history_relative_momentum,
    load_experiment_registry,
    run_relative_momentum_research,
)


def _series(direction=1, points=160):
    values = []
    for index in range(points):
        move = max(0, index - 120) * direction
        values.append(100 + move)
    return values


def _observation(index, classification):
    observed = date(2000, 1, 1) + timedelta(days=index * 50)
    success = classification == "improving"
    return {
        "ticker": f"T{index}",
        "company": f"Company {index}",
        "issuer_key": f"issuer-{index}",
        "sector": "Technology",
        "entry_date": observed.isoformat(),
        "decision_date": observed.isoformat(),
        "entry_price": 100,
        "verdict": "Buy",
        "strategy_key": "quality_growth",
        "relative_momentum_factors": {
            "available": True,
            "classification": classification,
            "display_direction": "Supportive" if success else "Contradictory",
            "sector_benchmark": "XLK",
            "as_of": (observed - timedelta(days=1)).isoformat(),
            "aligned_sessions": 160,
            "stock_return_6m_pct": 30 if success else -20,
            "sector_return_6m_pct": 5,
            "relative_momentum_6m_pct": 25 if success else -25,
            "prior_relative_momentum_6m_pct": 10 if success else -10,
            "relative_momentum_change_1m_pp": 15 if success else -15,
        },
        "return_milestones": {
            "first_hit_days": {"25": 80 if success else None,
                               "30": 100 if success else None},
            "last_observed_days": 800,
            "last_observed_date": (observed + timedelta(days=800)).isoformat(),
            "terminal_days": None,
            "max_drawdown_pct_by_horizon": {"365": -10 if success else -30,
                                               "730": -15 if success else -35},
        },
    }


def test_shared_relative_momentum_definition_is_transparent_and_unscored():
    result = calculate_relative_momentum(_series(1), [100] * 160)

    assert result["available"] is True
    assert result["classification"] == "improving"
    assert result["display_direction"] == "Supportive"
    assert result["relative_momentum_6m_pct"] > 0
    assert result["relative_momentum_change_1m_pp"] > 0
    assert result["score_effect"] == 0
    assert result["verdict_effect"] == "none"
    assert result["future_data_used"] is False


def test_relative_momentum_can_be_deteriorating_or_unavailable():
    deteriorating = calculate_relative_momentum(_series(-1), [100] * 160)
    unavailable = calculate_relative_momentum([100] * 100, [100] * 100)

    assert deteriorating["classification"] == "deteriorating"
    assert deteriorating["display_direction"] == "Contradictory"
    assert unavailable["available"] is False
    assert unavailable["classification"] == "unavailable"


def test_history_calculation_aligns_stock_and_sector_dates():
    start = date(2023, 1, 1)
    stock = [{"date": (start + timedelta(days=i)).isoformat(), "close": value}
             for i, value in enumerate(_series(1))]
    sector = [{"date": (start + timedelta(days=i)).isoformat(), "close": 100}
              for i in range(160)]
    sector.pop(20)

    result = calculate_history_relative_momentum(stock, sector)

    assert result["aligned_sessions"] == 159
    assert result["as_of"] == stock[-1]["date"]
    assert result["available"] is True


def test_warehouse_enrichment_uses_only_sessions_before_first_buy():
    connection = duckdb.connect(":memory:")
    connection.execute("""
      CREATE TABLE sfa_prices(
        ticker VARCHAR,date DATE,close DOUBLE,closeadj DOUBLE
      )
    """)
    connection.execute("""
      CREATE TABLE sfa_fund_prices(
        ticker VARCHAR,date DATE,closeadj DOUBLE
      )
    """)
    buy_date = date(2024, 8, 1)
    start = buy_date - timedelta(days=180)
    for index in range(180):
        observed = start + timedelta(days=index)
        connection.execute(
            "INSERT INTO sfa_prices VALUES (?,?,?,?)",
            ["SAFE", observed, _series(1, 180)[index], _series(1, 180)[index]],
        )
        connection.execute(
            "INSERT INTO sfa_fund_prices VALUES (?,?,?)",
            ["XLK", observed, 100],
        )
    connection.execute(
        "INSERT INTO sfa_prices VALUES (?,?,?,?)",
        ["SAFE", buy_date, 1000, 1000],
    )
    connection.execute(
        "INSERT INTO sfa_fund_prices VALUES (?,?,?)",
        ["XLK", buy_date, 1],
    )
    episodes = [{
        "ticker": "SAFE",
        "sector": "Technology",
        "issuer_key": "safe",
        "entry_date": buy_date.isoformat(),
        "decision_date": buy_date.isoformat(),
    }]

    enrichment = attach_relative_momentum_factors(episodes, connection)
    factor = episodes[0]["relative_momentum_factors"]

    assert enrichment["future_data_used"] is False
    assert factor["available"] is True
    assert factor["as_of"] < buy_date.isoformat()
    assert factor["stock_return_6m_pct"] < 100
    assert factor["sector_benchmark"] == "XLK"


def test_frozen_experiment_reports_chronological_results_and_trial_count():
    observations = [
        _observation(index, "improving" if index % 2 == 0 else "deteriorating")
        for index in range(120)
    ]

    result = run_relative_momentum_research(observations, step_days=30)

    assert result["ok"] is True
    assert result["production_effect"] == "none"
    assert result["one_official_score"] is True
    assert result["not_validated"] is True
    assert result["promotion_eligible"] is False
    assert result["experiment_registry"]["definitions_tested"] == 1
    assert result["anti_overfitting_controls"]["chronological_development_pct"] == 60
    assert set(result["results"]) == {
        "all_history", "development", "validation", "final_historical_test"
    }
    all_history = result["results"]["all_history"]
    supportive = all_history["groups"]["improving"]["outcomes"][
        "primary_30_within_one_year"
    ]
    contradictory = all_history["groups"]["deteriorating"]["outcomes"][
        "primary_30_within_one_year"
    ]
    assert supportive["success_rate_pct"] == 100
    assert contradictory["success_rate_pct"] == 0
    assert all_history["supportive_minus_contradictory_pp"] == 100
    assert len(result["episode_details"]) == 120
    assert load_experiment_registry()["experiments"][0]["id"] == result["experiment_id"]
