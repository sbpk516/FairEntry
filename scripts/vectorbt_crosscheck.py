#!/usr/bin/env python3
"""Cross-check FairEntry's recorded returns with VectorBT Community."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.backtest.vectorbt_crosscheck import (
    artifact_metadata,
    crosscheck,
    selected_observations,
)
from fairentry.sharadar import SharadarWarehouse


def _csv_set(value: str, cast=str):
    return {cast(item.strip()) for item in value.split(",") if item.strip()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--artifact", default="data/sharadar/reports/backtest-sfa-full.json",
        help="private or public SFA result containing observations",
    )
    parser.add_argument("--warehouse", default="data/sharadar/warehouse.duckdb")
    parser.add_argument("--verdicts", default="Buy")
    parser.add_argument(
        "--horizons", default="30,60,90,180,365,548,730,1095,1825",
    )
    parser.add_argument("--max-observations", type=int)
    parser.add_argument("--tolerance-pct", type=float, default=0.02)
    parser.add_argument(
        "--json-out", default="data/reports/vectorbt-crosscheck.json",
    )
    parser.add_argument(
        "--require-complete", action="store_true",
        help="also fail when a recorded result lacks a raw comparable price",
    )
    args = parser.parse_args()

    verdicts = _csv_set(args.verdicts)
    horizons = _csv_set(args.horizons, int)
    observations = selected_observations(
        args.artifact, verdicts, args.max_observations
    )
    with SharadarWarehouse(args.warehouse, read_only=True) as warehouse:
        report = crosscheck(
            observations,
            warehouse.con,
            horizons=horizons,
            tolerance_pct=args.tolerance_pct,
        )
    report.update({
        "artifact": str(Path(args.artifact)),
        "artifact_identity": artifact_metadata(args.artifact),
        "warehouse": str(Path(args.warehouse)),
        "verdicts": sorted(verdicts),
        "horizons_days": sorted(horizons),
        "selected_observations": len(observations),
        "complete_coverage_required": args.require_complete,
    })
    if args.require_complete and report["excluded"]:
        report["ok"] = False
    output = Path(args.json_out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
