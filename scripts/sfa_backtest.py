#!/usr/bin/env python3
"""Run the point-in-time SFA replay and write a UI-compatible artifact."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.backtest.sfa_replay import run_sfa_rolling
from fairentry.backtest.sfa_tune import policy_from_strategy, tune_sfa_observations
from fairentry.backtest.strategy import load_strategy
from fairentry.config import load_config
from fairentry.sharadar import SharadarWarehouse


def grouped_return_summary(observations: list[dict], hold_days: int) -> dict:
    """Aggregate return stability before public evidence rows are sampled."""
    output = {}
    for dimension, key_fn in {
        "by_year": lambda row: str(row.get("entry_date", ""))[:4] or "Unknown",
        "by_sector": lambda row: row.get("sector") or "Unknown",
    }.items():
        groups = {}
        for row in observations:
            value = row.get("horizons", {}).get(str(hold_days), {}).get("return_pct")
            if not isinstance(value, (int, float)):
                continue
            key = key_fn(row)
            group = groups.setdefault(
                key, {"Buy": [], "Watch": [], "Avoid": [], "tickers": set()}
            )
            group.setdefault(row.get("verdict"), []).append(value)
            group["tickers"].add(row.get("ticker"))
        output[dimension] = [
            {
                "key": key,
                "n": sum(len(group[v]) for v in ("Buy", "Watch", "Avoid")),
                "unique": len(group["tickers"]),
                **{
                    verdict: round(statistics.mean(group[verdict]), 2)
                    if group[verdict]
                    else None
                    for verdict in ("Buy", "Watch", "Avoid")
                },
            }
            for key, group in sorted(groups.items())
        ]
    return output


def public_artifact(result: dict, detail_limit: int = 2000) -> dict:
    """Remove reconstructable vendor inputs while retaining decision evidence."""
    # Python's encoder permits NaN, but browsers correctly reject it as invalid
    # JSON. Convert any missing dataframe number to null in the public copy.
    clean = json.loads(json.dumps(result), parse_constant=lambda _value: None)

    def public_currency_evidence(value):
        if not isinstance(value, dict):
            return value
        exact = value.pop("converted_fcf_usd", None)
        value.pop("reported_fcf", None)
        if isinstance(exact, (int, float)):
            value["converted_fcf_usd_rounded_millions"] = round(exact / 1_000_000)
        rate = value.get("historical_fxusd")
        if isinstance(rate, (int, float)):
            value["historical_fxusd"] = round(rate, 4)
        for key in ("reporting_currency", "country", "country_expected_currency"):
            if str(value.get(key) or "").lower() in {"nan", "nat", "none"}:
                value[key] = None
        value["licensed_amounts_redacted"] = True
        return value

    observations = clean.get("observations", [])
    clean["evidence_return_summary"] = grouped_return_summary(
        observations, int(clean.get("hold_days", 30))
    )
    # Keep a deterministic cross-time evidence sample plus the latest rows. Full
    # private evidence remains local; aggregate statistics always use all rows.
    if len(observations) > detail_limit:
        by_year = {}
        for row in observations:
            by_year.setdefault(row.get("entry_date", "")[:4], []).append(row)
        sample = [row for year in sorted(by_year) for row in by_year[year][:25]]
        latest = sorted(
            observations, key=lambda row: row.get("entry_date", ""), reverse=True
        )
        selected = {
            row.get("observation_id"): row for row in sample + latest[:detail_limit]
        }
        clean["observations"] = list(selected.values())
        clean["public_evidence_sample"] = {
            "displayed": len(clean["observations"]),
            "total": len(observations),
            "rule": "latest rows plus 25 deterministic observations per entry year",
        }
    for observation in clean.get("observations", []):
        for key in [key for key in observation if key.startswith("_")]:
            observation.pop(key, None)
        observation.pop("raw_close", None)
        observation.pop("security_id", None)
        observation.get("outcome", {}).pop("path", None)
        observation["categories"] = [
            {k: category.get(k) for k in ("id", "label", "weight", "score", "coverage")}
            for category in observation.get("categories", [])
        ]
        observation["screening"] = [
            {"id": row.get("id"), "passed": row.get("passed")}
            for row in observation.get("screening", [])
        ]
        quality = observation.get("data_quality", {})
        observation["quality_grade"] = quality.get("grade")
        observation["data_quality"] = {
            "grade": quality.get("grade"),
            "counts": quality.get("counts", {}),
            "fields": [],
        }
        observation["licensed_inputs_redacted"] = True
        observation["currency_conversion"] = public_currency_evidence(
            observation.get("currency_conversion")
        )
        if observation.get("terminal_event"):
            observation["terminal_event"].pop("value", None)
    for episode in (clean.get("buy_return_achievement", {})
                    .get("episode_details", [])):
        episode["currency_conversion"] = public_currency_evidence(
            episode.get("currency_conversion")
        )
        if episode.get("terminal_event"):
            episode["terminal_event"].pop("value", None)
    clean["public_data_boundary"] = {
        "raw_vendor_rows_exposed": False,
        "reconstructable_inputs_redacted": True,
        "attribution": "Data from Sharadar.com",
        "attribution_url": "https://www.sharadar.com/",
    }
    return clean


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--warehouse", default="data/sharadar/warehouse.duckdb")
    ap.add_argument("--strategy", default="config/backtest_sfa.yaml")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--hold", type=int, default=30)
    ap.add_argument("--step", type=int, default=30)
    ap.add_argument("--warmup", type=int, default=300)
    ap.add_argument("--min-names", type=int, default=20)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--top-n", type=int)
    ap.add_argument("--no-evidence", action="store_true")
    ap.add_argument("--tune", action="store_true",
                    help="evaluate one shared target-based challenger on chronological partitions")
    ap.add_argument("--tune-candidates", type=int,
                    help="override the configured number of weight combinations")
    ap.add_argument("--json-out", default="web/data/backtest-sfa.json")
    ap.add_argument(
        "--publish-private",
        help="Publish a redacted UI artifact from an existing private result without rerunning",
    )
    ap.add_argument(
        "--private-json-out", default="data/sharadar/reports/backtest-sfa-full.json"
    )
    args = ap.parse_args()
    if args.publish_private:
        result = json.loads(Path(args.publish_private).read_text(encoding="utf-8"))
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(public_artifact(result), separators=(",", ":")),
            encoding="utf-8",
        )
        print(json.dumps({"published": str(path), "run_id": result.get("run_id")}))
        return
    strategy = load_strategy(args.strategy)
    if args.top_n:
        from dataclasses import replace

        strategy = replace(strategy, universe_top_n=args.top_n)
    with SharadarWarehouse(args.warehouse, read_only=True) as warehouse:
        result = run_sfa_rolling(
            warehouse,
            load_config(),
            hold_days=args.hold,
            step_days=args.step,
            warmup_days=args.warmup,
            min_names=args.min_names,
            bootstrap=args.bootstrap,
            start=args.start,
            end=args.end,
            strategy=strategy,
            include_evidence=not args.no_evidence,
            progress=lambda row: print(json.dumps({"progress": row}), flush=True),
        )
    result["artifact"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator": "scripts/sfa_backtest.py",
    }
    if args.tune and result.get("ok"):
        result["weight_validation"] = tune_sfa_observations(
            result.get("observations", []), load_config(),
            step_days=result.get("step_days", args.step),
            candidates=args.tune_candidates,
            policy=policy_from_strategy(strategy),
            progress=lambda row: print(json.dumps({"tuning_progress": row}), flush=True),
        )
    private_path = Path(args.private_json_out)
    private_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
    path = Path(args.json_out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(public_artifact(result), separators=(",", ":")), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: result.get(k)
                for k in (
                    "ok",
                    "run_id",
                    "snapshot_id",
                    "window",
                    "cohorts",
                    "unique_stocks",
                    "by_verdict",
                    "buy_minus_avoid_pct",
                    "spread_ci90",
                    "monotonic",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
