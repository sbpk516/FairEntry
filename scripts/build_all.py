#!/usr/bin/env python3
"""End-to-end: (optionally refresh) -> screen -> score -> export board.json.

  python scripts/build_all.py            # score from existing store, export
  python scripts/build_all.py --refresh  # refresh data first
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.config import load_config
from fairentry.store import Store
from fairentry.catalog.refresh import refresh
from fairentry.pipeline.export import build_board, write_board
from fairentry.pipeline.emerging import build_emerging_candidates
from fairentry.screeners import REGISTRY as SCREENERS
from fairentry.backtest.universe import deduplicate_issuers
from fairentry.scoring.engine import score_ticker, sector_medians


def _candidates(cfg, store, cap):
    """Choose enrichment names without excluding possible aligned Buys.

    Every screened name already passing fundamentals, fair value, and vetoes is
    prioritized for the EMA/OBV fetch. Remaining capacity is filled by market
    cap, preserving the previous bounded enrichment behavior.
    """
    cand = set()
    for mod in SCREENERS.values():
        for s in store.active_securities():
            ok, _ = mod.passes(store.metrics_for(s["ticker"]))
            if ok:
                cand.add(s["ticker"])
    def capval(t):
        v = store.metrics_for(t).get("market_cap", {}).get("value")
        return v if isinstance(v, (int, float)) else 0
    representatives, _ = deduplicate_issuers([
        {"sec": security, "metrics": store.metrics_for(security["ticker"])}
        for security in store.active_securities() if security["ticker"] in cand
    ])
    securities = [item["sec"] for item in representatives]
    medians = sector_medians(cfg, store)
    defaults = {
        key: value.get("default")
        for key, value in (cfg.defaults.get("settings") or {}).items()
        if isinstance(value, dict) and "default" in value
    }
    prequalified = []
    for security in securities:
        record = score_ticker(
            cfg, security, store.metrics_for(security["ticker"]), medians, defaults
        )
        checks = (record.get("buy_entry_alignment") or {}).get("checks") or {}
        if not record.get("vetoes") and checks.get("fundamentals") and checks.get("valuation"):
            prequalified.append(security["ticker"])

    ordered = sorted((security["ticker"] for security in securities), key=capval, reverse=True)
    if not cap:
        return ordered
    priority = sorted(prequalified, key=capval, reverse=True)
    remaining = [ticker for ticker in ordered if ticker not in set(priority)]
    return priority + remaining[:max(0, cap - len(priority))]


def _moving_average_candidates(cfg, store):
    """Cover every screened name that could appear in the SMA-zone view."""
    candidates = _candidates(cfg, store, 0)
    securities = {s["ticker"]: s for s in store.active_securities()}
    medians = sector_medians(cfg, store)
    defaults = {
        key: value.get("default")
        for key, value in (cfg.defaults.get("settings") or {}).items()
        if isinstance(value, dict) and "default" in value
    }
    selected = []
    for ticker in candidates:
        security = securities[ticker]
        record = score_ticker(cfg, security, store.metrics_for(ticker), medians, defaults)
        checks = (record.get("buy_entry_alignment") or {}).get("checks") or {}
        if not record.get("vetoes") and checks.get("fundamentals"):
            selected.append(ticker)
    return selected


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="refresh universe (Finviz) before scoring")
    ap.add_argument("--reason", action="store_true", help="run the LLM reasoning layer on the shortlist")
    ap.add_argument("--enrich-cap", type=int, default=60,
                    help="how many top candidates to enrich with SEC forensic data (0=all)")
    args = ap.parse_args()
    cfg = load_config()
    with Store() as store:
        if args.refresh:
            print("Refreshing universe (Finviz)…")
            refresh(cfg, store)
        if args.enrich_cap != 0 or args.refresh:
            cand = _candidates(cfg, store, args.enrich_cap)
            technical = sorted(set(cand) | set(_moving_average_candidates(cfg, store)))
            print(f"Enriching {len(cand)} SEC candidates and {len(technical)} "
                  "moving-average candidates (cached)…")
            refresh(cfg, store, sec_tickers=cand, wma_tickers=technical)
        from fairentry.tracking import refresh_tracking_quotes
        quote_refresh = refresh_tracking_quotes(store)
        print(f"Tracking quotes: {quote_refresh['refreshed']}/{quote_refresh['requested']} refreshed "
              "from independent source")
        print("Screening + scoring…" + (" + reasoning shortlist" if args.reason else ""))
        board = build_board(cfg, store, reason=args.reason)
        print("Computing outside-universe emerging research candidates…")
        emerging = build_emerging_candidates(cfg, store, official_board=board)
        board["emerging_candidates"] = emerging["stocks"]
        board["meta"]["emerging_candidates"] = emerging["meta"]
        from fairentry.tracking import record as track_record
        track = track_record(store, board)
        board["meta"]["tracking"] = {"alerts": len(track["alerts"]),
                                     "opened": track["opened"], "closed": track["closed"],
                                     "signals": track.get("signals", 0)}
        path = write_board(board)
        from fairentry.alerts import email_trading_alerts, email_wma_alerts
        wma_alerts = board["meta"].get("moving_average_zone_candidates", [])
        try:
            emailed = email_wma_alerts(wma_alerts)
        except Exception as exc:
            emailed = False
            print(f"SMA-zone email failed: {exc}")
        try:
            trading_emailed = email_trading_alerts(track.get("new_buys", []),
                                                   track.get("near_30", []),
                                                   track.get("exit_reviews", []))
        except Exception as exc:
            trading_emailed = {"new_buys": False, "near_30": False,
                               "exit_reviews": False}
            print(f"Trading alert email failed: {exc}")
    if board["meta"].get("reasoning"):
        print("Reasoning:", board["meta"]["reasoning"])
    print(f"Tracking: {track['tracked']} tracked, {track.get('signals', 0)} signal snapshots, "
          f"{track['opened']} paper positions opened, "
          f"{track['closed']} closed, {len(track['alerts'])} degradation alert(s)")
    for a in track["alerts"][:8]:
        print(f"  ALERT {a['ticker']} ({a['strategy']}): {a['from']} -> {a['to']} "
              f"(score {a['score_from']} -> {a['score_to']})")
    print(f"SMA-zone candidates: {len(wma_alerts)}" + (" (email sent)" if emailed else ""))
    print(f"New Buy alerts: {len(track.get('new_buys', []))}" +
          (" (email sent)" if trading_emailed["new_buys"] else ""))
    print(f"Near +30% alerts: {len(track.get('near_30', []))}" +
          (" (email sent)" if trading_emailed["near_30"] else ""))
    print(f"Exit/review alerts: {len(track.get('exit_reviews', []))}" +
          (" (email sent)" if trading_emailed["exit_reviews"] else ""))
    from collections import Counter
    v = Counter(s["verdict"] if "verdict" in s else "" for s in [])  # verdict is recomputed in UI
    print(f"Exported {board['meta']['count']} stocks -> {path}")
    emerging_meta = board["meta"].get("emerging_candidates", {})
    print(f"Emerging research: {emerging_meta.get('candidate_count', 0)} active, "
          f"{(emerging_meta.get('variant_counts') or {}).get('selective', 0)} selective; "
          "research only; historical advantage not proven")
    for stock in board.get("emerging_candidates", []):
        evidence = stock.get("emerging_candidate", {})
        liquidity = (evidence.get("summary_values") or {}).get(
            "average_daily_dollar_volume")
        print(f"  EMERGING {stock['ticker']}: {evidence.get('status')} · "
              f"shadow {evidence.get('shadow_model_verdict')} "
              f"{evidence.get('shadow_score', 0):.1f}/100 · "
              f"${(liquidity or 0) / 1_000_000:.1f}M avg daily dollar volume")
    print(f"Sectors: {board['meta']['sectors']}")


if __name__ == "__main__":
    main()
