#!/usr/bin/env python3
"""Run one $5M-floor SFA replay and compare four liquidity cutoffs."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.backtest.liquidity import compare_liquidity_thresholds
from fairentry.backtest.sfa_replay import run_sfa_rolling
from fairentry.backtest.strategy import load_strategy
from fairentry.config import load_config
from fairentry.sharadar import SharadarWarehouse


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--warehouse", default="data/sharadar/warehouse.duckdb")
    ap.add_argument("--strategy", default="config/backtest_sfa.yaml")
    ap.add_argument("--output", default="data/sharadar/reports/liquidity-thresholds.json")
    ap.add_argument("--step", type=int, default=30)
    ap.add_argument("--bootstrap", type=int, default=0)
    ap.add_argument("--artifact", default="web/data/backtest-sfa.json",
                    help="public SFA artifact to receive the aggregate comparison")
    ap.add_argument("--inject-only", action="store_true",
                    help="inject an existing --output report without rerunning SFA")
    args = ap.parse_args()
    if args.inject_only:
        comparison = json.loads(Path(args.output).read_text(encoding="utf-8"))
        artifact_path = Path(args.artifact)
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact["liquidity_threshold_comparison"] = comparison
        artifact_path.write_text(json.dumps(artifact, separators=(",", ":")), encoding="utf-8")
        print(json.dumps({"injected": str(artifact_path), "rows": len(comparison.get("rows", []))}))
        return
    strategy = replace(load_strategy(args.strategy), avg_dollar_volume_min_usd=5_000_000)
    with SharadarWarehouse(args.warehouse, read_only=True) as warehouse:
        result = run_sfa_rolling(
            warehouse, load_config(), hold_days=30, step_days=args.step,
            warmup_days=300, min_names=20, bootstrap=args.bootstrap,
            strategy=strategy, include_evidence=False,
            progress=lambda row: print(json.dumps({"progress": row}), flush=True),
        )
    if not result.get("ok"):
        raise SystemExit(result.get("reason") or "liquidity replay failed")
    comparison = compare_liquidity_thresholds(result.get("observations", []))
    comparison["replay"] = {"run_id": result.get("run_id"), "snapshot_id": result.get("snapshot_id"),
                            "window": result.get("window"), "cohorts": result.get("cohorts"),
                            "step_days": result.get("step_days"), "base_floor_usd": 5_000_000}
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(comparison, indent=2), encoding="utf-8")
    artifact_path = Path(args.artifact)
    if artifact_path.exists():
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact["liquidity_threshold_comparison"] = comparison
        artifact_path.write_text(json.dumps(artifact, separators=(",", ":")), encoding="utf-8")
    print(json.dumps(comparison, indent=2))


if __name__ == "__main__":
    main()
