from fairentry.backtest.dilution_research import (
    dilution_yoy_pct,
    run_dilution_veto_research,
)


def _row(ticker, entry_date, dilution, *, verdict="Buy", hit=None, observed=400):
    return {
        "issuer_key": ticker,
        "ticker": ticker,
        "entry_date": entry_date,
        "verdict": verdict,
        "categories": [{
            "id": "survival",
            "items": [{"id": "dilution", "actual": dilution}],
        }],
        "return_milestones": ({
            "first_hit_days": {"30": hit},
            "last_observed_days": observed,
            "max_drawdown_pct_by_horizon": {"365": -15},
        } if verdict == "Buy" else None),
    }


def test_dilution_research_compares_veto_without_mutating_verdicts():
    observations = [
        _row("DILUTED", "2020-01-02", 12, hit=90),
        _row("DILUTED", "2020-02-03", 12, verdict="Watch"),
        _row("CONTROL", "2020-01-02", 2, hit=None),
        _row("CONTROL", "2020-02-03", 2, verdict="Watch"),
    ]
    report = run_dilution_veto_research(observations)
    selected = report["selected"]
    assert report["baseline"]["full_history"]["success_rate_pct"] == 50.0
    assert selected["threshold_pct"] == 10.0
    assert selected["removed_buy_signals"] == 1
    assert selected["full_history"]["completed"] == 1
    assert selected["full_history"]["success_rate_pct"] == 0.0
    assert [row["verdict"] for row in observations] == ["Buy", "Watch", "Buy", "Watch"]


def test_dilution_missing_is_unknown_and_does_not_trigger_veto():
    observation = _row("UNKNOWN", "2020-01-02", None, hit=50)
    report = run_dilution_veto_research([observation])
    assert dilution_yoy_pct(observation) is None
    assert report["buy_signals_missing_dilution"] == 1
    assert report["selected"]["removed_buy_signals"] == 0
    assert report["selected"]["full_history"]["successes"] == 1


def test_research_reconstructs_buy_before_the_live_dilution_veto():
    observation = _row("VETOED", "2020-01-02", 12, verdict="Buy", hit=50)
    observation["verdict"] = "Avoid"
    observation["pre_dilution_verdict"] = "Buy"
    report = run_dilution_veto_research([observation])
    assert report["baseline"]["full_history"]["successes"] == 1
    assert report["selected"]["removed_buy_signals"] == 1
    assert report["selected"]["full_history"]["episodes"] == 0


def test_research_uses_compact_tuning_outcome_for_a_vetoed_buy():
    observation = _row("VETOED", "2020-01-02", 12, verdict="Watch")
    observation["verdict"] = "Avoid"
    observation["pre_dilution_verdict"] = "Buy"
    observation["_tuning_outcome"] = {
        "first_hit_days_by_target": {"30": 80},
        "last_observed_days": 365,
        "max_drawdown_pct": -18,
    }
    report = run_dilution_veto_research([observation])
    assert report["baseline"]["full_history"]["successes"] == 1
    assert report["baseline"]["full_history"]["median_drawdown_pct"] == -18
    assert report["selected"]["full_history"]["episodes"] == 0
