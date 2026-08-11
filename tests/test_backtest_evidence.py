import tempfile
import pytest
from datetime import date, timedelta

from fairentry.backtest.evidence import (
    evaluate_path,
    fixed_return_milestones,
    quality_for,
    summarize_buy_return_achievement,
    summarize_methods,
    summarize_targets,
)
from fairentry.backtest.strategy import BacktestStrategy, load_strategy
from fairentry.backtest.targets import targets_for
from fairentry.scoring.targets import build_target_plan


def test_strategy_has_stable_version_id():
    a = load_strategy()
    b = load_strategy()
    assert a.strategy_id == b.strategy_id
    assert a.tuning_promotion == "manual"
    assert (30, 60, 90, 180, 365) == a.horizons_days


def test_strategy_rejects_descriptive_only_contract_values():
    with pytest.raises(ValueError, match="unsupported backtest benchmark"):
        BacktestStrategy(benchmark="sp500")
    with pytest.raises(ValueError, match="primary_target"):
        BacktestStrategy(target_models=("technical",), primary_target="practical")


def test_target_models_actually_filter_shared_engine_output():
    strategy = BacktestStrategy(target_models=("practical", "fcf"), primary_target="practical")
    rec = {"price": 100, "valuation": {"methods": [
        {"key": "fcf", "fair": 135}, {"key": "book", "fair": 125}]}}
    out = targets_for(rec, {"sma200": {"value": 110}}, strategy)
    assert set(out) == {"practical", "fcf"}


def test_target_excludes_analyst_anchor_and_is_frozen():
    strategy = BacktestStrategy(minimum_upside_pct=5)
    rec = {"price": 100, "valuation": {"methods": [
        {"key": "analyst", "fair": 500}, {"key": "peer_pe", "fair": 125},
        {"key": "fcf", "fair": 135}]}}
    out = targets_for(rec, {"sma200": {"value": 105}}, strategy)
    assert out["fundamental"]["price"] == 130
    assert out["fundamental"]["price"] != 500
    assert out["blended"]["available"]
    assert out["practical"]["price"] is not None
    assert out["practical"]["status"] == "pending"


def test_live_and_backtest_use_identical_shared_target_engine():
    strategy = BacktestStrategy(minimum_upside_pct=10)
    metrics = {"price": {"value": 100, "source": "seed_price"},
               "sma200": {"value": 120, "source": "seed_price"},
               "pb_ratio": {"value": 2, "source": "sec_hist"}}
    rec = {"price": 100, "valuation": {"methods": [
        {"name": "Book", "key": "book", "fair": 106, "upside": 6, "basis": "book"}]}}
    live = build_target_plan(rec, metrics, minimum_upside_pct=10,
                             maximum_upside_pct=100, expiry_days=365,
                             historical=True)["targets"]
    historical = targets_for(rec, metrics, strategy)
    assert live == historical
    # The below-minimum book target remains visible, while technical becomes
    # the guaranteed practical expectation.
    assert historical["book"]["price"] == 106
    assert historical["practical"]["price"] == 110
    assert historical["practical"]["selected_method"] == "technical"


def test_every_valid_price_has_practical_target():
    plan = build_target_plan({"price": 10, "valuation": {"methods": []}},
                             {"price": {"value": 10, "source": "seed_price"}},
                             historical=True)
    assert plan["practical"]["price"] == 11
    assert plan["practical"]["status"] == "pending"


def test_low_relevance_book_value_does_not_drive_technology_target():
    plan = build_target_plan(
        {"price": 100, "sector": "Technology", "valuation": {"methods": [
            {"name": "Book", "key": "book", "fair": 132, "upside": 32, "basis": "book"}]}},
        {"price": {"value": 100, "source": "seed_price"}},
        minimum_upside_pct=30, historical=True)
    assert plan["targets"]["book"]["relevance"] == "low"
    assert plan["practical"]["selected_method"] == "technical"


def test_target_censoring_and_time_to_target():
    series = [{"date": "2024-01-01", "close": 100},
              {"date": "2024-01-31", "close": 108},
              {"date": "2024-03-01", "close": 121}]
    targets = {"fundamental": {"price": 120, "available": True, "expiry_days": 90}}
    out = evaluate_path(series, 100, targets, (30, 60, 90), "2024-01-01")
    assert out["targets"]["fundamental"]["status"] == "reached"
    assert out["targets"]["fundamental"]["days_to_target"] == 60
    assert out["horizons"]["30"]["return_pct"] == 8


def test_target_hit_after_expiry_is_an_expired_failure():
    series = [{"date": "2024-01-01", "close": 100},
              {"date": "2024-01-29", "close": 110},
              {"date": "2024-02-05", "close": 125}]
    targets = {"fundamental": {"price": 120, "available": True, "expiry_days": 30}}
    out = evaluate_path(series, 100, targets, (30,), "2024-01-01")
    target = out["targets"]["fundamental"]
    assert target["status"] == "expired"
    assert target["hit_date"] is None
    assert target["days_to_target"] is None


def test_target_below_entry_is_day_zero_reference_not_a_hit():
    series = [{"date": "2024-01-01", "close": 100},
              {"date": "2024-02-01", "close": 110}]
    targets = {"fcf": {"price": 80, "available": True, "role": "reference",
                       "expiry_days": 365}}
    out = evaluate_path(series, 100, targets, (30,), "2024-01-01")
    fcf = out["targets"]["fcf"]
    assert fcf["status"] == "already_above_at_entry"
    assert fcf["hit_date"] == "2024-01-01"
    assert fcf["days_to_target"] == 0


