#!/usr/bin/env python3
"""Stream the large private replay and summarize baseline/challenger episodes."""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import ijson


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _wilson(successes: int, total: int, z: float = 1.6448536269514722):
    if not total:
        return [None, None]
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return [round(max(0, center - margin) * 100, 1),
            round(min(1, center + margin) * 100, 1)]


def _root_record(row: dict) -> dict:
    tuning = row.get("_tuning_outcome") or {}
    horizon = (row.get("horizons") or {}).get("365") or {}
    hit = (tuning.get("first_hit_days_by_target") or {}).get("30")
    if hit is None:
        hit = tuning.get("first_hit_primary_days")
    return {
        "observation_id": row.get("observation_id"),
        "issuer": row.get("issuer_key") or row.get("security_id") or row.get("ticker"),
        "ticker": row.get("ticker"),
        "entry_date": row.get("entry_date"),
        "year": str(row.get("entry_date") or "")[:4],
        "sector": row.get("sector") or "Unknown",
        "hit_days": _number(hit),
        "last_observed_days": _number(tuning.get("last_observed_days")),
        "terminal_days": _number(tuning.get("terminal_days")),
        "return_365_pct": _number(tuning.get("return_pct") or horizon.get("return_pct")),
        "alpha_365_pct": _number(tuning.get("alpha_pct") or horizon.get("alpha_pct")),
        "max_drawdown_365_pct": _number(tuning.get("max_drawdown_pct")),
    }


def _complete(row: dict) -> bool:
    return bool((row["last_observed_days"] is not None and row["last_observed_days"] >= 365)
                or (row["terminal_days"] is not None and row["terminal_days"] <= 365)
                or (row["hit_days"] is not None and row["hit_days"] <= 365))


def _summary(episodes: list[dict], *, groups=True) -> dict:
    completed = [row for row in episodes if _complete(row)]
    hits = [row for row in completed
            if row["hit_days"] is not None and row["hit_days"] <= 365]
    mature = [row for row in episodes if row["alpha_365_pct"] is not None]
    alphas = [row["alpha_365_pct"] for row in mature]
    returns = [row["return_365_pct"] for row in mature
               if row["return_365_pct"] is not None]
    drawdowns = [row["max_drawdown_365_pct"] for row in mature
                 if row["max_drawdown_365_pct"] is not None]
    result = {
        "episodes": len(episodes),
        "completed_one_year_episodes": len(completed),
        "unique_issuers": len({row["issuer"] for row in episodes}),
        "reached_30pct_within_one_year": len(hits),
        "hit_rate_pct": round(len(hits) / len(completed) * 100, 1) if completed else None,
        "hit_rate_ci90": _wilson(len(hits), len(completed)),
        "mean_one_year_alpha_pct": round(statistics.mean(alphas), 2) if alphas else None,
        "median_one_year_alpha_pct": round(statistics.median(alphas), 2) if alphas else None,
        "mean_one_year_return_pct": round(statistics.mean(returns), 2) if returns else None,
        "median_max_drawdown_pct": round(statistics.median(drawdowns), 2)
        if drawdowns else None,
    }
    if groups:
        for field, output in (("year", "by_year"), ("sector", "by_sector")):
            buckets = defaultdict(list)
            for row in episodes:
                buckets[row[field]].append(row)
            result[output] = [dict(key=key, **_summary(rows, groups=False))
                              for key, rows in sorted(buckets.items())]
    return result


def summarize(path: Path, max_gap_days: int = 45) -> dict:
    states = {"baseline": {}, "challenger": {}}
    episodes = {"baseline": [], "challenger": []}
    migrations = Counter()
    coverage = Counter()
    observations = 0
    with path.open("rb") as stream:
        for row in ijson.items(stream, "observations.item"):
            observations += 1
            baseline = row.get("verdict") or "Unknown"
            result = row.get("business_health_challenger") or {}
            challenge = result.get("verdict") or "Unknown"
            migrations[(baseline, challenge)] += 1
            coverage["eligible" if result.get("coverage_eligible")
                     else "ineligible"] += 1
            issuer = row.get("issuer_key") or row.get("security_id") or row.get("ticker")
            entry = row.get("entry_date")
            if not issuer or not entry:
                continue
            current_date = date.fromisoformat(entry)
            for model, verdict in (("baseline", baseline), ("challenger", challenge)):
                state = states[model].get(issuer, {"active": False, "previous": None})
                gap = ((current_date - state["previous"]).days
                       if state["previous"] is not None else None)
                if verdict == "Buy" and (not state["active"] or gap is None
                                          or gap > max_gap_days):
                    episodes[model].append(_root_record(row))
                state["active"] = verdict == "Buy"
                state["previous"] = current_date
                states[model][issuer] = state
    baseline_ids = {row["observation_id"] for row in episodes["baseline"]}
    challenger_ids = {row["observation_id"] for row in episodes["challenger"]}
    baseline_only = [row for row in episodes["baseline"]
                     if row["observation_id"] not in challenger_ids]
    challenger_only = [row for row in episodes["challenger"]
                       if row["observation_id"] not in baseline_ids]
    return {
        "method": "continuous_buy_episode_roots_v1",
        "source": str(path),
        "observations": observations,
        "max_gap_days": max_gap_days,
        "coverage": dict(coverage),
        "verdict_migration": [
            {"baseline": old, "challenger": new, "observations": count}
            for (old, new), count in sorted(migrations.items())
        ],
        "episode_overlap": {
            "same_root": len(baseline_ids & challenger_ids),
            "baseline_only": len(baseline_ids - challenger_ids),
            "challenger_only": len(challenger_ids - baseline_ids),
            "removed_episode_outcomes": _summary(baseline_only, groups=False),
            "added_episode_outcomes": _summary(challenger_only, groups=False),
        },
        "baseline": _summary(episodes["baseline"]),
        "challenger": _summary(episodes["challenger"]),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = summarize(args.artifact)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
