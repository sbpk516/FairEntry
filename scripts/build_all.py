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
from fairentry.screeners import REGISTRY as SCREENERS


def _candidates(cfg, store, cap):
    """Screened tickers (union), top-`cap` by market cap — the set worth the
    expensive SEC/yfinance enrichment."""
    cand = set()
    for mod in SCREENERS.values():
        for s in store.securities():
            ok, _ = mod.passes(store.metrics_for(s["ticker"]))
            if ok:
                cand.add(s["ticker"])
    def capval(t):
        v = store.metrics_for(t).get("market_cap", {}).get("value")
        return v if isinstance(v, (int, float)) else 0
    return sorted(cand, key=capval, reverse=True)[:cap] if cap else sorted(cand)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="refresh universe (Finviz) before scoring")
    ap.add_argument("--reason", action="store_true", help="run the LLM reasoning layer on the shortlist")
    ap.add_argument("--enrich-cap", type=int, default=60,
                    help="how many top candidates to enrich with SEC forensic + 200wma (0=all)")
    args = ap.parse_args()
    cfg = load_config()
    with Store() as store:
        if args.refresh:
            print("Refreshing universe (Finviz)…")
            refresh(cfg, store)
        if args.enrich_cap != 0 or args.refresh:
            cand = _candidates(cfg, store, args.enrich_cap)
            print(f"Enriching {len(cand)} candidates (SEC forensic + 200wma; cached)…")
            refresh(cfg, store, sec_tickers=cand, wma_tickers=cand)
        print("Screening + scoring…" + (" + reasoning shortlist" if args.reason else ""))
        board = build_board(cfg, store, reason=args.reason)
        from fairentry.tracking import record as track_record
        track = track_record(store, board)
        board["meta"]["tracking"] = {"alerts": len(track["alerts"]),
                                     "opened": track["opened"], "closed": track["closed"]}
        path = write_board(board)
    if board["meta"].get("reasoning"):
        print("Reasoning:", board["meta"]["reasoning"])
    print(f"Tracking: {track['tracked']} tracked, {track['opened']} paper positions opened, "
          f"{track['closed']} closed, {len(track['alerts'])} degradation alert(s)")
    for a in track["alerts"][:8]:
        print(f"  ALERT {a['ticker']} ({a['strategy']}): {a['from']} -> {a['to']} "
              f"(score {a['score_from']} -> {a['score_to']})")
    from collections import Counter
    v = Counter(s["verdict"] if "verdict" in s else "" for s in [])  # verdict is recomputed in UI
    print(f"Exported {board['meta']['count']} stocks -> {path}")
    print(f"Sectors: {board['meta']['sectors']}")


if __name__ == "__main__":
    main()
