import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.config import load_config
from fairentry.store import Store
from fairentry.backtest.seed import snapshots_for, sec_fundamental_snapshots, _ma, _price_asof
from fairentry.backtest.harness import run_rolling, _quality_metrics, _settings_for_strategy
from fairentry.backtest.strategy import BacktestStrategy


# ---- pure snapshot derivation ------------------------------------------------

def test_ma():
    assert _ma([1, 2, 3, 4], 2) == 3.5
    assert _ma([1, 2], 5) is None


def test_price_asof_accepts_weekly_rows_with_volume():
    assert _price_asof(
        [("2026-01-02", 10.0, 1000), ("2026-01-09", 11.0, 1200)],
        "2026-01-05",
    ) == 10.0


def test_snapshots_scale_ratios_with_price():
    closes = [("2024-01-01", 50.0), ("2024-01-08", 100.0)]
    # price_now = 100, fwd_pe now = 20 -> at price 50 it should be ~10
    consts, per = snapshots_for(closes, price_now=100.0,
                                fundamentals={"fwd_pe": {"value": 20}, "gross_margin": {"value": 55}})
    d0 = dict(per)["2024-01-01"]
    d1 = dict(per)["2024-01-08"]
    assert abs(d0["fwd_pe"] - 10.0) < 0.01      # halved with price
    assert abs(d1["fwd_pe"] - 20.0) < 0.01
    assert consts["gross_margin"] == 55          # fundamentals held constant


def _fact(concept, unit, vals):
    return {concept: {"units": {unit: vals}}}


def _usd(start, end, filed, val, form="10-Q", fp="Q1"):
    return {"start": start, "end": end, "filed": filed, "val": val, "form": form, "fp": fp}


def _shares(end, filed, val):
    return {"end": end, "filed": filed, "val": val, "form": "10-Q", "fp": "Q1"}


def test_sec_fundamental_snapshots_use_filing_dates():
    facts = {"facts": {"us-gaap": {}}}
    facts["facts"]["us-gaap"].update(_fact("Revenues", "USD", [
        _usd("2023-01-01", "2023-03-31", "2023-05-01", 100),
        _usd("2023-04-01", "2023-06-30", "2023-08-01", 125),
    ]))
    facts["facts"]["us-gaap"].update(_fact("GrossProfit", "USD", [
        _usd("2023-04-01", "2023-06-30", "2023-08-01", 75),
    ]))
    facts["facts"]["us-gaap"].update(_fact("OperatingIncomeLoss", "USD", [
        _usd("2023-04-01", "2023-06-30", "2023-08-01", 25),
    ]))
    facts["facts"]["us-gaap"].update(_fact("NetIncomeLoss", "USD", [
        _usd("2023-04-01", "2023-06-30", "2023-08-01", 20),
    ]))
    facts["facts"]["us-gaap"].update(_fact("Assets", "USD", [
        _usd("2023-04-01", "2023-06-30", "2023-08-01", 500),
    ]))
    facts["facts"]["us-gaap"].update(_fact("Liabilities", "USD", [
        _usd("2023-04-01", "2023-06-30", "2023-08-01", 200),
    ]))
    facts["facts"]["us-gaap"].update(_fact("StockholdersEquity", "USD", [
        _usd("2023-04-01", "2023-06-30", "2023-08-01", 300),
    ]))
    facts["facts"]["us-gaap"].update(_fact("AssetsCurrent", "USD", [
        _usd("2023-04-01", "2023-06-30", "2023-08-01", 150),
    ]))
    facts["facts"]["us-gaap"].update(_fact("LiabilitiesCurrent", "USD", [
        _usd("2023-04-01", "2023-06-30", "2023-08-01", 75),
    ]))
    facts["facts"]["us-gaap"].update(_fact("CommonStockSharesOutstanding", "shares", [
        _shares("2023-03-31", "2023-05-01", 9),
        _shares("2023-06-30", "2023-08-01", 10),
    ]))
    facts["facts"]["us-gaap"].update(_fact("NetCashProvidedByUsedInOperatingActivities", "USD", [
        _usd("2023-04-01", "2023-06-30", "2023-08-01", 30),
    ]))
    facts["facts"]["us-gaap"].update(_fact("PaymentsToAcquirePropertyPlantAndEquipment", "USD", [
        _usd("2023-04-01", "2023-06-30", "2023-08-01", 5),
    ]))

    snaps = dict(sec_fundamental_snapshots(facts, [
        ("2023-04-28", 8.0), ("2023-07-28", 10.0), ("2023-08-04", 12.0),
    ]))

    assert "2023-08-01" in snaps
    s = snaps["2023-08-01"]
    assert s["gross_margin"] == 60
    assert s["oper_margin"] == 20
    assert s["current_ratio"] == 2
    assert s["market_cap"] == 100       # filing-date uses latest prior close, not Aug 4
    assert s["ps_ratio"] == 0.8
    assert s["rev_growth_qoq"] == 25
    assert round(s["share_count_yoy"], 2) == 11.11


