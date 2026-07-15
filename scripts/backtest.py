#!/usr/bin/env python3
"""Backtest the scoring model over accumulated point-in-time history.

  python scripts/backtest.py
  python scripts/backtest.py --rolling
  python scripts/backtest.py --db data/backtest.db --rolling --hold 30 --step 7

Seed a history first with:
  python scripts/seed_backtest.py
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.backtest.harness import run, run_rolling
from fairentry.config import load_config
from fairentry.store import Store
from fairentry.store.db import DEFAULT_DB


def _markdown_rolling(res):
    if not res["ok"]:
        return f"### Backtest not ready\n\n{res['reason']}\n"
    w = res["window"]
    lines = [
        "### Rolling backtest - Buy-filter alpha",
        f"`{w[0]} -> {w[1]}` - **{res['cohorts']} cohorts** - "
        f"hold {res['hold_days']}d / step {res['step_days']}d",
        "",
        "| verdict | n | mean alpha | median alpha | hit-rate | raw ret |",
        "|---|--:|--:|--:|--:|--:|",
    ]
    for verdict in ("Buy", "Watch", "Avoid"):
        row = res["by_verdict"].get(verdict)
        if row:
            lines.append(
                f"| {verdict} | {row['n']} | {row['mean_alpha_pct']:+.2f}% | "
                f"{row['median_alpha_pct']:+.2f}% | {row['hit_rate_pct']:.1f}% | "
                f"{row['mean_raw_return_pct']:+.2f}% |"
            )
    spread, mono = res.get("buy_minus_avoid_pct"), res.get("monotonic")
    verdict = "working" if (mono and (spread or 0) > 0) else "check"
    lines += [
        "",
        f"**Buy - Avoid alpha spread:** {spread:+.2f}% - "
        f"**monotonic (Buy>=Watch>=Avoid):** {mono} - {verdict}",
        "",
        "> alpha = each name's return minus its cohort's cross-sectional mean "
        "(measures stock selection, not market direction).",
    ]
    return "\n".join(lines) + "\n"


def _write_json(res, path):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, indent=1), encoding="utf-8")


def _print_signal_backtest(res):
    print(f"Prospective signal backtest ({res['signals']} recorded Buy/Watch signals)")
    for horizon, data in res["horizons"].items():
        print(f"\nHorizon {horizon}: {data['signals']} matured signals")
        for verdict, row in data["by_verdict"].items():
            print(
                f"  {verdict:6} n={row['n']:4} avg {row['avg_return_pct']:+.2f}% "
                f"median {row['median_return_pct']:+.2f}% hit {row['hit_rate_pct']}%"
            )
        for strategy, row in data["by_strategy"].items():
            print(
                f"  {strategy:14} n={row['n']:4} avg {row['avg_return_pct']:+.2f}% "
                f"hit {row['hit_rate_pct']}%"
            )


def _print_rolling(res):
    if not res["ok"]:
        print("Backtest not ready:", res["reason"])
        if "signals" in res:
            print(f"  recorded signals: {res['signals']}")
        return
    if res.get("mode") == "signal_events":
        _print_signal_backtest(res)
        print(json.dumps(res, indent=1))
        return

    start, end = res["window"]
    print(
        f"Rolling backtest {start} -> {end}  -  {res['cohorts']} cohorts  -  "
        f"hold {res['hold_days']}d / step {res['step_days']}d"
    )
    print(f"{'verdict':7} {'n':>6} {'mean alpha':>11} {'median':>9} {'hit-rate':>9} {'raw ret':>9}")
    for verdict in ("Buy", "Watch", "Avoid"):
        row = res["by_verdict"].get(verdict)
        if row:
            print(
                f"{verdict:7} {row['n']:>6} {row['mean_alpha_pct']:>+10.2f}% "
                f"{row['median_alpha_pct']:>+8.2f}% {row['hit_rate_pct']:>8.1f}% "
                f"{row['mean_raw_return_pct']:>+8.2f}%"
            )
    spread, mono = res.get("buy_minus_avoid_pct"), res.get("monotonic")
    print(f"\nBuy - Avoid alpha spread: {spread:+.2f}%   monotonic (Buy>=Watch>=Avoid): {mono}")
    print("alpha = return minus the cohort's cross-sectional mean (stock selection, not market).")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB), help="store to backtest, e.g. data/backtest.db")
    parser.add_argument("--rolling", action="store_true", help="rolling benchmark-relative alpha backtest")
    parser.add_argument("--hold", type=int, default=30, help="forward holding window in days")
    parser.add_argument("--step", type=int, default=7, help="days between cohort entries")
    parser.add_argument("--json", action="store_true", help="also dump the full result JSON")
    parser.add_argument("--json-out", default=None, help="write the full result JSON to this path")
    parser.add_argument("--md-out", default=None, help="write a markdown report to this path")
    args = parser.parse_args()

    cfg = load_config()
    with Store(args.db) as store:
        res = run_rolling(store, cfg, hold_days=args.hold, step_days=args.step) if args.rolling else run(store, cfg)

    if args.rolling:
        _print_rolling(res)
    elif not res["ok"]:
        print("Backtest not ready:", res["reason"])
        if "signals" in res:
            print(f"  recorded signals: {res['signals']}")
    elif res.get("mode") == "signal_events":
        _print_signal_backtest(res)
    else:
        print(f"Backtest {res['entry']} -> {res['exit']} ({res['span_days']}d)")
        for verdict, row in res["by_verdict"].items():
            print(
                f"  {verdict:6} n={row['n']:4}  avg forward {row['avg_return_pct']:+.2f}%  "
                f"hit-rate {row['hit_rate_pct']}%"
            )

    if args.md_out and args.rolling:
        with open(args.md_out, "a", encoding="utf-8") as fh:
            fh.write(_markdown_rolling(res))
    if args.json_out:
        _write_json(res, args.json_out)
    if args.json:
        print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
