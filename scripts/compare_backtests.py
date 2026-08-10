#!/usr/bin/env python3
"""Create a transparent legacy-versus-SFA comparison artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def summary(payload):
    return {
        k: payload.get(k)
        for k in (
            "run_id",
            "snapshot_id",
            "window",
            "cohorts",
            "unique_stocks",
            "buy_minus_avoid_pct",
            "spread_ci90",
            "significant",
            "monotonic",
        )
    } | {
        "by_verdict": payload.get("by_verdict", {}),
        "universe_bias": payload.get("data_quality", {}).get("universe_bias"),
        "benchmark": payload.get("strategy", {}).get("benchmark"),
        "entry": payload.get("strategy", {}).get("entry"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--legacy", default="web/data/backtest.json")
    ap.add_argument("--sfa", default="web/data/backtest-sfa.json")
    ap.add_argument("--out", default="data/sharadar/reports/legacy-vs-sfa.json")
    args = ap.parse_args()
    legacy = json.loads(Path(args.legacy).read_text(encoding="utf-8"))
    sfa = json.loads(Path(args.sfa).read_text(encoding="utf-8"))
    result = {
        "comparison_warning": (
            "These runs use different universes, history, entries and benchmarks. "
            "Differences diagnose data/model sensitivity; they are not an A/B test."
        ),
        "legacy": summary(legacy),
        "sfa": summary(sfa),
        "material_changes": [
            "current survivors -> point-in-time active and delisted universe",
            "seeded/partly proxied fundamentals -> as-reported SFA filings",
            "sampled weekly prices -> daily corporate-action-aware prices",
            "cohort mean -> SPY total-return benchmark",
            "same snapshot close -> next trading close entry",
        ],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
