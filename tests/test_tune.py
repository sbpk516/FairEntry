import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.config import load_config
from fairentry.store import Store
from fairentry.backtest.seed import snapshots_for
from fairentry.backtest.tune import precompute, evaluate, tune


def _seed_world(store):
    dts = [(date(2023, 1, 2) + timedelta(weeks=i)).isoformat() for i in range(60)]

    def f(**k):
        return {a: {"value": v} for a, v in k.items()}

    def seed(t, closes, fund):
        c, per = snapshots_for(closes, closes[-1][1], fund)
        store.upsert_security(t, t, "Technology")
        for fid, v in c.items():
            store.set_metric(t, fid, v, "c", per[0][0])
        for d, sn in per:
            for fid, v in sn.items():
                store.set_metric(t, fid, v, "h", d)

    WIN = f(gross_margin=70, oper_margin=35, roic=30, debt_eq=0.2, current_ratio=3.2, altman_z=8,
            rev_growth_qoq=25, eps_growth_next_y=30, fwd_pe=12, ps_ratio=2, pb_ratio=2, pfcf_ratio=10,
            target_price=200, analyst_recom=1.3, red_flags_score=100, red_flags_critical=0, short_float=3, beta=1.0)
    LOSE = f(gross_margin=15, oper_margin=-5, roic=-3, debt_eq=3.5, current_ratio=0.8, altman_z=1.0,
             rev_growth_qoq=-10, eps_growth_next_y=-8, fwd_pe=40, ps_ratio=9, pb_ratio=5, pfcf_ratio=35,
             target_price=40, analyst_recom=3.2, red_flags_score=50, red_flags_critical=1, short_float=25, beta=2.0)
    MID = f(gross_margin=40, oper_margin=12, roic=11, debt_eq=1.0, current_ratio=1.6, altman_z=3.5,
            rev_growth_qoq=5, eps_growth_next_y=8, fwd_pe=18, ps_ratio=3, pb_ratio=2.5, pfcf_ratio=16,
            target_price=78, analyst_recom=2.3, red_flags_score=85, red_flags_critical=0, short_float=8, beta=1.1)
    for i in range(12):
        seed(f"WIN{i}", [(dts[w], round(50 * 1.012 ** w, 4)) for w in range(60)], WIN)
    for i in range(12):
        seed(f"LOSE{i}", [(dts[w], round(100 * 0.988 ** w, 4)) for w in range(60)], LOSE)
    for i in range(8):
        seed(f"MID{i}", [(dts[w], round(70 + (1 if w % 2 else -1), 4)) for w in range(60)], MID)
    store.commit()


def test_evaluate_is_pure_and_deterministic():
    cfg = load_config()
    store = Store(tempfile.mktemp(suffix=".db"))
    _seed_world(store)
    obs = precompute(store, cfg, hold_days=30, step_days=14, min_names=20)
    store.close()
    assert obs, "precompute should produce observations"
    w = {cid: c["weight"] for cid, c in cfg.categories.items()}
    b, wb = cfg.verdict_bands["buy"], cfg.verdict_bands["watch"]
    r1 = evaluate(obs, w, b, wb)
    r2 = evaluate(obs, w, b, wb)
    assert r1 == r2                       # pure
    assert r1["n_buy"] + r1["n_watch"] + r1["n_avoid"] == len(obs)


def test_tuner_reports_and_never_worsens_train_spread():
    cfg = load_config()
    store = Store(tempfile.mktemp(suffix=".db"))
    _seed_world(store)
    res = tune(store, cfg, hold_days=30, step_days=14, min_names=20, test_frac=0.3)
    store.close()

    assert res["ok"], res
    cands = res["candidates"]
    assert "default" in cands and "tuned" in cands
    # the tuned vector must not be worse than default on the TRAIN objective
    assert cands["tuned"]["train"]["spread"] >= cands["default"]["train"]["spread"] - 1e-6
    # weights are a normalized simplex
    tw = cands["tuned"]["weights"]
    assert abs(sum(tw.values()) - 100) < 0.5
    assert all(v >= 0 for v in tw.values())
