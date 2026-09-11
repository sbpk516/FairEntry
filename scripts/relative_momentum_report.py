#!/usr/bin/env python3
"""Refresh the frozen sector-relative momentum report without a full replay.

The private SFA artifact is large, so this command streams only the fields
needed to reconstruct official Buy episodes. It leaves the private artifact
unchanged and updates the public research report from point-in-time prices.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import ijson

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.backtest.relative_momentum_research import (  # noqa: E402
    run_relative_momentum_research,
)
from fairentry.sharadar import SharadarWarehouse  # noqa: E402


KEEP = {
    "issuer_key",
    "security_id",
    "ticker",
    "company",
    "sector",
    "decision_date",
    "entry_date",
    "verdict",
    "strategy_key",
    "return_milestones",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--private", default="data/sharadar/reports/backtest-sfa-full.json")
    parser.add_argument("--warehouse", default="data/sharadar/warehouse.duckdb")
    parser.add_argument("--public", default="web/data/backtest-sfa.json")
    parser.add_argument(
        "--report", default="data/sharadar/reports/relative-momentum-research.json"
    )
    parser.add_argument("--step", type=int, default=30)
    args = parser.parse_args()

    observations = []
    with Path(args.private).open("rb") as source:
        for row in ijson.items(source, "observations.item", use_float=True):
            observations.append({key: row.get(key) for key in KEEP if key in row})

    with SharadarWarehouse(args.warehouse, read_only=True) as warehouse:
        result = run_relative_momentum_research(
            observations,
            warehouse.con,
            step_days=args.step,
        )

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    public_path = Path(args.public)
    artifact = json.loads(public_path.read_text(encoding="utf-8"))
    artifact["relative_momentum_research"] = result
    public_path.write_text(json.dumps(artifact, separators=(",", ":")), encoding="utf-8")

    primary = result["results"]["all_history"]["groups"]["improving"]["outcomes"][
        "primary_30_within_one_year"
    ]
    print(json.dumps({
        "public": str(public_path),
        "report": str(report_path),
        "observations": len(observations),
        "buy_episodes": result["coverage"]["earliest_buy_episodes"],
        "supportive_completed": primary["completed_episodes"],
        "supportive_successes": primary["reached"],
        "supportive_success_rate_pct": primary["success_rate_pct"],
    }, indent=2))


if __name__ == "__main__":
    main()
