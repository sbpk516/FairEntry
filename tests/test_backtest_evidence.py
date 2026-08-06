import tempfile
from datetime import date, timedelta

from fairentry.backtest.evidence import evaluate_path, quality_for, summarize_targets
from fairentry.backtest.strategy import BacktestStrategy, load_strategy
from fairentry.backtest.targets import targets_for
from fairentry.scoring.targets import build_target_plan


def test_strategy_has_stable_version_id():
    a = load_strategy()
    b = load_strategy()
    assert a.strategy_id == b.strategy_id
    assert a.tuning_promotion == "manual"
    assert (30, 60, 90, 180, 365) == a.horizons_days


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


def test_quality_exposes_current_proxy():
    q = quality_for({"price": {"value": 10, "source": "seed_hist", "fetched_at": "2024-01-01"},
                     "target_price": {"value": 20, "source": "seed_const", "fetched_at": "2024-01-01"}})
    assert q["counts"]["current_proxy"] == 1
    assert any(f["field"] == "target_price" and f["quality"] == "current_proxy" for f in q["fields"])