# ---- rolling backtest end to end (synthetic, no network) --------------------

def _weekly_dates(n, start=date(2023, 1, 2)):
    return [(start + timedelta(weeks=i)).isoformat() for i in range(n)]


def _seed(store, ticker, sector, closes_dates, fundamentals):
    price_now = closes_dates[-1][1]
    priced_volume = [(day, price, 1_000_000) for day, price in closes_dates]
    consts, per = snapshots_for(priced_volume, price_now, fundamentals)
    store.upsert_security(ticker, ticker, sector)
    earliest = per[0][0]
    for fid, v in consts.items():
        store.set_metric(ticker, fid, v, "seed_const", earliest)
    for d, snap in per:
        for fid, v in snap.items():
            store.set_metric(ticker, fid, v, "seed_hist", d)


def _fund(**kw):
    return {k: {"value": v} for k, v in kw.items()}


def test_rolling_backtest_detects_buy_alpha():
    """A synthetic world where cheap/quality names rise and weak names fall:
    the Buy bucket must show higher benchmark-relative return than Avoid, and
    the ordering should be monotonic. This exercises seed -> as-of scoring ->
    forward return -> cross-sectional alpha end to end."""
    cfg = load_config()
    weeks = 60
    dts = _weekly_dates(weeks)
    db = tempfile.mktemp(suffix=".db")
    store = Store(db)

    WIN = _fund(gross_margin=70, oper_margin=35, roic=30, debt_eq=0.2, current_ratio=3.2,
                altman_z=8, rev_growth_qoq=25, eps_growth_next_y=30, fwd_pe=12, ps_ratio=2,
                pb_ratio=2, pfcf_ratio=10, target_price=200, analyst_recom=1.3,
                red_flags_score=100, red_flags_critical=0, short_float=3, beta=1.0)
    LOSE = _fund(gross_margin=15, oper_margin=-5, roic=-3, debt_eq=3.5, current_ratio=0.8,
                 altman_z=1.0, rev_growth_qoq=-10, eps_growth_next_y=-8, fwd_pe=40, ps_ratio=9,
                 pb_ratio=5, pfcf_ratio=35, target_price=40, analyst_recom=3.2,
                 red_flags_score=50, red_flags_critical=1, short_float=25, beta=2.0)
    MID = _fund(gross_margin=40, oper_margin=12, roic=11, debt_eq=1.0, current_ratio=1.6,
                altman_z=3.5, rev_growth_qoq=5, eps_growth_next_y=8, fwd_pe=18, ps_ratio=3,
                pb_ratio=2.5, pfcf_ratio=16, target_price=78, analyst_recom=2.3,
                red_flags_score=85, red_flags_critical=0, short_float=8, beta=1.1)

    for i in range(12):   # winners: cheap + quality, price stays near monthly EMA
        closes = [(dts[w], round(50 * (1.002 ** w), 4)) for w in range(weeks)]
        _seed(store, f"WIN{i}", "Technology", closes, WIN)
    for i in range(12):   # losers: weak + expensive, price falls ~1.2%/wk
        closes = [(dts[w], round(100 * (0.988 ** w), 4)) for w in range(weeks)]
        _seed(store, f"LOSE{i}", "Technology", closes, LOSE)
    for i in range(8):    # middle: flat, moderate
        closes = [(dts[w], round(70 + (1 if w % 2 else -1), 4)) for w in range(weeks)]
        _seed(store, f"MID{i}", "Technology", closes, MID)
    store.commit()

    # screened_only=False / warmup_days=0 keeps this controlled full-population
    # world intact (the LOSE names would be screened out otherwise).
    res = run_rolling(store, cfg, hold_days=30, step_days=14, min_names=20,
                      screened_only=False, warmup_days=0,
                      strategy=BacktestStrategy(data_quality_mode="experimental"))
    store.close()

    assert res["ok"], res
    assert res["cohorts"] >= 3
    bv = res["by_verdict"]
    assert "Buy" in bv and "Avoid" in bv, bv
    # Buys must beat Avoids on benchmark-relative return, with a positive spread
    assert bv["Buy"]["mean_alpha_pct"] > bv["Avoid"]["mean_alpha_pct"]
    assert res["buy_minus_avoid_pct"] > 0
    assert bv["Buy"]["hit_rate_pct"] > bv["Avoid"]["hit_rate_pct"]
    # a strong, consistent edge -> the block-bootstrap 90% CI excludes zero
    assert res["spread_ci90"] is not None
    assert res["spread_ci90"][0] <= res["spread_ci90"][1]
    assert res["significant"] is True


