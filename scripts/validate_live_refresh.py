#!/usr/bin/env python3
"""Validate that a live export used fresh official and Emerging universes."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.refresh_validation import validate_live_refresh


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", default="web/data/board.json")
    parser.add_argument("--db", default="data/fairentry.db")
    parser.add_argument("--max-age-minutes", type=float, default=120)
    args = parser.parse_args()
    result = validate_live_refresh(
        args.board, args.db, max_age_minutes=args.max_age_minutes)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
