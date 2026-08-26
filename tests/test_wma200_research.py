from datetime import date, timedelta

from fairentry.backtest.wma200_research import run_wma200_research


def _row(index: int, distance: float, success: bool, issuer: str | None = None):
    start = date(2000, 1, 1) + timedelta(days=index * 60)
    hits = {
        "10": 45 if success else None,
        "20": 90 if success else None,
        "25": 120 if success else None,
        "30": 160 if success else None,
    }
    return {
        "observation_id": f"{start}:S{index}",
        "issuer_key": issuer or f"ISSUER{index}",
        "ticker": f"S{index}",
        "decision_date": start.isoformat(),
        "entry_date": (start + timedelta(days=1)).isoformat(),
        "verdict": "Buy",
        "categories": [
            {"id": "quality", "score": 80},
            {"id": "survival", "score": 75},
            {"id": "growth", "score": 85},
        ],
        "vetoes": [],
        "research_factors": {"dist_200wma_pct": distance},
        "return_milestones": {
            "status": "observed",
            "first_hit_days": hits,
            "last_observed_days": 1200,
            "last_observed_date": (start + timedelta(days=1201)).isoformat(),
            "terminal_days": None,
            "max_drawdown_pct_by_horizon": {"90": -5, "365": -10 if success else -30},
        },
    }


def test_wma200_research_compares_strong_near_and_away_events():
    observations = [
        _row(0, 1.0, True),
        _row(1, -2.0, True),
        _row(2, 2.5, True),
        _row(3, -1.5, False),
        _row(4, 20.0, True),
        _row(5, 25.0, False),
        _row(6, -15.0, False),
        _row(7, 30.0, False),
    ]

    result = run_wma200_research(observations)
    primary = result["threshold_comparison"][0]

    assert result["production_effect"] == "none"
    assert primary["near"]["events"] == 4
    assert primary["near"]["one_year_targets"]["30"]["success_rate_pct"] == 75.0
    assert primary["away"]["one_year_targets"]["30"]["success_rate_pct"] == 25.0
    assert primary["near_minus_away_30pct_pp"] == 50.0
    assert primary["near"]["bounce"]["success_rate_pct"] == 75.0


def test_wma200_research_counts_contiguous_touch_as_one_event():
    first = _row(0, 1.0, True, issuer="SAME")
    second = _row(0, 2.0, True, issuer="SAME")
    second["observation_id"] = "later:SAME"
    second["decision_date"] = (date.fromisoformat(first["decision_date"]) + timedelta(days=30)).isoformat()
    second["entry_date"] = (date.fromisoformat(first["entry_date"]) + timedelta(days=30)).isoformat()

    result = run_wma200_research([first, second])

    assert result["threshold_comparison"][0]["near"]["events"] == 1
