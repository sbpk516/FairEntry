#!/usr/bin/env python3
"""Compare original scoring with individual and combined model changes."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.backtest.ablation import run_ablation
from fairentry.config import load_config
from fairentry.store import Store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/backtest.db")
    ap.add_argument("--hold", type=int, default=30)
    ap.add_argument("--step", type=int, default=14)
    ap.add_argument("--warmup", type=int, default=300)
    ap.add_argument("--min-names", type=int, default=20)
    ap.add_argument("--bootstrap", type=int, default=500)
    ap.add_argument("--full-universe", action="store_true")
    ap.add_argument("--json-out")
    args = ap.parse_args()

    with Store(args.db) as store:
        report = run_ablation(
            store, load_config(), hold_days=args.hold, step_days=args.step,
            min_names=args.min_names, screened_only=not args.full_universe,
            warmup_days=args.warmup, bootstrap=args.bootstrap,
        )

    print("variant                         buys  buy alpha  avoid alpha  spread   hit rate  CI90")
    for name, result in report["results"].items():
        if not result.get("ok"):
            print(f"{name:31} NOT READY: {result.get('reason')}")
            continue
        buy = result.get("by_verdict", {}).get("Buy", {})
        avoid = result.get("by_verdict", {}).get("Avoid", {})
        ci = result.get("spread_ci90")
        ci_text = f"[{ci[0]:+.2f},{ci[1]:+.2f}]" if ci else "n/a"
        print(f"{name:31} {buy.get('n', 0):5d}  "
              f"{buy.get('mean_alpha_pct', float('nan')):+9.2f}%  "
              f"{avoid.get('mean_alpha_pct', float('nan')):+10.2f}%  "
              f"{(result.get('buy_minus_avoid_pct') or 0):+7.2f}%  "
              f"{buy.get('hit_rate_pct', 0):7.1f}%  {ci_text}")

    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
