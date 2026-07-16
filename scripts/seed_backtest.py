#!/usr/bin/env python3
"""Seed a backtestable point-in-time history from real prices (yfinance).

Prereq: a populated live store (run `python scripts/build_all.py --refresh`
first) — the seeder scales/holds today's fundamentals over the historical price
path. Writes to data/backtest.db (never touches the live data/fairentry.db).

  python scripts/seed_backtest.py                 # seed the whole universe
  python scripts/seed_backtest.py --limit 150     # top-150 by market cap (faster)
  python scripts/seed_backtest.py --weeks 200     # deeper history
  python scripts/seed_backtest.py --tickers AAPL MSFT NVDA
  python scripts/seed_backtest.py --sec-history --limit 150

Then:  python scripts/backtest.py --db data/backtest.db --rolling
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.store.db import DEFAULT_DB
from fairentry.backtest.seed import seed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(DEFAULT_DB), help="live store to read current metrics from")
    ap.add_argument("--dst", default=str(DEFAULT_DB.parent / "backtest.db"), help="backtest store to write")
    ap.add_argument("--weeks", type=int, default=208, help="weeks of price history to pull (~4y; >=200 for the 200-week MA)")
    ap.add_argument("--limit", type=int, default=None, help="cap to top-N by market cap")
    ap.add_argument("--tickers", nargs="*", default=None, help="only these tickers")
    ap.add_argument("--sec-history", action="store_true",
                    help="reconstruct SEC filing fundamentals by filed date where available")
    args = ap.parse_args()

    if not Path(args.src).exists():
        print(f"No live store at {args.src}. Run: python scripts/build_all.py --refresh")
        sys.exit(1)
    mode = "yfinance weekly closes + SEC filing fundamentals" if args.sec_history else "yfinance weekly closes"
    print(f"Seeding backtest history from {args.src} ({mode})…")
    res = seed(args.src, args.dst, tickers=args.tickers, weeks=args.weeks,
               limit=args.limit, use_sec_history=args.sec_history)
    sec_note = f", {res.get('sec_seeded', 0)} with SEC history" if args.sec_history else ""
    print(f"Done: {res['seeded']} tickers{sec_note} -> {res['db']}")
    if res["seeded"] == 0:
        print("Nothing seeded — check network access to Yahoo Finance and that the "
              "live store has prices.")


if __name__ == "__main__":
    main()
