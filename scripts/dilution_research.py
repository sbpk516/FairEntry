#!/usr/bin/env python3
"""Stream a private SFA artifact and evaluate substantial-dilution vetoes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import ijson

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.backtest.dilution_research import run_dilution_veto_research


def _minimal(observation: dict) -> dict:
    survival = next(
        (category for category in observation.get("categories", [])
         if category.get("id") == "survival"),
        {"id": "survival", "items": []},
    )
    dilution = next(
        (item for item in survival.get("items", []) if item.get("id") == "dilution"),
        {"id": "dilution", "actual": None},
    )
    return {
        key: observation.get(key)
        for key in (
            "issuer_key", "security_id", "ticker", "entry_date", "verdict",
            "pre_dilution_verdict", "return_milestones", "terminal_event",
            "_tuning_outcome",
        )
    } | {
        "categories": [{"id": "survival", "items": [dilution]}],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact")
    parser.add_argument("--out")
    args = parser.parse_args()
    observations = []
    with Path(args.artifact).open("rb") as handle:
        for observation in ijson.items(handle, "observations.item", use_float=True):
            observations.append(_minimal(observation))
    report = run_dilution_veto_research(observations)
    output = json.dumps(report, indent=2)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
