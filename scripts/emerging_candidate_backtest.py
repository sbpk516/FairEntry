#!/usr/bin/env python3
"""Replay the fixed Emerging Research v2 variants from point-in-time SFA data."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.backtest.emerging_candidates import (
    attach_historical_entry_evidence,
    run_emerging_candidate_backtest,
)
from fairentry.backtest.sfa_replay import run_sfa_rolling
from fairentry.backtest.strategy import load_strategy
from fairentry.backtest.valuation_research import attach_valuation_factors
from fairentry.config import load_config
from fairentry.sharadar import SharadarWarehouse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", default="data/sharadar/warehouse.duckdb")
    parser.add_argument("--strategy", default="config/backtest_sfa.yaml")
    parser.add_argument("--output", default="data/sharadar/reports/emerging-research-v2.json")
    parser.add_argument("--artifact", default="web/data/backtest-sfa.json")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--step", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=300)
    parser.add_argument("--min-names", type=int, default=20)
    parser.add_argument("--top-n", type=int)
    args = parser.parse_args()

    cfg = load_config()
    policy = cfg.sectors.get("emerging_candidates", {})
    strategy = replace(
        load_strategy(args.strategy), avg_dollar_volume_min_usd=5_000_000,
        universe_top_n=args.top_n or load_strategy(args.strategy).universe_top_n,
    )
    with SharadarWarehouse(args.warehouse, read_only=True) as warehouse:
        replay = run_sfa_rolling(
            warehouse, cfg, hold_days=30, step_days=args.step,
            warmup_days=args.warmup, min_names=args.min_names, bootstrap=0,
            start=args.start, end=args.end, strategy=strategy,
            include_evidence=False,
            progress=lambda row: print(json.dumps({"progress": row}), flush=True),
        )
        if not replay.get("ok"):
            raise SystemExit(replay.get("reason") or "emerging replay failed")
        valuation = attach_valuation_factors(
            replay["observations"], warehouse.con, buy_only=False)
        entry = attach_historical_entry_evidence(
            replay["observations"], warehouse.con, policy)
        result = run_emerging_candidate_backtest(
            replay["observations"], policy, step_days=args.step)

    result["replay"] = {
        "run_id": replay.get("run_id"), "snapshot_id": replay.get("snapshot_id"),
        "window": replay.get("window"), "cohorts": replay.get("cohorts"),
        "unique_stocks": replay.get("unique_stocks"), "base_floor_usd": 5_000_000,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    result["enrichment"] = {"valuation": valuation, "entry_evidence": entry}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")

    artifact = Path(args.artifact)
    if artifact.exists():
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        payload["emerging_candidate_backtest"] = result
        artifact.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({
        "output": str(output), "artifact": str(artifact) if artifact.exists() else None,
        "validation": result.get("validation"),
    }, indent=2))


if __name__ == "__main__":
    main()
