#!/usr/bin/env python3
"""Search category weights that widen the Buy-filter's alpha spread, validated
on held-out (later) cohorts so we don't overfit.

  python scripts/tune_weights.py --db data/backtest.db
  python scripts/tune_weights.py --db data/backtest.db --hold 30 --step 7 --test-frac 0.3

Reports the train and TEST Buy-Avoid alpha spread for the current defaults, each
preset, and a tuned weight vector. Only adopt the tuned weights if they beat the
defaults on TEST. This does NOT edit config — it recommends.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.config import load_config
from fairentry.store import Store
from fairentry.store.db import DEFAULT_DB
from fairentry.backtest.tune import tune


def _fmt_spread(e):
    s = e.get("spread")
    return f"{s:+.2f}%" if s is not None else "  n/a"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB.parent / "backtest.db"))
    ap.add_argument("--hold", type=int, default=30)
    ap.add_argument("--step", type=int, default=7)
    ap.add_argument("--test-frac", type=float, default=0.3)
    ap.add_argument("--md-out", default=None, help="append a markdown report (e.g. $GITHUB_STEP_SUMMARY)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    with Store(args.db) as store:
        res = tune(store, cfg, hold_days=args.hold, step_days=args.step, test_frac=args.test_frac)

    if not res["ok"]:
        print("Tuning not ready:", res["reason"])
        return

    lines = []
    def out(s=""):
        print(s); lines.append(s)

    out(f"Weight tuning — {res['cohorts']} cohorts "
        f"(train {res['train_cohorts']} / test {res['test_cohorts']}, split at {res['cut_date']})")
    out(f"hold {res['hold_days']}d / step {res['step_days']}d · objective: Buy−Avoid alpha spread")
    out("")
    out(f"{'candidate':28} {'train α-spread':>14} {'TEST α-spread':>14} {'test Buy n':>11} {'mono':>6}")
    for name, c in res["candidates"].items():
        out(f"{name:28} {_fmt_spread(c['train']):>14} {_fmt_spread(c['test']):>14} "
            f"{c['test']['n_buy']:>11} {str(c['test']['monotonic']):>6}")

    d = res["default_test_spread"]
    t = res["tuned_test_spread"]
    out("")
    if d is not None and t is not None:
        delta = t - d
        if delta > 0.1:
            out(f"✅ tuned beats default on TEST by {delta:+.2f}% — candidate worth adopting.")
        elif delta < -0.1:
            out(f"⚠️ tuned is WORSE on TEST ({delta:+.2f}%) — overfit; keep the defaults.")
        else:
            out(f"≈ tuned ≈ default on TEST ({delta:+.2f}%) — no real gain; keep the defaults.")
    out("")
    out("Tuned weights (only adopt if it wins on TEST):")
    out("  " + json.dumps(res["candidates"]["tuned"]["weights"]))

    if args.md_out:
        with open(args.md_out, "a", encoding="utf-8") as fh:
            fh.write("### Weight tuning — Buy alpha spread\n\n```\n" + "\n".join(lines) + "\n```\n")
    if args.json:
        print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
