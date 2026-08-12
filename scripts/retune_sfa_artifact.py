#!/usr/bin/env python3
"""Re-run only the weight challenger over an existing private SFA artifact."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.backtest.sfa_tune import policy_from_strategy, tune_sfa_observations
from fairentry.backtest.strategy import load_strategy
from fairentry.config import load_config
from scripts.sfa_backtest import public_artifact


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--private", default="data/sharadar/reports/backtest-sfa-full.json")
    parser.add_argument("--public", default="web/data/backtest-sfa.json")
    parser.add_argument("--strategy", default="config/backtest_sfa.yaml")
    parser.add_argument("--candidates", type=int)
    args = parser.parse_args()

    private_path = Path(args.private)
    result = json.loads(private_path.read_text(encoding="utf-8"))
    strategy = load_strategy(args.strategy)
    result["weight_validation"] = tune_sfa_observations(
        result.get("observations", []),
        load_config(),
        step_days=int(result.get("step_days", 30)),
        candidates=args.candidates,
        policy=policy_from_strategy(strategy),
        progress=lambda row: print(json.dumps({"tuning_progress": row}), flush=True),
    )
    private_path.write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
    public_path = Path(args.public)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.write_text(
        json.dumps(public_artifact(result), separators=(",", ":")), encoding="utf-8"
    )
    print(json.dumps({"decision": result["weight_validation"].get("decision"),
                      "run_id": result.get("run_id")}))


if __name__ == "__main__":
    main()
