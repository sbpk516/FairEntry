#!/usr/bin/env python3
"""Rebuild the point-in-time factor comparison for Buy successes/failures."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import ijson

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fairentry.backtest.factor_explorer import (  # noqa: E402
    attach_warehouse_factors,
    run_factor_explorer,
)
from fairentry.backtest.evidence import _fixed_horizon_evaluation  # noqa: E402
from fairentry.sharadar import SharadarWarehouse  # noqa: E402


def episode_roots(path: Path, max_gap_days: int = 45) -> list[dict]:
    """Stream recorded production verdicts and retain first Buy observations."""
    states: dict[str, dict] = {}
    roots = []
    with path.open("rb") as stream:
        for row in ijson.items(stream, "observations.item", use_float=True):
            issuer = row.get("issuer_key") or row.get("security_id") or row.get("ticker")
            entry = row.get("entry_date")
            if not issuer or not entry:
                continue
            current = date.fromisoformat(entry)
            state = states.get(str(issuer), {"active": False, "previous": None})
            gap = ((current - state["previous"]).days
                   if state["previous"] is not None else None)
            is_buy = row.get("verdict") == "Buy"
            if is_buy and (not state["active"] or gap is None or gap > max_gap_days):
                roots.append(row)
            state["active"] = is_buy
            state["previous"] = current
            states[str(issuer)] = state
    return roots


def analyze(path: Path, warehouse_path: Path) -> dict:
    roots = episode_roots(path)
    with SharadarWarehouse(warehouse_path, read_only=True) as warehouse:
        enrichment = attach_warehouse_factors(roots, warehouse.con)
    result = run_factor_explorer(roots, step_days=30)
    buckets = {
        "improving_above_1pp": [],
        "stable_minus_1pp_to_1pp": [],
        "declining_below_minus_1pp": [],
        "missing": [],
    }
    for row in roots:
        evaluation = _fixed_horizon_evaluation(row, 30, 365)["result"]
        if evaluation not in {"success", "failure"}:
            continue
        value = (row.get("research_factors") or {}).get(
            "net_profit_margin_change_yoy_pp"
        )
        if not isinstance(value, (int, float)):
            bucket = "missing"
        elif value > 1:
            bucket = "improving_above_1pp"
        elif value < -1:
            bucket = "declining_below_minus_1pp"
        else:
            bucket = "stable_minus_1pp_to_1pp"
        buckets[bucket].append(evaluation == "success")
    result["net_profit_margin_trend_buckets"] = {
        key: {
            "completed_episodes": len(values),
            "successes": sum(values),
            "failures": len(values) - sum(values),
            "hit_rate_pct": round(sum(values) / len(values) * 100, 1)
            if values else None,
        }
        for key, values in buckets.items()
    }
    result.update({
        "source": str(path),
        "factor_enrichment": enrichment,
        "scope": "First observation of each recorded production Buy episode",
    })
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--warehouse", type=Path,
                        default=Path("data/sharadar/warehouse.duckdb"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = analyze(args.artifact, args.warehouse)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
