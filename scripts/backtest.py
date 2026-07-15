#!/usr/bin/env python3
"""Backtest the scoring model over accumulated point-in-time history.

  python scripts/backtest.py                          # single entry->exit, by verdict
  python scripts/backtest.py --rolling                # rolling, benchmark-relative alpha
  python scripts/backtest.py --db data/backtest.db --rolling --hold 30 --step 7

Seed a history first (so there's something to replay) with:
  python scripts/seed_backtest.py
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.config import load_config
from fairentry.store import Store
from fairentry.store.db import DEFAULT_DB
from fairentry.backtest.harness import run, run_rolling


def _markdown_rolling(res):
    if not res["ok"]:
        return f"### Backtest not ready\n\n{res['reason']}\n"
    w = res["window"]
    L = [f"### Rolling backtest — Buy-filter alpha",
         f"`{w[0]} → {w[1]}` · **{res['cohorts']} cohorts** · hold {res['hold_days']}d / step {res['step_days']}d",
         "",
         "| verdict | n | mean α | median α | hit-rate | raw ret |",
         "|---|--:|--:|--:|--:|--:|"]
    for v in ("Buy", "Watch", "Avoid"):
        d = res["by_verdict"].get(v)
        if d:
            L.append(f"| {v} | {d['n']} | {d['mean_alpha_pct']:+.2f}% | {d['median_alpha_pct']:+.2f}% "
                     f"| {d['hit_rate_pct']:.1f}% | {d['mean_raw_return_pct']:+.2f}% |")
    spread, mono = res.get("buy_minus_avoid_pct"), res.get("monotonic")
    verdict = "✅ working" if (mono and (spread or 0) > 0) else "⚠️ check"
    L += ["",
          f"**Buy − Avoid alpha spread:** {spread:+.2f}%  ·  **monotonic (Buy≥Watch≥Avoid):** {mono}  ·  {verdict}",
          "",
          "> α = each name's return minus its cohort's cross-sectional mean "
          "(measures stock selection, not market direction)."]
    return "\n".join(L) + "\n"


def _print_rolling(res):
    if not res["ok"]:
        print("Backtest not ready:", res["reason"])
        return
    w = res["window"]
    print(f"Rolling backtest {w[0]} -> {w[1]}  ·  {res['cohorts']} cohorts  "
          f"·  hold {res['hold_days']}d / step {res['step_days']}d")
    print(f"{'verdict':7} {'n':>6} {'mean α':>9} {'median α':>9} {'hit-rate':>9} {'raw ret':>9}")
    for v in ("Buy", "Watch", "Avoid"):
        d = res["by_verdict"].get(v)
        if d:
            print(f"{v:7} {d['n']:>6} {d['mean_alpha_pct']:>+8.2f}% {d['median_alpha_pct']:>+8.2f}% "
                  f"{d['hit_rate_pct']:>8.1f}% {d['mean_raw_return_pct']:>+8.2f}%")
    spread, mono = res.get("buy_minus_avoid_pct"), res.get("monotonic")
    print(f"\nBuy − Avoid alpha spread: {spread:+.2f}%   "
          f"monotonic (Buy≥Watch≥Avoid): {mono}")
    print("α = return minus the cohort's cross-sectional mean (stock selection, not market).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB), help="store to backtest (e.g. data/backtest.db)")
    ap.add_argument("--rolling", action="store_true", help="rolling benchmark-relative alpha backtest")
    ap.add_argument("--hold", type=int, default=30, help="forward holding window in days (rolling)")
    ap.add_argument("--step", type=int, default=7, help="days between cohort entries (rolling)")
    ap.add_argument("--json", action="store_true", help="also dump the full result JSON")
    ap.add_argument("--md-out", default=None, help="write a markdown report to this path (e.g. $GITHUB_STEP_SUMMARY)")
    args = ap.parse_args()

    cfg = load_config()
    with Store(args.db) as store:
        res = run_rolling(store, cfg, hold_days=args.hold, step_days=args.step) if args.rolling \
            else run(store, cfg)

    if args.rolling:
        _print_rolling(res)
    elif not res["ok"]:
        print("Backtest not ready:", res["reason"])
    else:
        print(f"Backtest {res['entry']} -> {res['exit']} ({res['span_days']}d)")
        for v, d in res["by_verdict"].items():
            print(f"  {v:6} n={d['n']:4}  avg forward {d['avg_return_pct']:+.2f}%  "
                  f"hit-rate {d['hit_rate_pct']}%")
    if args.md_out and args.rolling:
        with open(args.md_out, "a", encoding="utf-8") as fh:
            fh.write(_markdown_rolling(res))
    if args.json:
        print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