def test_screened_only_reduces_population():
    """screened_only=True (product-faithful) drops names that fail every screener
    (the LOSE names), so it scores fewer observations than the full universe."""
    cfg = load_config()
    store = Store(tempfile.mktemp(suffix=".db"))
    weeks = 60
    dts = _weekly_dates(weeks)
    WIN = _fund(gross_margin=70, oper_margin=35, roic=30, debt_eq=0.2, current_ratio=3.2,
                altman_z=8, rev_growth_qoq=25, eps_growth_next_y=30, fwd_pe=12, ps_ratio=2,
                pb_ratio=2, pfcf_ratio=10, target_price=200, analyst_recom=1.3,
                red_flags_score=100, red_flags_critical=0, short_float=3, beta=1.0)
    LOSE = _fund(gross_margin=15, oper_margin=-5, roic=-3, debt_eq=3.5, current_ratio=0.8,
                 altman_z=1.0, rev_growth_qoq=-10, eps_growth_next_y=-8, fwd_pe=40, ps_ratio=9,
                 pb_ratio=5, pfcf_ratio=35, target_price=40, analyst_recom=3.2,
                 red_flags_score=50, red_flags_critical=1, short_float=25, beta=2.0)
    for i in range(15):
        _seed(store, f"WIN{i}", "Technology", [(dts[w], round(50 * 1.002 ** w, 4)) for w in range(weeks)], WIN)
    for i in range(15):
        _seed(store, f"LOSE{i}", "Technology", [(dts[w], round(100 * 0.988 ** w, 4)) for w in range(weeks)], LOSE)
    store.commit()

    def total(r):
        return sum(d["n"] for d in r["by_verdict"].values())
    full = run_rolling(store, cfg, hold_days=30, step_days=14, min_names=5,
                       screened_only=False, warmup_days=0, bootstrap=0,
                       strategy=BacktestStrategy(data_quality_mode="experimental"))
    scr = run_rolling(store, cfg, hold_days=30, step_days=14, min_names=5,
                      screened_only=True, warmup_days=0, bootstrap=0,
                      strategy=BacktestStrategy(data_quality_mode="experimental"))
    store.close()
    assert full["ok"] and scr["ok"]
    assert total(scr) < total(full)          # LOSE names filtered out when screened


