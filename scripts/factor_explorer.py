#!/usr/bin/env python3
"""Build and optionally publish the point-in-time factor research explorer."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.backtest.factor_explorer import (  # noqa: E402
    attach_warehouse_factors,
    run_factor_explorer,
)
from fairentry.sharadar import SharadarWarehouse  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backtest", default="data/sharadar/reports/backtest-sfa-full.json")
    parser.add_argument("--warehouse", default="data/sharadar/warehouse.duckdb")
    parser.add_argument("--out", default="web/data/factor-explorer.json")
    parser.add_argument(
        "--public-attach",
        help="attach the aggregate explorer to an existing redacted UI artifact",
    )
    args = parser.parse_args()

    artifact = json.loads(Path(args.backtest).read_text(encoding="utf-8"))
    observations = artifact.get("observations") or []
    with SharadarWarehouse(args.warehouse, read_only=True) as warehouse:
        enrichment = attach_warehouse_factors(observations, warehouse.con)
    report = run_factor_explorer(
        observations, step_days=int(artifact.get("step_days") or 30)
    )
    report["enrichment"] = enrichment

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.public_attach:
        public_path = Path(args.public_attach)
        public = json.loads(public_path.read_text(encoding="utf-8"))
        public["factor_explorer"] = report
        public_path.write_text(json.dumps(public, separators=(",", ":")), encoding="utf-8")

    walk = report.get("walk_forward") or {}
    print(json.dumps({
        "out": str(out),
        "ok": report.get("ok"),
        "enrichment": enrichment,
        "oos_baseline_pct": (walk.get("out_of_sample_baseline") or {}).get("success_rate_pct"),
        "oos_selected_pct": (walk.get("out_of_sample_selected") or {}).get("success_rate_pct"),
        "oos_improvement_pp": walk.get("out_of_sample_improvement_pp"),
        "research_signal": walk.get("research_signal"),
        "production_effect": report.get("production_effect"),
    }, indent=2))


if __name__ == "__main__":
    main()
