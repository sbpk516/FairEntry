from datetime import date, timedelta

import duckdb

from fairentry.backtest.movement_capacity_research import (
    attach_movement_capacity_factors,
    calculate_movement_capacity,
    run_movement_capacity_research,
)


def _history(points=252):
    start = date(2023, 1, 1)
    rows = []
    price = 100.0
    for index in range(points):
        # A rising series with enough positive and negative sessions to measure
        # directional volatility without relying on future prices.
        price *= 1.004 if index % 5 else .994
        rows.append({
            "date": (start + timedelta(days=index)).isoformat(),
            "close": price,
            "high": price * 1.01,
            "low": price * .99,
        })
    return rows


def _observation(index, range_pct, *, hit_30=None, hit_50=None,
                 trend=100, downside=.8):
    observed = date(2010, 1, 1) + timedelta(days=index * 40)
    return {
        "ticker": f"T{index}",
        "issuer_key": f"issuer-{index}",
        "company": f"Company {index}",
        "entry_date": observed.isoformat(),
        "decision_date": observed.isoformat(),
        "entry_price": 100,
        "verdict": "Buy",
        "strategy_key": "quality_growth",
        "movement_capacity_factors": {
            "available": True,
            "as_of": observed.isoformat(),
            "available_sessions": 252,
            "trailing_high": 100 + range_pct,
            "trailing_low": 100,
            "trailing_range_pct": range_pct,
            "range_band": (
                "under_20" if range_pct < 20 else
                "20_to_under_30" if range_pct < 30 else
                "30_to_under_45" if range_pct < 45 else
                "45_to_under_60" if range_pct < 60 else "60_plus"
            ),
            "movement_capacity_ratio": round(range_pct / 30, 2),
            "current_range_position_pct": 50,
            "realized_volatility_126d_pct": 25,
            "target_sigma_distance": 1.05,
            "trend_regime_score": trend,
            "downside_upside_volatility_63d": downside,
            "trend_downside_combination": (
                "constructive_" if trend >= 75 else "nonconstructive_"
            ) + ("controlled" if downside <= 1.1 else "uncontrolled"),
        },
        "return_milestones": {
            "first_hit_days": {"30": hit_30, "50": hit_50},
            "last_observed_days": 800,
            "last_observed_date": (observed + timedelta(days=800)).isoformat(),
            "terminal_days": None,
            "max_drawdown_pct_by_horizon": {"365": -12, "730": -18},
        },
    }


def test_calculate_movement_capacity_is_transparent_and_unscored():
    result = calculate_movement_capacity(_history())

    assert result["available"] is True
    assert result["available_sessions"] == 252
    assert result["trailing_range_pct"] > 0
    assert result["movement_capacity_ratio"] == round(
        result["trailing_range_pct"] / 30, 2
    )
    assert result["realized_volatility_126d_pct"] is not None
    assert result["trend_regime_score"] is not None
    assert result["score_effect"] == 0
    assert result["verdict_effect"] == "none"
    assert result["future_data_used"] is False


def test_short_history_is_unavailable_instead_of_passing():
    result = calculate_movement_capacity(_history(120))

    assert result["available"] is False
    assert result["available_sessions"] == 120
    assert "Fewer than 200" in result["reason"]


def test_warehouse_enrichment_never_reads_after_buy_date():
    connection = duckdb.connect(":memory:")
    connection.execute("""
      CREATE TABLE sfa_prices(
        ticker VARCHAR,date DATE,close DOUBLE,closeadj DOUBLE,
        high DOUBLE,low DOUBLE,volume DOUBLE
      )
    """)
    cutoff = date(2024, 8, 1)
    rows = []
    for offset in range(220):
        observed = cutoff - timedelta(days=219 - offset)
        price = 100 + offset / 10
        rows.append(("SAFE", observed, price, price, price + 1, price - 1, 1000))
    rows.append(("SAFE", cutoff + timedelta(days=1), 1000, 1000, 1100, 900, 1000))
    connection.executemany("INSERT INTO sfa_prices VALUES (?,?,?,?,?,?,?)", rows)
    episodes = [{
        "ticker": "SAFE",
        "issuer_key": "safe",
        "entry_date": cutoff.isoformat(),
        "decision_date": cutoff.isoformat(),
    }]

    enrichment = attach_movement_capacity_factors(episodes, connection)
    factors = episodes[0]["movement_capacity_factors"]

    assert enrichment["future_data_used"] is False
    assert factors["available"] is True
    assert factors["as_of"] == cutoff.isoformat()
    assert factors["trailing_high"] < 200
    assert factors["trailing_high_date"] <= cutoff.isoformat()


def test_research_reports_range_horizon_and_directional_comparisons():
    observations = [
        _observation(index, 15 if index < 4 else 35 if index < 8 else 65,
                     hit_30=None if index < 4 else 100,
                     hit_50=500 if index >= 6 else None,
                     trend=100 if index % 2 else 50,
                     downside=.8 if index % 3 else 1.4)
        for index in range(12)
    ]

    result = run_movement_capacity_research(observations, step_days=30)

    assert result["ok"] is True
    assert result["production_effect"] == "none"
    assert result["one_official_score"] is True
    assert result["not_validated"] is True
    assert result["coverage"]["earliest_buy_episodes"] == 12
    assert result["coverage"]["with_movement_capacity"] == 12
    assert [row["id"] for row in result["range_bands"]] == [
        "under_20", "20_to_under_30", "30_to_under_45",
        "45_to_under_60", "60_plus",
    ]
    comparison = next(row for row in result["cutoff_comparisons"]
                      if row["cutoff_pct"] == 30)
    low = comparison["below"]["all_history"]["primary_30_within_one_year"]
    higher = comparison["at_least"]["all_history"]["primary_30_within_one_year"]
    assert low["success_rate_pct"] == 0
    assert higher["success_rate_pct"] == 100
    assert result["range_bands"][2]["all_history"][
        "extended_30_within_two_years"
    ]["target_pct"] == 30
    assert result["range_bands"][2]["all_history"][
        "compounder_50_within_two_years"
    ]["target_pct"] == 50
    assert len(result["trend_downside_combinations"]) == 4
    assert len(result["episode_details"]) == 12
