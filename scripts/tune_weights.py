#!/usr/bin/env python3
"""Regime-robust category-weight tuning against the backtest.

A weight set is only recommended if it improves the Buy-Avoid alpha spread
across MULTIPLE hold windows AND MULTIPLE time folds (worst-case, not average),
stays close to the current weights (regularized), and still wins on a final
held-out time fold. This kills single-regime overfitting. It recommends only —
it never edits config.

  python scripts/tune_weights.py --db data/backtest.db
  python scripts/tune_weights.py --db data/backtest.db --holds 20,30,60 --folds 4
  python scripts/tune_weights.py --db data/backtest.db --simple   # old single-window
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.config import load_config
from fairentry.store import Store
from fairentry.store.db import DEFAULT_DB
from fairentry.backtest.tune import tune, robust_tune


def _s(x):
    return f"{x:+.2f}%" if isinstance(x, (int, float)) else "  n/a"


def _print_robust(res, emit):
    if not res["ok"]:
        emit("Tuning not ready: " + res["reason"]); return
    holds = res["holds"]
    prot = ", ".join(res.get("protect") or []) or "none"
    emit(f"Regime-robust weight tuning — {res['cohorts']} cohorts · {res['folds']} time folds "
         f"· holds {holds}d · step {res['step_days']}d · reg {res['reg']} "
         f"· protect [{prot}] ±{res.get('protect_band')}")
    emit(f"Objective: worst-case Buy-Avoid alpha spread across (fold x hold), "
         f"confirmed on held-out fold {res['holdout_range'][0]}..{res['holdout_range'][1]}")
    emit("")
    hdr = "".join(f"{('h'+str(h)):>10}" for h in holds)
    emit(f"{'slice':22}{hdr}")
    for label, rep in (("DEFAULT worst-sel", res["default"]), ("TUNED   worst-sel", res["tuned"])):
        emit(f"{label:22}{_s(rep['worst_selection_spread']):>10}")
    emit("  — final holdout (out-of-sample) —")
    for label, rep in (("DEFAULT holdout", res["default"]), ("TUNED   holdout", res["tuned"])):
        emit(f"{label:22}" + "".join(f"{_s(rep['holdout'][h]):>10}" for h in holds))
    emit("")
    mark = {"adopt": "✅ ADOPT", "overfit": "⚠️ KEEP DEFAULT (overfit)", "no_gain": "≈ KEEP DEFAULT (no gain)"}
    emit(f"{mark.get(res['verdict'], res['verdict'])} — {res['note']}")
    if res["verdict"] == "adopt":
        emit("Recommended weights:")
        emit("  " + json.dumps(res["tuned"]["weights"]))


def _print_simple(res, emit):
    if not res["ok"]:
        emit("Tuning not ready: " + res["reason"]); return
    emit(f"Weight tuning — {res['cohorts']} cohorts (train {res['train_cohorts']}/test {res['test_cohorts']})")
    emit(f"{'candidate':28}{'train':>10}{'TEST':>10}{'mono':>7}")
    for name, c in res["candidates"].items():
        emit(f"{name:28}{_s(c['train']['spread']):>10}{_s(c['test']['spread']):>10}{str(c['test']['monotonic']):>7}")
    emit("Tuned weights: " + json.dumps(res["candidates"]["tuned"]["weights"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB.parent / "backtest.db"))
    ap.add_argument("--holds", default="20,30,60", help="comma-separated hold windows (robust mode)")
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--step", type=int, default=7)
    ap.add_argument("--reg", type=float, default=0.15, help="pull toward current weights (higher = more conservative)")
    ap.add_argument("--protect", default="risk,survival",
                    help="comma-separated categories pinned near default (downside guardrail); '' to disable")
    ap.add_argument("--protect-band", type=float, default=3.0, help="± weight points protected categories may move")
    ap.add_argument("--simple", action="store_true", help="old single-window train/test tuner")
    ap.add_argument("--hold", type=int, default=30, help="hold window for --simple")
    ap.add_argument("--md-out", default=None, help="append markdown report (e.g. $GITHUB_STEP_SUMMARY)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    lines = []
    def emit(s=""):
        print(s); lines.append(s)

    with Store(args.db) as store:
        if args.simple:
            res = tune(store, cfg, hold_days=args.hold, step_days=args.step)
            _print_simple(res, emit)
        else:
            holds = tuple(int(x) for x in args.holds.split(","))
            protect = frozenset(c.strip() for c in args.protect.split(",") if c.strip())
            res = robust_tune(store, cfg, holds=holds, step_days=args.step, folds=args.folds,
                              reg=args.reg, protect=protect, protect_band=args.protect_band)
            _print_robust(res, emit)

    if args.md_out:
        with open(args.md_out, "a", encoding="utf-8") as fh:
            fh.write("### Regime-robust weight tuning\n\n```\n" + "\n".join(lines) + "\n```\n")
    if args.json:
        print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
