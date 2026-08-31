from fairentry.backtest.exit_policy_research import (
    evaluate_exit_policy,
    run_exit_policy_research,
)
import duckdb
from pathlib import Path


def _observation(entry_date="2020-01-02", verdict="Buy", score=80):
    return {
        "ticker": "TEST",
        "security_id": "TEST.SEC",
        "issuer_key": "test issuer",
        "decision_date": entry_date,
        "entry_date": entry_date,
        "entry_price": 100.15,
        "raw_close": 100,
        "_entry_closeadj": 100,
        "_exit_episode_id": "0",
        "verdict": verdict,
        "score": score,
        "vetoes": [],
        "execution": {"entry_cost_bps": 15, "exit_cost_bps": 15},
        "return_milestones": {
            "first_hit_days": {"30": None},
            "last_observed_days": 730,
            "last_observed_date": "2022-01-01",
            "max_return_pct_by_horizon": {"365": 0},
            "max_drawdown_pct_by_horizon": {"365": -30},
        },
        "outcome": {"targets": {"practical": {"price": 150}}},
    }


def test_exit_research_uses_next_close_and_compares_730_day_baseline():
    observation = _observation()
    paths = {"0": [
        {"date": "2020-01-02", "close": 100, "closeadj": 100},
        {"date": "2020-01-03", "close": 74, "closeadj": 74},
        {"date": "2020-01-06", "close": 68, "closeadj": 68},
        {"date": "2022-01-03", "close": 110, "closeadj": 110},
    ]}
    report = evaluate_exit_policy([observation], paths, step_days=30)
    trade = report["sample_trades"][0]
    assert trade["exit_reason"] == "catastrophic_loss"
    # The -25% signal happened at 74, but the modeled fill used the next close 68.
    assert trade["candidate_return_pct"] < -31
    assert trade["baseline_return_pct"] > 9
    assert report["production_effect"] == "none"
    assert report["promotion_eligible"] is False


def test_exit_research_counts_avoid_only_on_new_recommendation_dates():
    rows = [
        _observation("2020-01-02", "Buy"),
        _observation("2020-02-03", "Avoid"),
        _observation("2020-03-02", "Avoid"),
    ]
    # Only the first row is an episode root; later non-Buys form its timeline.
    rows[1]["return_milestones"] = None
    rows[2]["return_milestones"] = None
    paths = {"0": [
        {"date": "2020-01-02", "close": 100, "closeadj": 100},
        {"date": "2020-02-03", "close": 101, "closeadj": 101},
        {"date": "2020-02-04", "close": 102, "closeadj": 102},
        {"date": "2020-03-02", "close": 103, "closeadj": 103},
        {"date": "2020-03-03", "close": 104, "closeadj": 104},
    ]}
    report = evaluate_exit_policy(rows, paths, step_days=30)
    trade = report["sample_trades"][0]
    assert trade["exit_reason"] == "confirmed_avoid"
    assert trade["candidate_return_pct"] > 3


def test_public_backtest_page_renders_exit_policy_evidence():
    html = (Path(__file__).resolve().parents[1] / "web" / "backtest.html").read_text(
        encoding="utf-8"
    )
    assert "function exitPolicyResearch" in html
    assert "Exit Policy v1 backtest" in html
    assert "+exitPolicyResearch(b)" in html


def test_warehouse_adapter_assigns_price_path_to_episode():
    connection = duckdb.connect(":memory:")
    connection.execute(
        "CREATE TABLE sfa_prices(ticker VARCHAR,date DATE,close DOUBLE,closeadj DOUBLE)"
    )
    connection.execute(
        "INSERT INTO sfa_prices VALUES "
        "('TEST','2020-01-02',100,100),('TEST','2020-01-03',75,75),"
        "('TEST','2020-01-06',70,70),('TEST','2022-01-03',105,105)"
    )
    report = run_exit_policy_research([_observation()], connection, step_days=30)
    assert report["episodes"] == 1
    assert report["sample_trades"][0]["exit_reason"] == "catastrophic_loss"
    connection.close()
