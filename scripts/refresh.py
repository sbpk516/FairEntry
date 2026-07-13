#!/usr/bin/env python3
"""Refresh the FairEntry store from catalog sources.

Usage:
  python scripts/refresh.py                 # finviz universe (fast)
  python scripts/refresh.py --wma AVNT,MU   # + 200-week MA for those tickers
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.config import load_config
from fairentry.store import Store
from fairentry.catalog.refresh import refresh


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wma", default="", help="comma-separated tickers for 200wma")
    args = ap.parse_args()
    cfg = load_config()
    print(f"Refreshing sectors: {[s['id'] for s in cfg.enabled_sectors]}")
    with Store() as store:
        summary = refresh(cfg, store,
                          wma_tickers=[t.strip().upper() for t in args.wma.split(",") if t.strip()])
    print("Done.", summary["sources"])


if __name__ == "__main__":
    main()
