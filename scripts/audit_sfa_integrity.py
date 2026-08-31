#!/usr/bin/env python3
"""Fail-closed integrity audit for a completed private SFA replay."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import ijson

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.backtest.dilution_research import dilution_yoy_pct


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact")
    parser.add_argument("--public", default="web/data/backtest-sfa.json")
    parser.add_argument("--dilution-threshold", type=float, default=10.0)
    args = parser.parse_args()

    public = json.loads(Path(args.public).read_text(encoding="utf-8"))
    identity = public.get("ticker_identity_control") or {}
    dilution_report = public.get("dilution_veto_research") or {}
    invalid_identity = []
    invalid_dilution_buy = []
    dilution_vetoes = 0
    clgx_buy_dates = []
    observations = 0

    with Path(args.artifact).open("rb") as handle:
        for row in ijson.items(handle, "observations.item", use_float=True):
            observations += 1
            ticker_identity = row.get("ticker_identity") or {}
            valid_from = ticker_identity.get("ticker_valid_from")
            if valid_from and str(row.get("decision_date") or "")[:10] < str(valid_from)[:10]:
                invalid_identity.append({
                    "ticker": row.get("ticker"),
                    "decision_date": row.get("decision_date"),
                    "ticker_valid_from": valid_from,
                })
            dilution = dilution_yoy_pct(row)
            if row.get("verdict") == "Buy" and dilution is not None and dilution > args.dilution_threshold:
                invalid_dilution_buy.append({
                    "ticker": row.get("ticker"),
                    "decision_date": row.get("decision_date"),
                    "dilution_yoy_pct": dilution,
                })
            if any(veto.get("id") == "substantial_dilution" for veto in row.get("vetoes", [])):
                dilution_vetoes += 1
            if row.get("ticker") == "CLGX" and row.get("verdict") == "Buy":
                clgx_buy_dates.append(row.get("entry_date"))

    errors = []
    if identity.get("policy") != "strict_point_in_time":
        errors.append("strict point-in-time ticker policy is not recorded")
    if identity.get("remaining_invalid_observations") != 0 or invalid_identity:
        errors.append(f"{len(invalid_identity)} invalid ticker observations remain")
    if dilution_report.get("production_effect") != "tested_hard_veto":
        errors.append("tested dilution hard-veto contract is not recorded")
    if dilution_report.get("selected_threshold_pct") != args.dilution_threshold:
        errors.append("published dilution threshold does not match the audit threshold")
    if invalid_dilution_buy:
        errors.append(f"{len(invalid_dilution_buy)} substantially diluted Buy observations remain")
    if clgx_buy_dates and min(clgx_buy_dates) < "2010-05-28":
        errors.append("CLGX has a Buy before its recorded ticker-effective date")

    result = {
        "ok": not errors,
        "run_id": public.get("run_id"),
        "observations_scanned": observations,
        "ticker_identity": {
            "excluded_universe_rows": identity.get("excluded_universe_rows"),
            "excluded_unique_tickers": identity.get("excluded_unique_tickers"),
            "remaining_violations": len(invalid_identity),
        },
        "dilution": {
            "threshold_pct": args.dilution_threshold,
            "vetoed_observations": dilution_vetoes,
            "remaining_buy_violations": len(invalid_dilution_buy),
        },
        "clgx_earliest_buy": min(clgx_buy_dates) if clgx_buy_dates else None,
        "errors": errors,
    }
    print(json.dumps(result, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
