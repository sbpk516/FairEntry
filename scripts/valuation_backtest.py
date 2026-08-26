#!/usr/bin/env python3
"""Run valuation challengers from an existing private SFA artifact."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import ijson

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.backtest.factor_explorer import attach_warehouse_factors
from fairentry.backtest.valuation_research import (
    attach_valuation_factors,
    run_valuation_research,
)
from fairentry.sharadar import SharadarWarehouse


def _buy_observations(path: Path):
    observations = []
    with path.open("rb") as source:
        for row in ijson.items(source, "observations.item", use_float=True):
            if row.get("verdict") == "Buy":
                observations.append(row)
    return observations


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--private", default="data/sharadar/reports/backtest-sfa-full.json")
    parser.add_argument("--warehouse", default="data/sharadar/warehouse.duckdb")
    parser.add_argument("--public", default="web/data/backtest-sfa.json")
    parser.add_argument("--report", default="data/sharadar/reports/valuation-research.json")
    parser.add_argument("--step", type=int, default=30)
    args = parser.parse_args()

    observations = _buy_observations(Path(args.private))
    with SharadarWarehouse(args.warehouse, read_only=True) as warehouse:
        factor_enrichment = attach_warehouse_factors(observations, warehouse.con)
        valuation_enrichment = attach_valuation_factors(observations, warehouse.con)
    result = run_valuation_research(observations, step_days=args.step)
    result["enrichment"] = {
        "factor_inputs": factor_enrichment,
        "valuation_inputs": valuation_enrichment,
    }

    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, indent=2), encoding="utf-8")

    public = Path(args.public)
    payload = json.loads(public.read_text(encoding="utf-8"))
    payload["valuation_research"] = result
    public.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({
        "buy_observations": len(observations),
        "report": str(report),
        "public": str(public),
        "baseline_completed": result.get("baseline", {}).get("all", {}).get("primary", {}).get("completed_episodes"),
    }, indent=2))


if __name__ == "__main__":
    main()
