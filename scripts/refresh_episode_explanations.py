"""Refresh presentation evidence from the matching private replay, without rerunning scores."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import duckdb
import ijson

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fairentry.backtest.evidence import refresh_episode_explanations


def refresh(public_path, private_path, warehouse_path):
    public_path = Path(public_path)
    public = json.loads(public_path.read_text(encoding="utf-8"))
    from fairentry.backtest.vectorbt_crosscheck import artifact_metadata
    identity = artifact_metadata(private_path)
    for field in ("run_id", "snapshot_id", "implementation_fingerprint"):
        if not public.get(field) or identity.get(field) != public[field]:
            raise ValueError(f"Replay identity mismatch: {field}")
    episodes = public["buy_return_achievement"]["episode_details"]
    wanted = {(e["ticker"], e["started"]) for e in episodes}
    roots = {}
    with Path(private_path).open("rb") as stream:
        for row in ijson.items(stream, "observations.item", use_float=True):
            key = (row.get("ticker"), row.get("entry_date"))
            if key in wanted:
                if key in roots:
                    raise ValueError(f"Ambiguous observation: {key}")
                roots[key] = row
    if set(roots) != wanted:
        raise ValueError("Private replay is missing episode roots")
    with duckdb.connect(str(warehouse_path), read_only=True) as con:
        securities = {str(r[0]): (r[1], r[2]) for r in con.execute(
            'SELECT permaticker,exchange,currency FROM sfa_tickers WHERE "table"=\'SEP\''
        ).fetchall()}
    for episode in episodes:
        root = roots[(episode["ticker"], episode["started"])]
        exchange, currency = securities.get(str(root.get("security_id")), (None, None))
        root.update(exchange=exchange, currency=currency, snapshot_id=public["snapshot_id"])
        refreshed = refresh_episode_explanations(episode, root)
        for key in ("target_failure_reasons", "market_context", "entry_provenance", "fixed_30_reason"):
            episode[key] = refreshed.get(key)
        p = episode.setdefault("entry_provenance", {})
        p.update(exchange=exchange, currency=currency, security_id=root.get("security_id"),
                 decision_date=root.get("decision_date"), raw_close=root.get("raw_close"),
                 entry_cost_bps=(root.get("execution") or {}).get("entry_cost_bps"),
                 snapshot_id=public["snapshot_id"],
                 price_basis="Source close is split-adjusted. Simulated entry adds trading costs. Return calculations also account for dividends using adjusted prices.")
        if episode.get("market_context"):
            episode["market_context"]["benchmark_label"] = (
                "SPY (S&P 500 fund)" if public.get("strategy", {}).get("benchmark") == "spy_total_return"
                else "Comparison portfolio"
            )
    from fairentry.backtest.failure_research import build_research_queue
    queue = build_research_queue(public, queue_path=public_path.parent / "target-failure-research-queue.json")
    public["failure_research_queue"] = queue["summary"]
    public_path.write_text(json.dumps(public, separators=(",", ":")), encoding="utf-8")
    print(f"Updated explanations and entry provenance for {len(episodes)} episodes; scores and returns preserved.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", default="web/data/backtest-sfa.json")
    parser.add_argument("--private", default="data/sharadar/reports/backtest-sfa-full.json")
    parser.add_argument("--warehouse", default="data/sharadar/warehouse.duckdb")
    args = parser.parse_args()
    refresh(args.public, args.private, args.warehouse)
