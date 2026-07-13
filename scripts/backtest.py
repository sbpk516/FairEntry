#!/usr/bin/env python3
"""Backtest the scoring model over accumulated point-in-time history.

  python scripts/backtest.py            # replay + report by verdict
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.config import load_config
from fairentry.store import Store
from fairentry.backtest.harness import run


def main():
    cfg = load_config()
    with Store() as store:
        res = run(store, cfg)
    if not res["ok"]:
        print("Backtest not ready:", res["reason"])
        return
    print(f"Backtest {res['entry']} -> {res['exit']} ({res['span_days']}d)")
    for v, d in res["by_verdict"].items():
        print(f"  {v:6} n={d['n']:4}  avg forward {d['avg_return_pct']:+.2f}%  "
              f"hit-rate {d['hit_rate_pct']}%")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
