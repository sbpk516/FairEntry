#!/usr/bin/env python3
"""Backtest the scoring model over accumulated point-in-time history.

  python scripts/backtest.py
  python scripts/backtest.py --rolling
  python scripts/backtest.py --db data/backtest.db --rolling --hold 30 --step 7

Seed a history first with:
  python scripts/seed_backtest.py
"""
import argparse
import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.backtest.harness import run, run_rolling
from fairentry.backtest.strategy import load_strategy
from fairentry.config import load_config
from fairentry.store import Store
from fairentry.store.db import DEFAULT_DB


def _markdown_rolling(res):
    if not res["ok"]:
        return f"### Backtest not ready\n\n{res['reason']}\n"
    w = res["window"]
    scope = "screened candidates" if res.get("screened_only", True) else "full universe"
    lines = [
        "### Rolling backtest - Buy-filter alpha",
        f"`{w[0]} -> {w[1]}` - **{res['cohorts']} cohorts** - "
        f"hold {res['hold_days']}d / step {res['step_days']}d - {scope} - "
        f"warmup {res.get('warmup_days', 0)}d",
        "",
        "| verdict | n | mean alpha | median alpha | hit-rate | raw ret |",
        "|---|--:|--:|--:|--:|--:|",
    ]
    for verdict in ("Buy", "Watch", "Avoid"):
        row = res["by_verdict"].get(verdict)
        if row:
            lines.append(
                f"| {verdict} | {row['n']} | {row['mean_alpha_pct']:+.2f}% | "
                f"{row['median_alpha_pct']:+.2f}% | {row['hit_rate_pct']:.1f}% | "
                f"{row['mean_raw_return_pct']:+.2f}% |"
            )
    spread, mono = res.get("buy_minus_avoid_pct"), res.get("monotonic")
    ci, sig = res.get("spread_ci90"), res.get("significant")
    ci_txt = f" (90% CI {ci[0]:+.2f}%..{ci[1]:+.2f}%)" if ci else ""
    tag = "working" if (mono and sig) else "check" if mono else "not monotonic"
    lines += [
        "",
        f"**Buy - Avoid alpha spread:** {'n/a' if spread is None else f'{spread:+.2f}%'}{ci_txt} - "
        f"**monotonic:** {mono} - **CI excludes 0:** {sig} - {tag}",
        "",
        "> alpha = each name's return minus its cohort's cross-sectional mean "
        "(selection, not market). CI = block-bootstrap over cohorts (respects overlap). "
        "Population = screener-passing names as-of (matches the live board). "
        "Absolute alpha is optimistic (survivorship: universe = today's survivors).",
    ]
    ts = res.get("target_summary") or {}
    if ts:
        rate = "n/a" if ts.get("hit_rate_pct") is None else f"{ts['hit_rate_pct']:.1f}%"
        lines += ["", "### Frozen target evidence", "",
                  f"Primary model: **{ts.get('primary_model')}** · Buy observations: **{ts.get('buy_recommendations', 0)}** · "
                  f"evaluable: **{ts.get('evaluable', 0)}** · reached: **{ts.get('reached', 0)}** · hit rate: **{rate}** · "
                  f"median days: **{ts.get('median_days_to_target') or 'n/a'}**",
                  "", "> Active or incomplete observations are not counted as failures. Targets are frozen at entry."]
    return "\n".join(lines) + "\n"


def _write_json(res, path):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(res)
    payload["artifact"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_commit": os.getenv("GITHUB_SHA") or None,
        "generator": "scripts/backtest.py",
    }
    observations = payload.get("observations") or []
    if observations:
        evidence_dir = p.parent / "backtest-evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        by_ticker = {}
        compact = []
        for o in observations:
            by_ticker.setdefault(o["ticker"], []).append(o)
            primary = payload.get("strategy", {}).get("primary_target", "practical")
            target = o.get("outcome", {}).get("targets", {}).get(primary, {})
            compact.append({k: o.get(k) for k in ("observation_id", "ticker", "company", "sector",
                                                   "entry_date", "entry_price", "verdict", "score",
                                                   "strategy_key", "strategy_memberships", "preset_name") } | {
                "quality_grade": o.get("data_quality", {}).get("grade"),
                "target": target,
                "horizons": o.get("outcome", {}).get("horizons", {}),
                "max_gain_pct": o.get("outcome", {}).get("max_gain_pct"),
                "max_drawdown_pct": o.get("outcome", {}).get("max_drawdown_pct"),
                "detail_path": f"data/backtest-evidence/{o['ticker']}.json",
            })
        for ticker, rows in by_ticker.items():
            (evidence_dir / f"{ticker}.json").write_text(
                json.dumps({"ticker": ticker, "observations": rows}, separators=(",", ":"), ensure_ascii=False),
                encoding="utf-8")
        payload["observations"] = compact
        payload["evidence_delivery"] = {"mode": "per_ticker_lazy", "directory": "data/backtest-evidence"}
    p.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")


def _print_signal_backtest(res):
    print(f"Prospective signal backtest ({res['signals']} recorded Buy/Watch signals)")
    for horizon, data in res["horizons"].items():
        print(f"\nHorizon {horizon}: {data['signals']} matured signals")
        for verdict, row in data["by_verdict"].items():
            print(
                f"  {verdict:6} n={row['n']:4} avg {row['avg_return_pct']:+.2f}% "
                f"median {row['median_return_pct']:+.2f}% hit {row['hit_rate_pct']}%"
            )
        for strategy, row in data["by_strategy"].items():
            print(
                f"  {strategy:14} n={row['n']:4} avg {row['avg_return_pct']:+.2f}% "
                f"hit {row['hit_rate_pct']}%"
            )


