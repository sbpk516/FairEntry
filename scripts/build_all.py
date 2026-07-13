#!/usr/bin/env python3
"""End-to-end: (optionally refresh) -> screen -> score -> export board.json.

  python scripts/build_all.py            # score from existing store, export
  python scripts/build_all.py --refresh  # refresh data first
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.config import load_config
from fairentry.store import Store
from fairentry.catalog.refresh import refresh
from fairentry.pipeline.export import build_board, write_board


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="refresh data before scoring")
    args = ap.parse_args()
    cfg = load_config()
    with Store() as store:
        if args.refresh:
            print("Refreshing…")
            refresh(cfg, store)
        print("Screening + scoring…")
        board = build_board(cfg, store)
        path = write_board(board)
    from collections import Counter
    v = Counter(s["verdict"] if "verdict" in s else "" for s in [])  # verdict is recomputed in UI
    print(f"Exported {board['meta']['count']} stocks -> {path}")
    print(f"Sectors: {board['meta']['sectors']}")


if __name__ == "__main__":
    main()
