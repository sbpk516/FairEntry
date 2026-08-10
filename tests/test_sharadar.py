from fairentry.backtest.strategy import BacktestStrategy
import duckdb
from dataclasses import replace

from fairentry.backtest.evidence import metrics_for_policy, quality_for
from fairentry.backtest.sfa_replay import SFAReplay, _implementation_fingerprint, _row_metrics
from fairentry.backtest.sfa_tune import tune_sfa_observations
from fairentry.analytics.breakout_setup import (breakout_price_metric,
    breakout_volume_metric, relative_strength_metric, trend_regime_metric)
from fairentry.config import load_config
from fairentry.scoring.targets import build_target_plan
from fairentry.sharadar.direct import DirectSharadarClient
from fairentry.sharadar.snapshot import _require_ok
from scripts.sfa_backtest import grouped_return_summary, public_artifact


def test_sfa_strategy_contract_is_operational():
    strategy = BacktestStrategy(
        entry="next_close",
        benchmark="spy_total_return",
        universe_top_n=500,
        minimum_coverage_pct=60,
    )
    assert strategy.entry == "next_close"
    assert strategy.benchmark == "spy_total_return"
    assert strategy.universe_top_n == 500


def test_sfa_strict_policy_excludes_declared_proxies_and_counts_missing_fields():
    strategy = BacktestStrategy(data_quality_mode="strict")
    metrics = {
        "price": {"value": 10, "source": "sharadar_sep"},
        "fwd_pe": {"value": 15, "source": "sharadar_daily_trailing_pe_proxy"},
    }
    filtered = metrics_for_policy(metrics, strategy)
    assert set(filtered) == {"price"}
    report = quality_for(filtered, {"price", "fwd_pe", "beta"})
    assert report["counts"]["missing"] == 2
    assert report["grade"] == "mostly_point_in_time"


def test_fixed_horizons_are_anchored_to_actual_next_close_entry():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE sfa_prices(ticker VARCHAR,date DATE,close DOUBLE,closeadj DOUBLE)")
    con.executemany("INSERT INTO sfa_prices VALUES (?,?,?,?)", [
        ("ABC", "2025-01-06", 100, 100),
        ("ABC", "2025-02-03", 108, 108),  # decision + 31, entry + 28: too early
        ("ABC", "2025-02-05", 110, 110),  # entry + 30: correct observation
    ])
    replay = SFAReplay(type("Warehouse", (), {"con": con})())
    result = replay.fixed_outcomes(["ABC"], "2025-01-03", (30,), True)["ABC"]
    assert result["entry_date"].isoformat() == "2025-01-06"
    assert result["date_30"].isoformat() == "2025-02-05"
    con.close()


