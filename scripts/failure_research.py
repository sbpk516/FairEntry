#!/usr/bin/env python3
"""Manage the source-backed research queue for new +30% one-year failures."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.backtest.failure_research import (  # noqa: E402
    DEFAULT_QUEUE,
    DEFAULT_RESEARCH,
    apply_verified_findings,
    build_research_queue,
)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    queue = sub.add_parser("queue", help="identify failed episodes needing research")
    queue.add_argument("--backtest", default="web/data/backtest.json")
    queue.add_argument("--out", default=str(DEFAULT_QUEUE))
    queue.add_argument("--research", default=str(DEFAULT_RESEARCH))
    apply = sub.add_parser("apply", help="append validated, source-backed findings")
    apply.add_argument("--findings", required=True)
    apply.add_argument("--research", default=str(DEFAULT_RESEARCH))
    args = parser.parse_args()
    if args.command == "queue":
        backtest = json.loads(Path(args.backtest).read_text(encoding="utf-8"))
        result = build_research_queue(backtest, research_path=Path(args.research), queue_path=Path(args.out))
        print(json.dumps(result["summary"], indent=2))
    else:
        added = apply_verified_findings(Path(args.findings), research_path=Path(args.research))
        print(f"Added {len(added)} researched ticker(s): {', '.join(added) or 'none'}")


if __name__ == "__main__":
    main()
