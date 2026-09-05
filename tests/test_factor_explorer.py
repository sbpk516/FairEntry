from datetime import date, timedelta

import duckdb
import pytest

from fairentry.backtest.factor_explorer import (
    attach_warehouse_factors,
    compare_factors,
    run_factor_explorer,
)


def _observation(index: int):
    decision = date(2000, 1, 1) + timedelta(days=index * 35)
    succeeds = index % 2 == 0
    hit = {"20": 70, "25": 90, "30": 110} if succeeds else {
        "20": None, "25": None, "30": None,
    }
    factor = 20 if succeeds else -20
    return {
        "observation_id": f"{decision}:S{index}",
        "issuer_key": f"ISSUER{index}",
        "ticker": f"S{index}",
        "decision_date": decision.isoformat(),
        "entry_date": (decision + timedelta(days=1)).isoformat(),
        "verdict": "Buy",
        "categories": [{
            "id": "confirmation",
            "score": 70,
            "items": [
                {"metric": "relative_strength_score", "actual": 80 if succeeds else 20},
                {"metric": "trend_regime_score", "actual": 90 if succeeds else 30},
            ],
        }],
        "research_factors": {
            "revenue_growth_change_pp": factor,
            "operating_margin_change_qoq_pp": factor / 2,
            "fcf_margin_pct": 25 if succeeds else -5,
            "pe_to_revenue_growth": 0.8 if succeeds else 3.0,
        },
        "return_milestones": {
            "first_hit_days": hit,
            "last_observed_days": 1200,
            "last_observed_date": (decision + timedelta(days=1201)).isoformat(),
            "terminal_days": None,
            "max_drawdown_pct_by_horizon": {
                "365": -8 if succeeds else -30,
                "730": -8 if succeeds else -30,
                "1095": -8 if succeeds else -30,
            },
        },
    }


def test_factor_comparison_separates_successes_and_failures():
    comparison = {row["id"]: row for row in compare_factors(
        [_observation(index) for index in range(20)]
    )}

    growth = comparison["growth_deceleration"]
    valuation = comparison["valuation_to_growth"]
    assert growth["coverage_pct"] == 100.0
    assert growth["success_distribution"]["median"] == 20
    assert growth["failure_distribution"]["median"] == -20
    assert growth["direction_matches_hypothesis"] is True
    assert valuation["success_distribution"]["median"] == 0.8
    assert valuation["failure_distribution"]["median"] == 3.0
    assert valuation["direction_matches_hypothesis"] is True


def test_walk_forward_thresholds_are_learned_on_older_folds_only():
    result = run_factor_explorer(
        [_observation(index) for index in range(100)],
        policy={"minimum_training_completed": 10,
                "minimum_training_unique_issuers": 10,
                "minimum_inner_validation_completed": 5,
                "minimum_total_oos_completed": 20},
    )
    walk = result["walk_forward"]

    assert walk["folds_evaluated"] == 3
    assert walk["out_of_sample_baseline"]["success_rate_pct"] == 50.0
    assert walk["out_of_sample_selected"]["success_rate_pct"] == 100.0
    assert walk["out_of_sample_improvement_pp"] == 50.0
    assert walk["research_signal"] is True
    assert walk["production_effect"] == "none"
    assert walk["deployable_rule"] is False
    assert walk["hypothesis_leaderboard"]
    assert result["unavailable_factors"][0]["status"] == "not_backtestable"
    for fold in walk["folds"]:
        assert fold["training"]["last"] < fold["test"]["first"]