def _print_rolling(res):
    if not res["ok"]:
        print("Backtest not ready:", res["reason"])
        if "signals" in res:
            print(f"  recorded signals: {res['signals']}")
        return
    if res.get("mode") == "signal_events":
        _print_signal_backtest(res)
        print(json.dumps(res, indent=1))
        return

    start, end = res["window"]
    print(
        f"Rolling backtest {start} -> {end}  -  {res['cohorts']} cohorts  -  "
        f"hold {res['hold_days']}d / step {res['step_days']}d"
    )
    print(f"{'verdict':7} {'n':>6} {'mean alpha':>11} {'median':>9} {'hit-rate':>9} {'raw ret':>9}")
    for verdict in ("Buy", "Watch", "Avoid"):
        row = res["by_verdict"].get(verdict)
        if row:
            print(
                f"{verdict:7} {row['n']:>6} {row['mean_alpha_pct']:>+10.2f}% "
                f"{row['median_alpha_pct']:>+8.2f}% {row['hit_rate_pct']:>8.1f}% "
                f"{row['mean_raw_return_pct']:>+8.2f}%"
            )
    spread, mono = res.get("buy_minus_avoid_pct"), res.get("monotonic")
    ci, sig = res.get("spread_ci90"), res.get("significant")
    ci_txt = f"  90% CI [{ci[0]:+.2f}%, {ci[1]:+.2f}%]" if ci else ""
    scope = "screened candidates" if res.get("screened_only", True) else "full universe"
    spread_txt = "n/a" if spread is None else f"{spread:+.2f}%"
    print(f"\nBuy - Avoid alpha spread: {spread_txt}{ci_txt}   monotonic: {mono}   CI>0: {sig}")
    print(f"population: {scope} - warmup {res.get('warmup_days', 0)}d")
    print("alpha = return minus cohort mean (selection, not market). Absolute alpha optimistic (survivorship).")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB), help="store to backtest, e.g. data/backtest.db")
    parser.add_argument("--rolling", action="store_true", help="rolling benchmark-relative alpha backtest")
    parser.add_argument("--hold", type=int, default=30, help="forward holding window in days")
    parser.add_argument("--step", type=int, default=7, help="days between cohort entries")
    parser.add_argument("--json", action="store_true", help="also dump the full result JSON")
    parser.add_argument("--json-out", default=None, help="write the full result JSON to this path")
    parser.add_argument("--md-out", default=None, help="write a markdown report to this path")
    parser.add_argument("--strategy", default=None, help="versioned backtest YAML (default config/backtest.yaml)")
    parser.add_argument("--no-evidence", action="store_true", help="omit per-stock evidence for a small alpha-only run")
    parser.add_argument("--target-model", choices=("practical",),
                        help="primary frozen target model for this run")
    parser.add_argument("--target-expiry", type=int, help="target expiry in calendar days")
    parser.add_argument("--horizons", help="comma-separated forward horizons, e.g. 30,60,90,180,365")
    parser.add_argument("--quality-mode", choices=("strict", "mostly_point_in_time", "experimental"),
                        help="label/policy recorded in the versioned strategy")
    parser.add_argument("--prospective-db", default=None,
                        help="optional live store whose signal_events are reported separately")
    args = parser.parse_args()

    cfg = load_config()
    strategy = load_strategy(args.strategy)
    overrides = {}
    if args.target_model: overrides["primary_target"] = args.target_model
    if args.target_expiry: overrides["target_expiry_days"] = args.target_expiry
    if args.horizons: overrides["horizons_days"] = tuple(int(x) for x in args.horizons.split(","))
    if args.quality_mode: overrides["data_quality_mode"] = args.quality_mode
    if overrides: strategy = replace(strategy, **overrides)
    with Store(args.db) as store:
        res = run_rolling(store, cfg, hold_days=args.hold, step_days=args.step,
                          strategy=strategy, include_evidence=not args.no_evidence) if args.rolling else run(store, cfg)
    if args.rolling and args.prospective_db:
        with Store(args.prospective_db) as prospective_store:
            prospective = run(prospective_store, cfg)
        res["prospective_validation"] = prospective
        res["survivorship_control"] = {
            "seeded_universe_biased": True,
            "prospective_store": str(args.prospective_db),
            "prospective_signal_events": prospective.get("signals", 0),
            "status": "matured" if prospective.get("ok") else "maturing",
            "reason": prospective.get("reason"),
        }

    if args.rolling:
        _print_rolling(res)
    elif not res["ok"]:
        print("Backtest not ready:", res["reason"])
        if "signals" in res:
            print(f"  recorded signals: {res['signals']}")
    elif res.get("mode") == "signal_events":
        _print_signal_backtest(res)
    else:
        print(f"Backtest {res['entry']} -> {res['exit']} ({res['span_days']}d)")
        for verdict, row in res["by_verdict"].items():
            print(
                f"  {verdict:6} n={row['n']:4}  avg forward {row['avg_return_pct']:+.2f}%  "
                f"hit-rate {row['hit_rate_pct']}%"
            )

    if args.md_out and args.rolling:
        with open(args.md_out, "a", encoding="utf-8") as fh:
            fh.write(_markdown_rolling(res))
    if args.json_out:
        _write_json(res, args.json_out)
    if args.json:
        print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
