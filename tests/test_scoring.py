import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.config import load_config
from fairentry.scoring.rules import apply_rule
from fairentry.scoring.engine import score_ticker
from fairentry.scoring.fair_value import fair_value

_SEC = {"ticker": "T", "company": "Test", "sector": "Technology"}
_SETTINGS = {"margin_of_safety_pct": 15, "target_upside_pct": 30}
_MED = {"Technology": {"gross_margin": 40}}


def _strong_metrics(**over):
    """A clean, cheap, high-quality name (should score Buy) so veto/gate tests
    isolate exactly the negative signal under test."""
    m = {"price": {"value": 100}, "target_price": {"value": 160},
         "gross_margin": {"value": 60}, "oper_margin": {"value": 30},
         "roic": {"value": 25}, "debt_eq": {"value": 0.2},
         "current_ratio": {"value": 2.5}, "altman_z": {"value": 6.0},
         "rev_growth_qoq": {"value": 20}, "eps_growth_next_y": {"value": 25},
         "perf_year": {"value": 20}, "pfcf_ratio": {"value": 10},
         "ps_ratio": {"value": 2}, "red_flags_score": {"value": 100},
         "red_flags_critical": {"value": 0}, "analyst_recom": {"value": 1.5}}
    m.update({k: {"value": v} for k, v in over.items()})
    return m


def test_higher_better():
    s, _ = apply_rule({"type": "higher_better", "full_at": 20, "floor_at": 0}, 20)
    assert s == 100
    s, _ = apply_rule({"type": "higher_better", "full_at": 20, "floor_at": 0}, 0)
    assert s == 0
    s, _ = apply_rule({"type": "higher_better", "full_at": 20, "floor_at": 0}, 10)
    assert 49 <= s <= 51


def test_lower_better_and_band():
    s, _ = apply_rule({"type": "lower_better", "full_at": 12, "floor_at": 40}, 12)
    assert s == 100
    s, _ = apply_rule({"type": "band", "bands": [{"min": 2.6, "score": 90},
                       {"min": 1.8, "score": 55}, {"min": 0, "score": 20}]}, 3.0)
    assert s == 90


def test_missing_metric_is_na():
    s, why = apply_rule({"type": "higher_better", "full_at": 20, "floor_at": 0}, None)
    assert s is None and why == "no data"


def test_reproducible():
    """Same inputs -> identical score (deterministic core)."""
    cfg = load_config()
    sec = {"ticker": "T", "company": "Test", "sector": "Technology"}
    metrics = {"price": {"value": 100, "source": "x", "fetched_at": "2026-07-12"},
               "target_price": {"value": 130, "source": "x", "fetched_at": "2026-07-12"},
               "gross_margin": {"value": 50, "source": "x", "fetched_at": "2026-07-12"},
               "debt_eq": {"value": 0.3, "source": "x", "fetched_at": "2026-07-12"},
               "oper_margin": {"value": 25, "source": "x", "fetched_at": "2026-07-12"}}
    med = {"Technology": {"gross_margin": 40}}
    s = {"margin_of_safety_pct": 15, "target_upside_pct": 30}
    r1 = score_ticker(cfg, sec, metrics, med, s)
    r2 = score_ticker(cfg, sec, metrics, med, s)
    assert r1["preliminary"] == r2["preliminary"]
    assert r1["verdict"] == r2["verdict"]
    assert r1["base_score"] > 0


# ---- veto / soft-gate firing -------------------------------------------------

def test_going_concern_veto_forces_avoid():
    """Regression: the going-concern hard veto must fire on its own (previously a
    YAML `true` typo made it a silent no-op)."""
    cfg = load_config()
    r = score_ticker(cfg, _SEC, _strong_metrics(going_concern=True), _MED, _SETTINGS)
    assert r["verdict"] == "Avoid"
    assert any(v["id"] == "going_concern" for v in r["vetoes"])


def test_no_going_concern_is_not_vetoed():
    cfg = load_config()
    r = score_ticker(cfg, _SEC, _strong_metrics(going_concern=False), _MED, _SETTINGS)
    assert not any(v["id"] == "going_concern" for v in r["vetoes"])


def test_critical_red_flag_veto():
    cfg = load_config()
    r = score_ticker(cfg, _SEC, _strong_metrics(red_flags_critical=1), _MED, _SETTINGS)
    assert r["verdict"] == "Avoid"
    assert any(v["id"] == "critical_red_flag" for v in r["vetoes"])


def test_distress_corroborated_veto():
    cfg = load_config()
    r = score_ticker(cfg, _SEC, _strong_metrics(altman_z=1.0, debt_eq=3.0), _MED, _SETTINGS)
    assert any(v["id"] == "distress_corroborated" for v in r["vetoes"])
    assert r["verdict"] == "Avoid"


def test_missing_veto_metric_does_not_fire():
    """A veto whose metric isn't present must not fire (unevaluable -> skipped)."""
    cfg = load_config()
    m = _strong_metrics()
    m.pop("altman_z"); m.pop("red_flags_critical")
    r = score_ticker(cfg, _SEC, m, _MED, _SETTINGS)
    assert not r["vetoes"]


def test_upside_soft_gate_caps_buy_to_watch():
    """A name whose upside is below target is soft-gated (never a clean Buy)."""
    cfg = load_config()
    # Drop the valuation-multiple metrics so fair value = analyst anchor only,
    # then a modest +10% target sits well below the 30% upside target.
    m = _strong_metrics(target_price=110)
    for k in ("pfcf_ratio", "ps_ratio"):
        m.pop(k, None)
    r = score_ticker(cfg, _SEC, m, _MED, _SETTINGS)
    assert round(r["valuation"]["upside_pct"]) == 10
    assert any(g["id"] == "upside_below_target" for g in r["soft_gates"])
    assert r["verdict"] != "Buy"


# ---- multi-method fair value -------------------------------------------------

def test_fair_value_blends_multiple_methods():
    metrics = {"price": {"value": 100}, "target_price": {"value": 130},
               "fwd_pe": {"value": 10}, "eps_growth_next_y": {"value": 20},
               "ps_ratio": {"value": 2}, "pb_ratio": {"value": 1.5},
               "pfcf_ratio": {"value": 12}}
    sector_med = {"fwd_pe": 15, "ps_ratio": 3, "pb_ratio": 2.5}
    fv = fair_value(metrics, mos_pct=15, sector_med=sector_med)
    assert fv["method_count"] >= 4                      # several methods contributed
    assert fv["fair_low"] <= fv["fair_base"] <= fv["fair_high"]
    assert fv["upside_pct"] > 0                         # cheap on every method -> upside
    assert fv["valuation_label"] == "cheap"
    assert fv["buy_zone"] < fv["fair_base"]             # MoS discount applied


def test_fair_value_no_price_is_unknown():
    fv = fair_value({"target_price": {"value": 50}}, mos_pct=15)
    assert fv["valuation_label"] == "unknown"
    assert fv["method_count"] == 0


def test_fair_value_expensive_label():
    metrics = {"price": {"value": 100}, "target_price": {"value": 80},
               "fwd_pe": {"value": 40}, "ps_ratio": {"value": 10}}
    sector_med = {"fwd_pe": 15, "ps_ratio": 3}
    fv = fair_value(metrics, sector_med=sector_med)
    assert fv["valuation_label"] == "expensive"
    assert fv["upside_pct"] < 0
