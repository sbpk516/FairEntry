#!/usr/bin/env python3
"""Stream a replay and compare practical reported-EPS Buy gates."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import ijson

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fairentry.backtest.eps_growth_challenger import (  # noqa: E402
    attach_eps_ttm_factors,
    eps_cagr_3y,
    score_challenger,
    verdict,
)
from fairentry.sharadar import SharadarWarehouse  # noqa: E402
from scripts.summarize_business_health_backtest import _root_record, _summary  # noqa: E402


def summarize(path: Path, thresholds: list[float], score_weights: list[float],
              warehouse_path: Path,
              max_gap_days: int = 45) -> dict:
    gate_models = [f"eps_at_least_{value:g}" for value in thresholds]
    score_models = [f"eps_score_weight_{value:g}" for value in score_weights]
    models = ["baseline", *gate_models, *score_models]
    states = {model: {} for model in models}
    episodes = {model: [] for model in models}
    migrations = {model: Counter() for model in models[1:]}
    coverage = Counter()
    observations = 0

    minimal_rows = []
    with path.open("rb") as stream:
        for row in ijson.items(stream, "observations.item"):
            minimal_rows.append({key: row.get(key) for key in (
                "observation_id", "ticker", "decision_date"
            )})
    with SharadarWarehouse(warehouse_path, read_only=True) as warehouse:
        enrichment = attach_eps_ttm_factors(minimal_rows, warehouse.con)
    exact_factors = {row["observation_id"]: row.get("research_factors") or {}
                     for row in minimal_rows}

    with path.open("rb") as stream:
        for row in ijson.items(stream, "observations.item"):
            observations += 1
            row["research_factors"] = exact_factors.get(row.get("observation_id"), {})
            baseline = row.get("verdict") or "Unknown"
            growth = eps_cagr_3y(row)
            coverage["available" if growth is not None else "missing"] += 1
            issuer = row.get("issuer_key") or row.get("security_id") or row.get("ticker")
            entry = row.get("entry_date")
            if not issuer or not entry:
                continue

            verdicts = {"baseline": baseline}
            for threshold in thresholds:
                model = f"eps_at_least_{threshold:g}"
                verdicts[model] = verdict(row, threshold)
                migrations[model][(baseline, verdicts[model])] += 1
            for weight in score_weights:
                model = f"eps_score_weight_{weight:g}"
                verdicts[model] = score_challenger(row, weight)["verdict"]
                migrations[model][(baseline, verdicts[model])] += 1

            current_date = date.fromisoformat(entry)
            for model, current_verdict in verdicts.items():
                state = states[model].get(issuer, {"active": False, "previous": None})
                gap = ((current_date - state["previous"]).days
                       if state["previous"] is not None else None)
                if (current_verdict == "Buy"
                        and (not state["active"] or gap is None or gap > max_gap_days)):
                    episodes[model].append(_root_record(row))
                state["active"] = current_verdict == "Buy"
                state["previous"] = current_date
                states[model][issuer] = state

    baseline_ids = {row["observation_id"] for row in episodes["baseline"]}
    variants = {}
    for threshold in thresholds:
        model = f"eps_at_least_{threshold:g}"
        ids = {row["observation_id"] for row in episodes[model]}
        removed = [row for row in episodes["baseline"]
                   if row["observation_id"] not in ids]
        variants[str(threshold)] = {
            "minimum_eps_cagr_3y_pct": threshold,
            "verdict_migration": [
                {"baseline": old, "challenger": new, "observations": count}
                for (old, new), count in sorted(migrations[model].items())
            ],
            "episode_overlap": {
                "same_root": len(baseline_ids & ids),
                "removed": len(baseline_ids - ids),
                "removed_episode_outcomes": _summary(removed, groups=False),
            },
            "challenger": _summary(episodes[model]),
        }

    score_variants = {}
    for weight in score_weights:
        model = f"eps_score_weight_{weight:g}"
        ids = {row["observation_id"] for row in episodes[model]}
        removed = [row for row in episodes["baseline"]
                   if row["observation_id"] not in ids]
        added = [row for row in episodes[model]
                 if row["observation_id"] not in baseline_ids]
        score_variants[str(weight)] = {
            "eps_weight_within_growth_pct": weight,
            "effective_top_level_weight_pct": round(weight / 100 * 33.75, 3),
            "verdict_migration": [
                {"baseline": old, "challenger": new, "observations": count}
                for (old, new), count in sorted(migrations[model].items())
            ],
            "episode_overlap": {
                "same_root": len(baseline_ids & ids),
                "removed": len(baseline_ids - ids),
                "added": len(ids - baseline_ids),
                "removed_episode_outcomes": _summary(removed, groups=False),
                "added_episode_outcomes": _summary(added, groups=False),
            },
            "challenger": _summary(episodes[model]),
        }

    return {
        "model": "reported_ttm_diluted_eps_cagr_3y_buy_gate_v1",
        "status": "research_only",
        "production_effect": "none",
        "method": "existing Buy capped to Watch when EPS CAGR is missing or below threshold",
        "source": str(path),
        "observations": observations,
        "enrichment": enrichment,
        "eps_coverage": dict(coverage),
        "baseline": _summary(episodes["baseline"]),
        "variants": variants,
        "score_variants": score_variants,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--thresholds", type=float, nargs="+", default=[0, 5, 10, 15, 20])
    parser.add_argument("--score-weights", type=float, nargs="+", default=[10, 20, 30])
    parser.add_argument("--warehouse", type=Path,
                        default=Path("data/sharadar/warehouse.duckdb"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = summarize(args.artifact, args.thresholds, args.score_weights,
                       args.warehouse)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
