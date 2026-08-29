from datetime import date, timedelta
from pathlib import Path

import pytest

from fairentry.backtest.evidence import (
    RETURN_THRESHOLDS_PCT,
    evaluate_path,
    fixed_return_milestones,
    practical_target_deadline,
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
    assert (a.tuning_lower_gain_pct, a.tuning_primary_gain_pct,
            a.tuning_upper_gain_pct) == (25, 30, 35)
    assert (30, 60, 90, 180, 365) == a.horizons_days


def test_return_explorer_covers_large_gains():
    assert RETURN_THRESHOLDS_PCT == (
        10, 15, 20, 25, 28, 30, 35, 40, 50, 75, 100, 150, 200
    )


def test_one_official_score_is_calibrated_against_25_to_35_pct_outcomes():
    def row(ticker, score, hit_35):
        return {
            "entry_date": "2020-01-02",
            "entry_price": 100,
            "ticker": ticker,
            "issuer_key": ticker,
            "verdict": "Buy",
            "score": score,
            "return_milestones": {
                "first_hit_days": {
                    "25": 80, "28": 95, "30": 110,
                    "35": 140 if hit_35 else None,
                },
                "last_observed_days": 365,
                "terminal_days": None,
                "max_return_pct_by_horizon": {"365": 40 if hit_35 else 31},
                "max_drawdown_pct_by_horizon": {"365": -8 if hit_35 else -18},
            },
        }

    summary = summarize_buy_return_achievement(
        [row("LOW", 73, False), row("HIGH", 91, True)], 30
    )
    calibration = summary["score_band_calibration"]
    assert calibration["one_official_score"] is True
    assert calibration["production_effect"] == "none"
    assert calibration["targets_pct"] == [25, 28, 30, 35]
    assert calibration["overall"]["30"]["hit_rate_pct"] == 100
    assert calibration["overall"]["35"]["hit_rate_pct"] == 50
    assert [band["label"] for band in calibration["bands"]] == ["72-74", "90+"]
    assert summary["primary_success_contract"]["primary_fixed_target_pct"] == 30


def test_backtest_ui_displays_one_score_probability_ladder():
    html = (Path(__file__).resolve().parents[1] / "web" / "backtest.html").read_text(
        encoding="utf-8"
    )
    assert "function scoreBandCalibration(b)" in html
    assert "What the one FairEntry score achieved" in html
    assert "+25% is the acceptable lower target" in html
    assert "+28% shows the middle" in html
    assert "+35% tests stronger upside" in html
    assert "successContract(b)+scoreBandCalibration(b)" in html


@pytest.mark.parametrize(("upside", "years"), [
    (30, 1), (30.1, 2), (70, 2), (70.1, 3),
    (120, 3), (120.1, 5), (271, 5), (500, 7),
])
def test_practical_target_deadline_uses_compounding_ladder(upside, years):
    assert practical_target_deadline(upside)["deadline_years"] == years


def test_practical_target_has_separate_on_time_and_late_result():
    start = date(2020, 1, 1)
    series = [
        {"date": start.isoformat(), "close": 100},
        {"date": (start + timedelta(days=500)).isoformat(), "close": 90},
        {"date": (start + timedelta(days=800)).isoformat(), "close": 171},
    ]
    target = {"price": 170, "upside_pct": 70, "available": True,
              "expiry_days": 365, "selected_method": "fundamental"}
    result = evaluate_path(series, 100, {"practical": target}, (365,), start.isoformat())
    test = result["targets"]["practical"]["performance_test"]
    assert test["deadline_years"] == 2
    assert test["status"] == "reached_late"
    assert test["days_to_target"] == 800
    assert test["max_drawdown_before_result_pct"] == -10
    assert test["highest_gain_before_deadline_pct"] == 0


def test_practical_target_hit_on_exact_deadline_is_on_time():
    start = date(2020, 1, 1)
    series = [
        {"date": start.isoformat(), "close": 100},
        {"date": (start + timedelta(days=365)).isoformat(), "close": 130},
    ]
    result = evaluate_path(
        series,
        100,
        {"practical": {"price": 130, "upside_pct": 30, "available": True}},
        (365,),
        start.isoformat(),
    )
    test = result["targets"]["practical"]["performance_test"]
    assert test["status"] == "reached"
    assert test["days_to_target"] == 365


def test_practical_target_without_enough_history_is_still_active():
    start = date(2024, 1, 1)
    series = [
        {"date": start.isoformat(), "close": 100},
        {"date": (start + timedelta(days=300)).isoformat(), "close": 110},
    ]
    result = evaluate_path(
        series,
        100,
        {"practical": {"price": 130, "upside_pct": 30, "available": True}},
        (365,),
        start.isoformat(),
    )
    assert result["targets"]["practical"]["performance_test"]["status"] == "active"


def test_unclassified_terminal_event_is_excluded_not_called_a_failure():
    start = date(2024, 1, 1)
    series = [{"date": start.isoformat(), "close": 100}]
    result = evaluate_path(
        series,
        100,
        {"practical": {"price": 160, "upside_pct": 60, "available": True}},
        (365,),
        start.isoformat(),
        terminal_date=(start + timedelta(days=90)).isoformat(),
    )
    test = result["targets"]["practical"]["performance_test"]
    assert test["status"] == "closed_early_excluded"
    assert test["terminal_days"] == 90
    assert test["counted_in_success_rate"] is False


def test_bankruptcy_terminal_event_is_a_completed_failure():
    start = date(2024, 1, 1)
    terminal = {
        "date": (start + timedelta(days=90)).isoformat(),
        "action": "bankruptcyliquidation",
        "terminal_return_policy": "zero",
    }
    result = evaluate_path(
        [{"date": start.isoformat(), "close": 100}], 100,
        {"practical": {"price": 160, "upside_pct": 60, "available": True}},
        (365,), start.isoformat(), terminal_event=terminal,
    )
    test = result["targets"]["practical"]["performance_test"]
    assert test["status"] == "expired"
    assert test["counted_in_success_rate"] is True


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
    def row(day, verdict, first_hit, observed, issuer="ISSUER-A", entry_price=100):
        return {
            "entry_date": day,
            "entry_price": entry_price,
            "ticker": "AAA",
            "issuer_key": issuer,
            "verdict": verdict,
            "return_milestones": {
                "first_hit_days": {"25": first_hit, "30": first_hit},
                "last_observed_days": observed,
                "terminal_days": None,
                "max_return_pct_by_horizon": {"365": 31.0},
                "max_drawdown_pct_by_horizon": {"365": -12.0},
            },
            "outcome": {"targets": {"practical": {
                "price": 160, "upside_pct": 60, "selected_method": "fundamental",
                "reason": "A tested fundamental value cleared the required range.",
                "performance_test": {
                    "status": ("reached" if first_hit is not None else
                               "expired" if observed >= 730 else "active"),
                    "deadline_days": 730, "deadline_years": 2,
                    "within_standard_five_year_range": True,
                    "days_to_target": first_hit,
                },
            }}},
        }

    observations = [
        row("2023-01-01", "Buy", 80, 730),
        row("2023-01-31", "Buy", 40, 730, entry_price=137),  # same episode
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
    first = summary["episode_details"][0]
    assert first["started"] == "2023-01-01"
    assert first["last_buy"] == "2023-01-31"
    assert first["buy_signals"] == 2
    assert first["entry_price_low"] == 100
    assert first["entry_price_high"] == 137
    assert first["highest_gain_within_one_year_pct"] == 31.0
    assert first["days_to_30_pct"] == 80
    assert first["fixed_30_target_price"] == 130
    assert first["fixed_30_status"] == "within_one_year"
    assert first["fixed_30_reason"]["code"] == "reached_on_time"
    assert first["max_drawdown_within_one_year_pct"] == -12
    assert first["practical_target"]["deadline_years"] == 2
    assert first["practical_target"]["reason"] == (
        "A tested fundamental value cleared the required range."
    )
    assert first["practical_target"]["result_reason"]["code"] == "reached_on_time"
    contract = summary["primary_success_contract"]
    assert contract["fixed_30_within_one_year"]["hit_rate_pct"] == 50
    assert contract["practical_target_with_compounding_deadline"]["hit_rate_pct"] == 50


def test_unclassified_terminal_event_is_excluded_from_fixed_return_rate():
    observations = [{
        "entry_date": "2024-01-01",
        "ticker": "DELIST",
        "issuer_key": "DELIST",
        "verdict": "Buy",
        "terminal_event": {"date": "2024-03-16", "action": "delisted"},
        "return_milestones": {
            "first_hit_days": {"25": None},
            "last_observed_days": 60,
            "terminal_days": 75,
        },
    }]
    cell = summarize_buy_return_achievement(
        observations, 30, thresholds=(25,), horizons=(365,)
    )["views"]["episodes"]["matrix"]["25"]["365"]
    assert cell["evaluable"] == 0
    assert cell["expired"] == 0
    assert cell["active"] == 0
    assert cell["excluded"] == 1


def test_episode_explains_acquisition_before_both_target_deadlines():
    observations = [{
        "entry_date": "2024-01-01",
        "entry_price": 100,
        "ticker": "TAKEN",
        "company": "Taken Corp",
        "issuer_key": "TAKEN",
        "verdict": "Buy",
        "terminal_event": {
            "date": "2024-06-01", "action": "acquisitionby", "successor": "NEWCO"
        },
        "return_milestones": {
            "first_hit_days": {"30": None},
            "last_observed_days": 150,
            "terminal_days": 152,
            "max_return_pct_by_horizon": {"365": 12},
            "max_drawdown_pct_by_horizon": {"365": -8},
        },
        "outcome": {"targets": {"practical": {
            "price": 160, "upside_pct": 60, "selected_method": "fundamental",
            "performance_test": {
                "status": "expired", "deadline_days": 730, "deadline_years": 2,
                "days_to_target": None, "last_observed_days": 150,
                "terminal_days": 152, "highest_gain_before_deadline_pct": 12,
                "max_drawdown_before_result_pct": -8,
            },
        }}},
    }]
    episode = summarize_buy_return_achievement(
        observations, 30, thresholds=(30,), horizons=(365,)
    )["episode_details"][0]
    assert episode["fixed_30_reason"]["code"] == "acquired"
    assert episode["fixed_30_reason"]["successor"] == "NEWCO"
    assert episode["practical_target"]["result_reason"]["code"] == "acquired"
    assert episode["fixed_30_status"] == "closed_early_excluded"
    assert episode["fixed_30_evaluation"]["counted_in_success_rate"] is False
    assert episode["practical_target"]["status"] == "closed_early_excluded"


def test_short_unclassified_history_is_not_a_three_year_failure():
    observations = [{
        "entry_date": "2025-09-02", "entry_price": 63.24,
        "ticker": "SHORT", "issuer_key": "SHORT", "verdict": "Buy",
        "terminal_event": {"date": "2025-09-11", "action": "delisted"},
        "return_milestones": {
            "first_hit_days": {"30": None}, "last_observed_days": 9,
            "last_observed_date": "2025-09-11", "terminal_days": 9,
            "max_return_pct_by_horizon": {"365": -0.13},
            "max_drawdown_pct_by_horizon": {"365": -0.33},
        },
        "outcome": {"targets": {"practical": {
            "price": 88.31, "upside_pct": 40, "selected_method": "peer_ps",
            "performance_test": {
                "status": "closed_early_excluded", "deadline_days": 730,
                "deadline_years": 2, "last_observed_days": 9,
                "last_observed_date": "2025-09-11", "terminal_days": 9,
                "counted_in_success_rate": False,
            },
        }}},
    }]
    summary = summarize_buy_return_achievement(
        observations, 30, thresholds=(30,), horizons=(365,)
    )
    episode = summary["episode_details"][0]
    assert episode["fixed_30_status"] == "closed_early_excluded"
    assert episode["fixed_30_evaluation"]["observed_days"] == 9
    assert episode["fixed_30_evaluation"]["required_days"] == 365
    fixed = summary["primary_success_contract"]["fixed_30_within_one_year"]
    assert fixed["excluded"] == 1
    assert fixed["evaluable"] == 0


def test_missed_first_year_but_still_in_three_year_window_is_not_labeled_no_time_passed():
    # Regression: an episode with 584 observed days (well past the 365-day
    # deadline) and no hit had been falling into the generic "still_waiting"
    # bucket alongside genuinely fresh Buys, and its reason claimed "not
    # enough time has passed" even though the one-year test already failed.
    observations = [{
        "entry_date": "2024-12-31", "entry_price": 20.65,
        "ticker": "FLYW", "issuer_key": "FLYW", "verdict": "Buy",
        "return_milestones": {
            "first_hit_days": {"30": None}, "last_observed_days": 584,
            "terminal_days": None,
            "max_return_pct_by_horizon": {"365": 2.84},
            "max_drawdown_pct_by_horizon": {"365": -59.29},
        },
    }]
    summary = summarize_buy_return_achievement(
        observations, 30, thresholds=(30,), horizons=(365,)
    )
    episode = summary["episode_details"][0]
    assert episode["fixed_30_status"] == "missed_within_one_year_tracking"
    assert episode["fixed_30_evaluation"]["result"] == "failure"
    assert episode["fixed_30_evaluation"]["counted_in_success_rate"] is True
    reason = episode["fixed_30_reason"]
    assert reason["code"] == "missed_first_year_tracking"
    assert "not enough time" not in reason["details"].lower()
    assert "already counted as a miss in the one-year result" in reason["details"]
    assert episode["fixed_30_miss_category"] == "modest_gain_short_of_target"
    timing = summary["primary_success_contract"]["fixed_30_timing"]
    assert timing["missed_within_one_year_tracking"] == 1
    assert timing["still_waiting"] == 0


def test_genuinely_fresh_buy_is_still_labeled_not_enough_time():
    observations = [{
        "entry_date": "2026-08-01", "entry_price": 50,
        "ticker": "NEW", "issuer_key": "NEW", "verdict": "Buy",
        "return_milestones": {
            "first_hit_days": {"30": None}, "last_observed_days": 19,
            "terminal_days": None,
            "max_return_pct_by_horizon": {"365": 3.0},
            "max_drawdown_pct_by_horizon": {"365": -2.0},
        },
    }]
    summary = summarize_buy_return_achievement(
        observations, 30, thresholds=(30,), horizons=(365,)
    )
    episode = summary["episode_details"][0]
    assert episode["fixed_30_status"] == "still_waiting"
    assert episode["fixed_30_reason"]["code"] == "still_waiting"
    assert "not enough" in episode["fixed_30_reason"]["details"].lower() or \
           "still waiting" in episode["fixed_30_reason"]["details"].lower()


def test_episode_miss_reason_shows_highest_gain_and_shortfall():
    # never_within_three_years is decided over the full 3-year window, so the
    # evidence must report the 3-year peak (here 18%), not a first-year figure.
    observations = [{
        "entry_date": "2020-01-01", "entry_price": 100, "ticker": "SHORT",
        "issuer_key": "SHORT", "verdict": "Buy",
        "return_milestones": {
            "first_hit_days": {"30": None}, "last_observed_days": 1200,
            "terminal_days": None,
            "max_return_pct_by_horizon": {"365": 6, "1095": 18},
            "max_drawdown_pct_by_horizon": {"365": -10, "1095": -25},
        },
    }]
    episode = summarize_buy_return_achievement(
        observations, 30, thresholds=(30,), horizons=(365,)
    )["episode_details"][0]
    reason = episode["fixed_30_reason"]
    assert reason["code"] == "fell_short"
    assert "12.00 percentage points short" in reason["details"]
    assert reason["evidence"][0]["value"] == 18
    assert reason["category"] == "close_but_short_of_target"
    assert episode["fixed_30_miss_category"] == "close_but_short_of_target"


def test_fixed_miss_category_covers_the_full_range():
    def make(ticker, max_gain):
        return {
            "entry_date": "2020-01-01", "entry_price": 100, "ticker": ticker,
            "issuer_key": ticker, "verdict": "Buy",
            "return_milestones": {
                "first_hit_days": {"30": None}, "last_observed_days": 1200,
                "terminal_days": None,
                "max_return_pct_by_horizon": {"1095": max_gain},
                "max_drawdown_pct_by_horizon": {"1095": -5},
            },
        }
    summary = summarize_buy_return_achievement(
        [make("A", -4), make("B", 9), make("C", 22)], 30, thresholds=(30,), horizons=(365,)
    )
    categories = {row["ticker"]: row["fixed_30_miss_category"] for row in summary["episode_details"]}
    assert categories == {
        "A": "stayed_at_or_below_entry", "B": "modest_gain_short_of_target",
        "C": "close_but_short_of_target",
    }
    breakdown = summary["primary_success_contract"]["fixed_30_miss_breakdown"]["counts"]
    assert breakdown["stayed_at_or_below_entry"] == 1
    assert breakdown["modest_gain_short_of_target"] == 1
    assert breakdown["close_but_short_of_target"] == 1


def test_fixed_miss_reason_notes_market_underperformance():
    observations = [{
        "entry_date": "2020-01-01", "entry_price": 100, "ticker": "LAG",
        "issuer_key": "LAG", "verdict": "Buy",
        "outcome": {"horizons": {"1095": {"benchmark_return_pct": 40.0, "alpha_pct": -25.0}}},
        "return_milestones": {
            "first_hit_days": {"30": None}, "last_observed_days": 1200,
            "terminal_days": None,
            "max_return_pct_by_horizon": {"1095": 15},
            "max_drawdown_pct_by_horizon": {"1095": -5},
        },
    }]
    reason = summarize_buy_return_achievement(
        observations, 30, thresholds=(30,), horizons=(365,)
    )["episode_details"][0]["fixed_30_reason"]
    assert reason["market_context"] == {
        "horizon_days": 1095, "benchmark_return_pct": 40.0, "alpha_pct": -25.0,
    }
    assert "underperformed the benchmark by 25.0 points" in reason["details"]


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


def test_target_failure_reasons_use_controlled_evidence_backed_phrases():
    from fairentry.backtest.evidence import _target_failure_reasons

    failed = {
        "categories": [
            {"id": "valuation", "score": 25}, {"id": "growth", "score": 30},
            {"id": "survival", "score": 80},
        ],
        "outcome": {"targets": {"practical": {"upside_pct": 85}}, "horizons": {}},
    }
    reasons = _target_failure_reasons(failed)
    codes = {row["code"] for row in reasons}
    assert {"valuation_too_high", "growth_below_expectations", "target_overly_optimistic"} <= codes
    assert all(row["phrase"] and row["category"] for row in reasons)


def test_target_failure_reason_is_honest_when_cause_is_not_proven():
    from fairentry.backtest.evidence import _target_failure_reasons

    reasons = _target_failure_reasons({"categories": [], "outcome": {"horizons": {}}})
    assert [row["code"] for row in reasons] == ["cause_not_verified"]


def test_researched_failure_reason_overrides_inferred_proxy_for_known_ticker():
    from fairentry.backtest.evidence import _target_failure_reasons

    reasons = _target_failure_reasons({"ticker": "CSCO", "categories": [], "outcome": {}})
    assert len(reasons) == 1
    assert reasons[0]["evidence_status"] == "researched"
    assert "inventory digestion" in reasons[0]["phrase"]
    assert reasons[0]["sources"]


def test_one_year_miss_gets_reason_even_when_target_is_reached_late():
    observations = [{
        "ticker": "CSCO", "company": "Cisco", "issuer_key": "CSCO",
        "entry_date": "2023-01-01", "entry_price": 100, "verdict": "Buy",
        "strategy_key": "quality_growth", "categories": [],
        "return_milestones": {
            "first_hit_days": {"30": 500}, "last_observed_days": 1200,
            "terminal_days": None,
            "max_return_pct_by_horizon": {"365": 10, "1095": 35},
            "max_drawdown_pct_by_horizon": {"365": -8, "1095": -8},
            "max_drawdown_before_first_hit_pct": {"30": -8},
        },
        "outcome": {"targets": {}, "horizons": {}},
    }]
    episode = summarize_buy_return_achievement(
        observations, 30, thresholds=(30,), horizons=(365,)
    )["episode_details"][0]
    assert episode["fixed_30_status"] == "during_year_two"
    assert episode["fixed_30_evaluation"]["result"] == "failure"
    assert episode["target_failure_reasons"][0]["evidence_status"] == "researched"