def test_method_summary_separates_day_zero_references_from_hits():
    obs = [
        {"verdict": "Buy", "outcome": {"targets": {"fcf": {
            "price": 80, "role": "reference", "status": "already_above_at_entry",
            "available": True, "relevance": "high", "upside_pct": -20,
            "expiry_days": 365}}}, "ticker": "REF"},
        {"verdict": "Buy", "outcome": {"targets": {"fcf": {
            "price": 120, "role": "growth", "status": "reached", "available": True,
            "relevance": "high", "upside_pct": 20, "days_to_target": 60,
            "expiry_days": 730}}}, "ticker": "HIT"},
    ]
    fcf = summarize_methods(obs)["fcf"]
    assert fcf["already_above_at_entry"] == 1
    assert fcf["evaluable"] == 1
    assert fcf["reached"] == 1
    assert fcf["hit_rate_pct"] == 100
    assert fcf["hit_rate_ci90"] == [27.0, 100.0]
    assert fcf["unique_stocks"] == 1
    assert fcf["expiry_days_range"] == [730, 730]


def test_unavailable_target_is_not_reported_as_failure():
    out = evaluate_path([{"date": "2024-01-01", "close": 100},
                         {"date": "2025-01-06", "close": 90}], 100,
                        {"fundamental": {"price": None, "available": False, "expiry_days": 365}},
                        (30,), "2024-01-01")
    assert out["targets"]["fundamental"]["status"] == "unavailable"


def test_active_is_not_counted_as_failure():
    obs = [{"verdict": "Buy", "outcome": {"targets": {"fundamental":
            {"available": True, "status": "active"}}}},
           {"verdict": "Buy", "outcome": {"targets": {"fundamental":
            {"available": True, "status": "reached", "days_to_target": 42}}}}]
    s = summarize_targets(obs)
    assert s["evaluable"] == 1
    assert s["hit_rate_pct"] == 100
    assert s["active_or_unavailable"] == 1


def test_fixed_return_milestones_use_adjusted_closes_and_execution_costs():
    milestones = fixed_return_milestones(
        [
            {"date": "2024-01-02", "closeadj": 100},
            {"date": "2024-04-01", "closeadj": 126},
            {"date": "2024-07-01", "closeadj": 132},
        ],
        100,
        "2024-01-02",
        entry_cost_bps=15,
        exit_cost_bps=15,
        thresholds=(25, 30),
    )
    assert milestones["first_hit_days"]["25"] == 90
    assert milestones["first_hit_days"]["30"] == 181
    assert milestones["return_basis"] == "dividend_adjusted_close_with_entry_and_exit_costs"


def test_return_attainment_collapses_consecutive_buys_and_censors_active_rows():
    def row(day, verdict, first_hit, observed, issuer="ISSUER-A"):
        return {
            "entry_date": day,
            "ticker": "AAA",
            "issuer_key": issuer,
            "verdict": verdict,
            "return_milestones": {
                "first_hit_days": {"25": first_hit},
                "last_observed_days": observed,
                "terminal_days": None,
            },
        }

    observations = [
        row("2023-01-01", "Buy", 80, 730),
        row("2023-01-31", "Buy", 40, 730),  # same episode; do not double-count
        row("2023-03-02", "Watch", None, 730),
        row("2023-04-01", "Buy", None, 730),  # new episode and mature failure
        row("2026-01-01", "Buy", None, 40, "ISSUER-B"),  # active, not failure
    ]
    summary = summarize_buy_return_achievement(
        observations, 30, thresholds=(25,), horizons=(90, 365)
    )
    episodes = summary["views"]["episodes"]
    signals = summary["views"]["signals"]
    assert episodes["observations"] == 3
    assert signals["observations"] == 4
    cell = episodes["matrix"]["25"]["90"]
    assert cell["reached"] == 1
    assert cell["expired"] == 1
    assert cell["active"] == 1
    assert cell["evaluable"] == 2
    assert cell["hit_rate_pct"] == 50.0
    assert cell["median_days_to_hit"] == 80


def test_terminal_event_makes_fixed_return_horizon_evaluable():
    observations = [{
        "entry_date": "2024-01-01",
        "ticker": "DELIST",
        "issuer_key": "DELIST",
        "verdict": "Buy",
        "return_milestones": {
            "first_hit_days": {"25": None},
            "last_observed_days": 60,
            "terminal_days": 75,
        },
    }]
    cell = summarize_buy_return_achievement(
        observations, 30, thresholds=(25,), horizons=(365,)
    )["views"]["episodes"]["matrix"]["25"]["365"]
    assert cell["evaluable"] == 1
    assert cell["expired"] == 1
    assert cell["active"] == 0


def test_fixed_return_milestones_accept_terminal_timestamp():
    milestones = fixed_return_milestones(
        [{"date": "2024-01-01", "closeadj": 100}],
        100,
        "2024-01-01",
        terminal_date="2024-03-01 00:00:00",
    )
    assert milestones["terminal_days"] == 60


def test_quality_exposes_current_proxy():
    q = quality_for({"price": {"value": 10, "source": "seed_hist", "fetched_at": "2024-01-01"},
                     "target_price": {"value": 20, "source": "seed_const", "fetched_at": "2024-01-01"}})
    assert q["counts"]["current_proxy"] == 1
    assert any(f["field"] == "target_price" and f["quality"] == "current_proxy" for f in q["fields"])