def test_execution_costs_are_applied_to_reported_returns():
    cfg = load_config()
    store = Store(tempfile.mktemp(suffix=".db"))
    dts = _weekly_dates(12)
    fund = _fund(gross_margin=70, oper_margin=35, roic=30, debt_eq=0.2,
                 current_ratio=3.2, altman_z=8, rev_growth_qoq=25,
                 eps_growth_next_y=30, fwd_pe=12, ps_ratio=2, pb_ratio=2,
                 pfcf_ratio=10, target_price=200, analyst_recom=1.3,
                 red_flags_score=100, red_flags_critical=0, short_float=3, beta=1.0)
    for i in range(3):
        _seed(store, f"COST{i}", "Technology",
              [(d, 100 + w * 2) for w, d in enumerate(dts)], fund)
    store.commit()

    free = run_rolling(store, cfg, hold_days=7, step_days=7, min_names=1,
                       screened_only=False, warmup_days=0, bootstrap=0,
                       strategy=BacktestStrategy(slippage_bps=0, transaction_cost_bps=0))
    costly = run_rolling(store, cfg, hold_days=7, step_days=7, min_names=1,
                         screened_only=False, warmup_days=0, bootstrap=0,
                         strategy=BacktestStrategy(slippage_bps=500, transaction_cost_bps=500))
    store.close()

    verdict = next(iter(free["by_verdict"]))
    assert costly["by_verdict"][verdict]["mean_raw_return_pct"] < free["by_verdict"][verdict]["mean_raw_return_pct"]
    assert costly["execution"]["return_basis"] == "cost_adjusted_entry"


def test_rejected_small_cohorts_do_not_leak_into_evidence():
    cfg = load_config()
    store = Store(tempfile.mktemp(suffix=".db"))
    dts = _weekly_dates(12)
    fund = _fund(gross_margin=70, oper_margin=35, roic=30, debt_eq=0.2,
                 current_ratio=3.2, altman_z=8, rev_growth_qoq=25,
                 eps_growth_next_y=30, fwd_pe=12, ps_ratio=2, pb_ratio=2,
                 pfcf_ratio=10, target_price=200, analyst_recom=1.3,
                 red_flags_score=100, red_flags_critical=0, short_float=3, beta=1.0)
    _seed(store, "EARLY", "Technology", [(d, 100 + w) for w, d in enumerate(dts)], fund)
    _seed(store, "LATE", "Technology", [(d, 100 + w) for w, d in enumerate(dts[4:])], fund)
    store.commit()

    res = run_rolling(store, cfg, hold_days=7, step_days=7, min_names=2,
                      screened_only=False, warmup_days=0, bootstrap=0)
    store.close()

    assert res["ok"]
    assert len(res["observations"]) == sum(c["n"] for c in res["per_cohort"])
    assert all(o["entry_date"] >= dts[4] for o in res["observations"])


def test_quality_modes_have_distinct_operational_source_policies():
    metrics = {
        "price": {"value": 10, "source": "seed_price"},
        "fwd_pe": {"value": 12, "source": "seed_price_scaled"},
        "growth": {"value": 20, "source": "seed_const"},
    }
    experimental = _quality_metrics(metrics, BacktestStrategy(data_quality_mode="experimental"))
    mostly = _quality_metrics(metrics, BacktestStrategy(data_quality_mode="mostly_point_in_time"))
    strict = _quality_metrics(metrics, BacktestStrategy(data_quality_mode="strict"))
    assert set(experimental) == {"price", "fwd_pe", "growth"}
    assert set(mostly) == {"price", "fwd_pe"}
    assert set(strict) == {"price"}


def test_live_strategy_presets_are_resolved_independently():
    cfg = load_config()
    cfg.defaults["strategy_presets"] = {"deep_value": "recovery_value",
                                        "quality_growth": "quality_compounder"}
    deep, deep_name = _settings_for_strategy(cfg, {}, "deep_value")
    growth, growth_name = _settings_for_strategy(cfg, {}, "quality_growth")
    assert deep_name == "recovery_value"
    assert growth_name == "quality_compounder"
    assert deep["weights"] != growth["weights"]
