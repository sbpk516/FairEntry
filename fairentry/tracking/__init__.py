"""Recommendation tracking — persist each run's verdicts and follow them over
time (first_seen / last_seen), the basis for a paper portfolio + degradation
alerts. Reads the exported board; writes the recommendations table.
"""
from __future__ import annotations

from datetime import datetime, timezone


def record(store, board: dict):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n = 0
    for s in board.get("stocks", []):
        # verdict is recomputed in the UI, but the pipeline stored score_results;
        # here we track the pipeline's own verdict from the store.
        for r in store.con.execute(
                "SELECT strategy, verdict, preliminary FROM score_results WHERE ticker=?",
                (s["ticker"],)):
            store.con.execute(
                "INSERT INTO recommendations(ticker,strategy,verdict,action,score,first_seen,last_seen) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(ticker,strategy) DO UPDATE SET "
                "verdict=excluded.verdict, score=excluded.score, last_seen=excluded.last_seen",
                (s["ticker"], r["strategy"], r["verdict"], s["action"]["action"],
                 r["preliminary"], now, now))
            n += 1
    store.commit()
    return n
