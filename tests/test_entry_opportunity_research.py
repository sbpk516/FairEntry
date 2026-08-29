from pathlib import Path

from fairentry.backtest.entry_opportunity_research import (
    attach_entry_opportunity_factors,
    run_entry_opportunity_research,
)


def _row(
    ticker,
    observed,
    verdict,
    *,
    price=100,
    fair_values=(160, 150),
    score=80,
    category_score=80,
    hits=None,
    observed_days=1200,
):
    return {
        "observation_id": f"{observed}:{ticker}",
        "issuer_key": ticker,
        "ticker": ticker,
        "company": f"{ticker} Corp",
        "sector": "Technology",
        "industry": "Software - Application",
        "decision_date": observed,
        "entry_date": observed,
        "entry_price": price,
        "verdict": verdict,
        "score": score,
        "vetoes": [],
        "growth_qualification": {"qualified": True},
        "categories": [
            {"id": category_id, "score": category_score, "items": []}
            for category_id in ("quality", "survival", "growth")
        ],
        "valuation": {"methods": [
            {"key": "peer_ps", "fair": fair_values[0], "decision_status": "tested"},
            {"key": "fcf", "fair": fair_values[1], "decision_status": "tested"},
            # P/B must not lower the conservative value for a software company.
            {"key": "book", "fair": 80, "decision_status": "tested"},
        ]},
        "research_factors": {"return_on_equity_pct": 20},
        "return_milestones": ({
            "first_hit_days": {
                "30": (hits or {}).get("30"),
                "50": (hits or {}).get("50"),
                "100": (hits or {}).get("100"),
            },
            "last_observed_days": observed_days,
            "terminal_days": None,
            "max_drawdown_pct_by_horizon": {
                "365": -12, "730": -15, "1095": -18,
            },
        } if verdict == "Buy" else None),
    }


def test_conservative_upside_uses_earliest_buy_and_stable_point_in_time_evidence():
    observations = [
        _row("EARLY", "2019-12-01", "Watch"),
        _row(
            "EARLY", "2020-01-01", "Buy", price=90,
            hits={"30": 90, "50": 400, "100": 900},
        ),
        # Repeated Buy belongs to the same episode and must not restart the clock.
        _row(
            "EARLY", "2020-02-01", "Buy", price=105,
            hits={"30": 200, "50": 500, "100": 1000},
        ),
    ]

    result = run_entry_opportunity_research(observations, step_days=30)

    assert result["production_effect"] == "none"
    assert result["one_official_score"] is True
    assert result["coverage"]["earliest_buy_episodes"] == 1
    detail = result["episode_details"][0]
    assert detail["earliest_qualifying_date"] == "2020-01-01"
    assert detail["entry_price"] == 90
    assert detail["conservative_fair_value"] == 150
    assert detail["conservative_upside_pct"] == 66.67
    assert detail["relevant_method_count"] == 2
    assert detail["stable_value_and_no_thesis_deterioration"] is True

    threshold = next(
        row for row in result["thresholds"]
        if row["minimum_conservative_upside_pct"] == 60
    )
    outcomes = threshold["all_history"]["stable_value_and_thesis"]
    assert outcomes["primary_30_within_one_year"]["success_rate_pct"] == 100
    assert outcomes["secondary_50_within_two_years"]["success_rate_pct"] == 100
    assert outcomes["long_term_100_within_three_years"]["success_rate_pct"] == 100
    assert threshold["research_signal"] is False
    assert threshold["research_status"] == "Too few completed newest-period cases"
    assert "No conservative-upside band" in result["research_conclusion"]


def test_deteriorating_fair_value_remains_visible_but_does_not_pass_stable_subset():
    observations = [
        _row("FALL", "2020-01-01", "Watch", fair_values=(170, 150)),
        _row(
            "FALL", "2020-02-01", "Buy", price=60, fair_values=(120, 105),
            hits={"30": None, "50": None, "100": None},
        ),
    ]

    enrichment = attach_entry_opportunity_factors(observations)
    factors = observations[-1]["entry_opportunity_factors"]
    assert enrichment["attached"] == 2
    assert factors["valuation"]["conservative_upside_pct"] == 75
    assert factors["checks"]["stable_business_value"] is False
    assert factors["stable_value_and_no_thesis_deterioration"] is False
    assert any("fair value fell" in reason for reason in factors["reasons"])

    result = run_entry_opportunity_research(observations, step_days=30)
    threshold = next(
        row for row in result["thresholds"]
        if row["minimum_conservative_upside_pct"] == 60
    )
    assert threshold["all_history"]["upside_only"][
        "primary_30_within_one_year"
    ]["completed_episodes"] == 1
    assert threshold["all_history"]["stable_value_and_thesis"][
        "primary_30_within_one_year"
    ]["completed_episodes"] == 0


def test_missing_prior_snapshot_is_not_assumed_stable():
    observations = [
        _row(
            "NEW", "2020-01-01", "Buy", price=90,
            hits={"30": 100, "50": None, "100": None},
        )
    ]
    attach_entry_opportunity_factors(observations)
    factors = observations[0]["entry_opportunity_factors"]
    assert factors["evidence_complete"] is False
    assert factors["stable_value_and_no_thesis_deterioration"] is False
    assert any("No comparable prior valuation" in reason for reason in factors["reasons"])


def test_backtest_ui_explains_earliest_entry_and_longer_outcomes():
    html = (Path(__file__).resolve().parents[1] / "web" / "backtest.html").read_text(
        encoding="utf-8"
    )
    assert "function entryOpportunityResearch(b)" in html
    assert "Could FairEntry have identified the Buy earlier?" in html
    assert "+30% within one year remains the main test" in html
    assert "+50% within two years" in html
    assert "+100% within three years" in html
    assert "research_conclusion" in html
    assert "Newest unseen" in html
    assert "scoreBandCalibration(b)+entryOpportunityResearch(b)" in html
