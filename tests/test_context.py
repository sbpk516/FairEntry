import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.config import load_config
from fairentry.store import Store
from fairentry.scoring.engine import sector_medians, score_ticker
from fairentry.pipeline.export import demand_momentum, build_board, _preset_weights


def _m(**kw):
    return {k: {"value": v} for k, v in kw.items()}


def test_demand_momentum_strong_and_rotating():
    r = demand_momentum(_m(rev_growth_qoq=25, eps_growth_next_y=30, estimate_revision_score=60,
                           perf_year=40, rel_volume=1.8, analyst_recom=1.5))
    assert r["demand"]["label"] == "strong"
    assert r["momentum"]["label"] == "rotating in"
    assert any("Sales" in e for e in r["demand"]["evidence"])
    assert "not part of the score" in r["disclaimer"].lower()


def test_demand_momentum_soft_and_out_of_favor():
    r = demand_momentum(_m(rev_growth_qoq=-8, eps_growth_next_y=2, perf_year=-30, rel_volume=0.6))
    assert r["demand"]["label"] == "soft"
    assert r["momentum"]["label"] == "out of favor"


def test_demand_momentum_na_when_empty():
    r = demand_momentum({})
    assert r["demand"]["label"] == "n/a"
    assert r["momentum"]["label"] == "n/a"


def test_context_is_present_but_does_not_change_the_verdict():
    """The exported verdict must be exactly what score_ticker produces — proving
    the demand/momentum context is additive and never feeds the score."""
    cfg = load_config()
    store = Store(tempfile.mktemp(suffix=".db"))
    store.upsert_security("AAA", "Alpha", "Technology")
    for fid, val in {"price": 50, "target_price": 82, "fwd_pe": 9, "pb_ratio": 1.2, "ps_ratio": 1.4,
                     "pfcf_ratio": 8, "debt_eq": 0.5, "current_ratio": 2.1, "altman_z": 4.2,
                     "gross_margin": 46, "oper_margin": 18, "roic": 15, "rev_growth_qoq": 25,
                     "eps_growth_next_y": 30, "perf_year": 40, "rel_volume": 1.8, "analyst_recom": 1.5,
                     "red_flags_score": 95, "red_flags_critical": 0}.items():
        store.set_metric("AAA", fid, val, "test")
    store.commit()
    board = build_board(cfg, store, reason=False)
    stock = board["stocks"][0]

    # context is exported...
    assert stock["context"]["demand"]["label"] in ("strong", "steady", "soft", "n/a")
    assert stock["context"]["momentum"]["label"] in ("rotating in", "neutral", "out of favor", "n/a")

    # ...and the verdict is byte-for-byte what the scored model returns (context ignored)
    med = sector_medians(cfg, store)
    s = {"margin_of_safety_pct": 15, "target_upside_pct": 30, "weights": _preset_weights(cfg, "deep_value")}
    rec = score_ticker(cfg, store.securities()[0], store.metrics_for("AAA"), med, s)
    store.close()
    assert stock["verdict"] == rec["verdict"]
    assert stock["score"] == rec["score"]