def test_warehouse_enrichment_uses_only_filings_and_prices_available_by_buy_date():
    connection = duckdb.connect(":memory:")
    connection.execute("""
        CREATE TABLE sfa_fundamentals(
          ticker VARCHAR, dimension VARCHAR, datekey DATE, reportperiod DATE,
          calendardate DATE, revenue DOUBLE, opinc DOUBLE, fcf DOUBLE,
          epsdil DOUBLE, sharesbas DOUBLE, debtnc DOUBLE, assets DOUBLE,
          gp DOUBLE, ncfo DOUBLE, netinc DOUBLE, debt DOUBLE, cashneq DOUBLE,
          ebit DOUBLE, intexp DOUBLE, roic DOUBLE
        )
    """)
    # Sixteen quarters allow a true current-TTM versus TTM-three-years-ago
    # diluted-EPS comparison. The final nine revenue values preserve the
    # existing growth assertions below.
    revenues = [20, 22, 24, 26, 30, 35, 40,
                50, 55, 60, 65, 75, 82.5, 90, 97.5, 120]
    for index, revenue in enumerate(revenues):
        period = date(2020, 6, 30) + timedelta(days=index * 91)
        opinc = revenue * (0.20 if index == len(revenues) - 1 else 0.10)
        connection.execute(
            "INSERT INTO sfa_fundamentals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ["ABC", "ARQ", period + timedelta(days=30), period,
             period, revenue, opinc, None, index + 1, 100 + index, 12 + index,
             120 + index * 10, None, None, None, None, None, None, None, None],
        )
    # A later amendment to an older quarter must not enter the original
    # Buy-date lag calculation.
    amended_period = date(2022, 3, 31) + timedelta(days=7 * 91)
    for index in range(12):
        period = date(2020, 12, 31) + timedelta(days=index * 91)
        art_revenue = 40 + index * 4
        connection.execute(
            "INSERT INTO sfa_fundamentals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ["ABC", "ART", period + timedelta(days=30), period, period,
             art_revenue, art_revenue * .10, 8 + index, None, None, None,
             160 + index * 3, art_revenue * .40, 10 + index, 9 + index,
             30, 10, 15, -3, .12 + index * .005],
        )
    connection.execute(
        "INSERT INTO sfa_fundamentals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ["ABC", "ARQ", "2025-02-01", amended_period, amended_period,
         1_000, 900, None, 100, 1_000, 900, 1_000,
         None, None, None, None, None, None, None, None],
    )
    connection.execute(
        "INSERT INTO sfa_fundamentals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ["ABC", "ART", "2024-04-01", "2023-12-31", "2023-12-31",
         100, 10, 20, None, None, None, 200, 40, 24, 20, 30, 10, 15, -3, .20],
    )
    connection.execute("CREATE TABLE sfa_daily(ticker VARCHAR,date DATE,pe DOUBLE,ps DOUBLE,pb DOUBLE)")
    connection.execute("INSERT INTO sfa_daily VALUES ('ABC','2025-01-01',30,3,4)")
    connection.execute("""
        CREATE TABLE sfa_price_features(
          ticker VARCHAR,date DATE,close DOUBLE,close_3m DOUBLE,sma50 DOUBLE,sma200 DOUBLE
        )
    """)
    connection.executemany(
        "INSERT INTO sfa_price_features VALUES (?,?,?,?,?,?)",
        [("ABC", "2025-01-14", 110, 100, 100, 90),
         ("XLK", "2025-01-14", 120, 100, 110, 105),
         ("SPY", "2025-01-14", 105, 100, 102, 98)],
    )
    connection.execute("CREATE TABLE sfa_fund_prices(ticker VARCHAR,date DATE,closeadj DOUBLE)")
    benchmark_rows = []
    for offset in range(64):
        observed = date(2025, 1, 14) - timedelta(days=offset)
        benchmark_rows.extend([
            ("XLK", observed, 120 - 20 * offset / 63),
            ("SPY", observed, 105 - 5 * offset / 63),
        ])
    connection.executemany("INSERT INTO sfa_fund_prices VALUES (?,?,?)", benchmark_rows)
    observation = {
        "observation_id": "ABC:2025-01-15",
        "ticker": "ABC",
        "decision_date": "2025-01-15",
        "entry_date": "2025-01-16",
        "verdict": "Buy",
        "sector": "Technology",
    }

    status = attach_warehouse_factors([observation], connection)
    factors = observation["research_factors"]

    assert status == {"observations": 1, "enriched": 1}
    assert factors["revenue_growth_yoy_pct"] == pytest.approx(60)
    assert factors["revenue_cagr_3y_pct"] == pytest.approx(35.7209)
    assert factors["fcf_cagr_3y_pct"] == pytest.approx(35.7209)
    assert factors["positive_fcf_history_pct"] == pytest.approx(100)
    assert factors["fcf_history_observations"] == 12
    assert factors["prior_revenue_growth_yoy_pct"] == pytest.approx(50)
    assert factors["revenue_growth_change_pp"] == pytest.approx(10)
    assert factors["operating_margin_change_qoq_pp"] == pytest.approx(10)
    assert factors["fcf_margin_pct"] == pytest.approx(20)
    assert factors["net_profit_margin_pct"] == pytest.approx(20)
    assert factors["net_profit_margin_change_yoy_pp"] is not None
    assert factors["gross_profitability_pct"] == pytest.approx(20)
    assert factors["cash_conversion_pct"] == pytest.approx(120)
    assert factors["accruals_to_assets_pct"] == pytest.approx(-2)
    assert factors["net_debt_to_fcf"] == pytest.approx(1)
    assert factors["interest_coverage"] == pytest.approx(5)
    assert factors["roic_pct"] == pytest.approx(20)
    assert factors["roic_5y_median_pct"] == pytest.approx(15)
    assert factors["roic_5y_observations"] == 13
    assert factors["pe_to_revenue_growth"] == pytest.approx(0.5)
    assert factors["positive_eps_quarters_pct"] == pytest.approx(100)
    assert factors["eps_improving_quarters_pct"] == pytest.approx(100)
    assert factors["eps_ttm_diluted"] == pytest.approx(58)
    assert factors["eps_ttm_diluted_3y_ago"] == pytest.approx(10)
    assert factors["eps_cagr_3y_pct"] == pytest.approx(79.6702)
    assert factors["eps_recovery"] is False
    assert factors["eps_deterioration"] is False
    assert factors["revenue_growth_positive_quarters_pct"] == pytest.approx(100)
    assert factors["trailing_pe"] == pytest.approx(30)
    assert factors["price_to_sales"] == pytest.approx(3)
    assert factors["market_return_3m_pct"] == pytest.approx(5)
    assert factors["sector_return_3m_pct"] == pytest.approx(20)
    assert factors["sector_minus_market_3m_pct"] == pytest.approx(15)
    assert factors["price_to_sma50_abs_pct"] == pytest.approx(10)
    assert factors["price_above_sma200_pct"] == pytest.approx(22.2222)
