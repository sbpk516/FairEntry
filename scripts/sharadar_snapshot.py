#!/usr/bin/env python3
"""Download, verify and optionally import a private Sharadar SFA snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.sharadar import DEFAULT_TABLES, SharadarWarehouse, SnapshotDownloader


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tables", default=",".join(DEFAULT_TABLES))
    parser.add_argument("--snapshot-id")
    parser.add_argument("--no-extract", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--build-warehouse", action="store_true")
    parser.add_argument("--warehouse")
    args = parser.parse_args()
    tables = tuple(t.strip().upper() for t in args.tables.split(",") if t.strip())
    snapshot = SnapshotDownloader().download_snapshot(
        tables,
        snapshot_id=args.snapshot_id,
        extract=not args.no_extract,
        resume=not args.no_resume,
    )
    print(f"Snapshot complete: {snapshot}")
    if args.build_warehouse:
        with SharadarWarehouse(args.warehouse) as warehouse:
            stats = warehouse.build(snapshot)
            print(json.dumps({"imported": stats, "audit": warehouse.audit()}, indent=2))


if __name__ == "__main__":
    main()
