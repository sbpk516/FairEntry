#!/usr/bin/env python3
"""Add the 200-week study to an existing public SFA artifact without a full replay.

The private SFA result can be larger than available memory.  This command
streams only the observation fields required by the study, leaves the private
file untouched, and updates the small redacted public artifact.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import ijson

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.backtest.wma200_research import (  # noqa: E402
    attach_wma200_factors,
    run_wma200_research,
)
from fairentry.sharadar import SharadarWarehouse  # noqa: E402


KEEP = {
    "observation_id", "issuer_key", "security_id", "ticker", "decision_date",
    "entry_date", "verdict", "categories", "vetoes", "return_milestones",
    "research_factors",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--private", default="data/sharadar/reports/backtest-sfa-full.json")
    parser.add_argument("--warehouse", default="data/sharadar/warehouse.duckdb")
    parser.add_argument("--public", default="web/data/backtest-sfa.json")
    args = parser.parse_args()

    observations = []
    with Path(args.private).open("rb") as source:
        for row in ijson.items(source, "observations.item"):
            observations.append({key: row.get(key) for key in KEEP if key in row})

    with SharadarWarehouse(args.warehouse, read_only=True) as warehouse:
        enrichment = attach_wma200_factors(observations, warehouse.con)
    report = run_wma200_research(observations)
    report["enrichment"] = enrichment

    public_path = Path(args.public)
    artifact = json.loads(public_path.read_text(encoding="utf-8"))
    artifact["wma200_research"] = report
    public_path.write_text(json.dumps(artifact, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({
        "public": str(public_path),
        "observations": len(observations),
        "wma_enriched": enrichment.get("enriched"),
        "research_signal": report.get("research_signal"),
        "primary": report.get("threshold_comparison", [None])[0],
    }, indent=2))


if __name__ == "__main__":
    main()