def test_snapshot_applies_live_universe_floors_before_top_n():
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE canonical_securities(
        security_id VARCHAR,ticker VARCHAR,company VARCHAR,sector VARCHAR,industry VARCHAR,
        country VARCHAR,isdelisted VARCHAR,firstpricedate DATE,lastpricedate DATE)""")
    con.executemany("INSERT INTO canonical_securities VALUES (?,?,?,?,?,?,?,?,?)", [
        ("1", "GOOD", "Good", "Technology", "Software", "US", "N", "2020-01-01", "2030-01-01"),
        ("2", "WRONG", "Wrong sector", "Healthcare", "Biotech", "US", "N", "2020-01-01", "2030-01-01"),
        ("3", "PENNY", "Penny", "Technology", "Software", "US", "N", "2020-01-01", "2030-01-01"),
        ("4", "ILLIQ", "Illiquid", "Technology", "Software", "US", "N", "2020-01-01", "2030-01-01"),
    ])
    con.execute("""CREATE TABLE sfa_price_features(
        ticker VARCHAR,date DATE,close DOUBLE,closeadj DOUBLE,closeunadj DOUBLE,high DOUBLE,low DOUBLE,
        volume DOUBLE,history_sessions BIGINT,sma50 DOUBLE,sma200 DOUBLE,wma200_proxy DOUBLE,
        avgvol50 DOUBLE,resistance50 DOUBLE,support126 DOUBLE,close_3m DOUBLE,close_1y DOUBLE,sma50_1m_ago DOUBLE)""")
    con.executemany("INSERT INTO sfa_price_features VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("GOOD", "2025-01-02", 50, 50, 50, 51, 49, 1_000_000, 1000, 45, 40, 30, 1_000_000, 48, 35, 45, 35, 44),
        ("WRONG", "2025-01-02", 60, 60, 60, 61, 59, 1_000_000, 1000, 55, 50, 40, 1_000_000, 58, 45, 55, 45, 54),
        ("PENNY", "2025-01-02", .5, .5, .5, .6, .4, 100_000_000, 1000, .4, .3, .2, 100_000_000, .48, .35, .45, .35, .39),
        ("ILLIQ", "2025-01-02", 100, 100, 100, 101, 99, 100, 1000, 90, 80, 70, 100, 98, 80, 90, 80, 89),
    ])
    con.execute("""CREATE TABLE sfa_art_features(
        ticker VARCHAR,datekey DATE,reportperiod DATE,grossmargin DOUBLE,ros DOUBLE,opinc DOUBLE,revenue DOUBLE,
        netmargin DOUBLE,roe DOUBLE,roic DOUBLE,de DOUBLE,currentratio DOUBLE,fcf DOUBLE,assets DOUBLE,
        liabilities DOUBLE,workingcapital DOUBLE,retearn DOUBLE,ebit DOUBLE,marketcap DOUBLE)""")
    con.execute("""CREATE TABLE sfa_arq_features(
        ticker VARCHAR,datekey DATE,reportperiod DATE,revenue DOUBLE,revenue_prev_q DOUBLE,opinc DOUBLE,
        opinc_prev_q DOUBLE,sharesbas DOUBLE,shares_prev_y DOUBLE,grossmargin DOUBLE,grossmargin_prev_q DOUBLE)""")
    con.execute("CREATE TABLE sfa_daily(ticker VARCHAR,date DATE,marketcap DOUBLE,pe DOUBLE,ps DOUBLE,pb DOUBLE)")
    for ticker, cap, price in (("GOOD", 2000, 50), ("WRONG", 3000, 60),
                               ("PENNY", 4000, .5), ("ILLIQ", 5000, 100)):
        con.execute("INSERT INTO sfa_daily VALUES (?,?,?,?,?,?)", [ticker, "2025-01-02", cap, 15, 2, 2])
    con.execute("CREATE TABLE sfa_fund_prices(ticker VARCHAR,date DATE,closeadj DOUBLE)")
    con.execute("CREATE TABLE sfa_prices(ticker VARCHAR,date DATE,closeadj DOUBLE)")
    con.execute("""CREATE TABLE sfa_insiders(ticker VARCHAR,filingdate DATE,transactioncode VARCHAR,
        transactionvalue DOUBLE,ownername VARCHAR,transactiondate DATE,officertitle VARCHAR)""")
    con.execute("""CREATE TABLE sfa_holdings_by_ticker(ticker VARCHAR,date DATE,shrunits DOUBLE,shrholders DOUBLE)""")
    replay = SFAReplay(type("Warehouse", (), {"con": con})())
    strategy = replace(BacktestStrategy(universe_mode="sharadar_point_in_time"),
                       universe_top_n=1, screened_only=False)
    candidates, universe = replay.snapshot("2025-01-02", strategy, load_config())
    assert [row["sec"]["ticker"] for row in universe] == ["GOOD"]
    assert [row["sec"]["ticker"] for row in candidates] == ["GOOD"]
    con.close()


def test_fundamental_first_practical_target_never_falls_back_to_generic_technical():
    plan = build_target_plan(
        {"price": 100, "sector": "Technology", "valuation": {"methods": []}},
        {"price": {"value": 100, "source": "sharadar_sep"}},
        minimum_upside_pct=30, historical=True,
        practical_policy="fundamental_first",
    )
    assert plan["practical"]["available"] is False
    assert plan["practical"]["selected_method"] is None


def test_direct_sharadar_maps_to_same_logical_tables():
    assert DirectSharadarClient.logical_code("fundamentals") == "SF1"
    assert DirectSharadarClient.logical_code("stocks") == "SEP"
    assert DirectSharadarClient.logical_code("actions") == "ACTIONS"


def test_sfa_market_cap_millions_are_normalized_for_price_to_fcf():
    metrics = _row_metrics(
        {
            "close": 50.0,
            "price_date": "2025-01-02",
            "marketcap_daily": 2_000.0,
            "fcf": 100_000_000,
            "assets": 1_000_000_000,
            "liabilities": 1_000_000_000,
            "workingcapital": 0,
            "retearn": 0,
            "ebit": 0,
            "revenue": 0,
        },
        "2025-01-02",
        None,
    )
    assert metrics["pfcf_ratio"]["value"] == 20.0
    assert metrics["market_cap"]["value"] == 2_000_000_000.0
    assert metrics["altman_z"]["value"] == 1.2


def test_sfa_market_features_use_the_shared_live_formulas():
    row = {"close": 105.0, "price_date": "2025-01-02", "resistance50": 100.0,
           "support126": 80.0, "history_sessions": 1000, "volume": 2_000_000,
           "avgvol50": 1_000_000, "sma50": 100.0, "sma200": 90.0,
           "sma50_1m_ago": 95.0, "close_3m": 100.0, "assets": None}
    metrics = _row_metrics(row, "2025-01-02", spy_3m=1.0, sector_3m=2.0)
    assert metrics["breakout_price_score"]["value"] == breakout_price_metric(105, 100)[0]
    assert metrics["breakout_volume_score"]["value"] == breakout_volume_metric(2_000_000, 1_000_000)[0]
    assert metrics["relative_strength_score"]["value"] == relative_strength_metric(5, 2, 1)[0]
    assert metrics["trend_regime_score"]["value"] == trend_regime_metric(105, 100, 90, 95)[0]


def test_sfa_run_fingerprint_captures_code_and_model_config():
    fingerprint = _implementation_fingerprint()
    assert len(fingerprint) == 16
    assert all(c in "0123456789abcdef" for c in fingerprint)


def test_sharadar_http_errors_never_echo_secret_urls():
    response = type(
        "Response",
        (),
        {
            "ok": False,
            "status_code": 403,
            "url": "https://example.invalid?api_key=do-not-log",
        },
    )()
    try:
        _require_ok(response, "metadata request")
    except RuntimeError as exc:
        assert str(exc) == "metadata request failed with HTTP 403"
        assert "api_key" not in str(exc)
    else:
        raise AssertionError("expected a sanitized HTTP error")


def test_public_stability_aggregates_use_full_observation_population():
    observations = [
        {
            "ticker": "A",
            "sector": "Tech",
            "entry_date": "2020-01-01",
            "verdict": "Buy",
            "horizons": {"30": {"return_pct": 10}},
        },
        {
            "ticker": "B",
            "sector": "Tech",
            "entry_date": "2020-02-01",
            "verdict": "Buy",
            "horizons": {"30": {"return_pct": -2}},
        },
    ]
    summary = grouped_return_summary(observations, 30)
    assert summary["by_year"][0]["Buy"] == 4
    assert summary["by_year"][0]["n"] == 2


def test_public_artifact_redacts_reconstructable_vendor_values():
    result = {
        "observations": [
            {
                "raw_close": 10,
                "security_id": "vendor-permanent-id",
                "categories": [{"items": [{"actual": 42}]}],
                "data_quality": {"grade": "point_in_time", "fields": [{"value": 42}]},
                "outcome": {"path": [["2020-01-01", 10]], "horizons": {}},
            }
        ]
    }
    clean = public_artifact(result)
    row = clean["observations"][0]
    assert "raw_close" not in row
    assert "security_id" not in row
    assert "path" not in row["outcome"]
    assert "items" not in row["categories"][0]
    assert row["data_quality"]["fields"] == []
    assert row["quality_grade"] == "point_in_time"
    assert clean["public_data_boundary"]["raw_vendor_rows_exposed"] is False


def test_sfa_tuner_uses_disjoint_chronological_partitions_per_strategy():
    cfg = load_config()
    observations = []
    for strategy_key in ("deep_value", "quality_growth"):
        for month in range(1, 21):
            decision = f"2024-{month:02d}-01" if month <= 9 else f"2025-{month-9:02d}-01"
            for verdict, alpha in (("Buy", 2.0), ("Avoid", -1.0)):
                observations.append({
                    "decision_date": decision,
                    "strategy_key": strategy_key,
                    "verdict": verdict,
                    "categories": [{"id": key, "score": 90 if verdict == "Buy" else 20}
                                   for key in cfg.categories],
                    "vetoes": [], "soft_gates": [],
                    "horizons": {str(h): {"alpha_pct": alpha}
                                 for h in (30, 90, 180, 365, 548)},
                })
    report = tune_sfa_observations(observations, cfg, candidates=2)
    for result in report["strategies"].values():
        assert result["ok"]
        split = result["split"]
        assert split["development"]["last"] < split["validation"]["first"]
        assert split["validation"]["last"] < split["test"]["first"]
