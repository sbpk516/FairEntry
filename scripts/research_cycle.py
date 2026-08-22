#!/usr/bin/env python3
"""Run the controlled predictive-rule research report on an SFA artifact."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.backtest.failure_research import build_research_queue  # noqa: E402
from fairentry.backtest.research_cycle import load_rules, run_predictive_rule_research  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backtest", default="data/sharadar/reports/backtest-sfa-full.json")
    parser.add_argument("--rules", default="config/predictive_rules.json")
    parser.add_argument("--out", default="web/data/research-cycle.json")
    parser.add_argument("--attach", action="store_true",
                        help="also attach the aggregate report to the input JSON")
    parser.add_argument("--public-attach",
                        help="attach only the aggregate report to an existing redacted UI artifact")
    args = parser.parse_args()
    path = Path(args.backtest)
    artifact = json.loads(path.read_text(encoding="utf-8"))
    report = run_predictive_rule_research(
        artifact.get("observations") or [],
        step_days=int(artifact.get("step_days") or 30),
        rules=load_rules(args.rules),
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    queue = build_research_queue(artifact)
    if args.attach:
        artifact["research_cycle"] = report
        path.write_text(json.dumps(artifact, separators=(",", ":")), encoding="utf-8")
    if args.public_attach:
        public_path = Path(args.public_attach)
        public = json.loads(public_path.read_text(encoding="utf-8"))
        public["research_cycle"] = report
        public["failure_research_queue"] = queue["summary"]
        public_path.write_text(json.dumps(public, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"out": str(out), "ok": report.get("ok"),
                      "selected_rule": (report.get("selected_rule") or {}).get("id"),
                      "promotion_eligible": report.get("promotion_eligible"),
                      "decision": report.get("decision")}, indent=2))


if __name__ == "__main__":
    main()
