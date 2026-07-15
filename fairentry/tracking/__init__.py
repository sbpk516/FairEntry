"""Recommendation tracking + paper portfolio + degradation alerts.

Each run: persist every name's verdict (first_seen / last_seen), detect when a
tracked name's verdict worsens or its score drops materially (alert), and run a
simple paper portfolio (open on Buy, close on Avoid). Reads the pipeline's
score_results (deterministic verdict — reproducible), writes the recommendations
+ paper_portfolio tables.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

_RANK = {"Buy": 0, "Watch": 1, "Avoid": 2}
_DROP_ALERT = 8.0   # score drop that counts as degradation


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(store, board: dict) -> dict:
    now = _now()
    signal_date = now[:10]
    alerts, opened, closed, signals = [], 0, 0, 0
    price_by = {s["ticker"]: s.get("price") for s in board.get("stocks", [])}
    action_by = {s["ticker"]: s.get("action", {}).get("action", "") for s in board.get("stocks", [])}
    board_by = {s["ticker"]: s for s in board.get("stocks", [])}

    for tkr in price_by:
        for r in store.con.execute(
                "SELECT strategy, verdict, preliminary FROM score_results WHERE ticker=?", (tkr,)):
            strat, verdict, score = r["strategy"], r["verdict"], r["preliminary"]
            stock = board_by.get(tkr, {})
            store.con.execute(
                "INSERT INTO signal_events("
                "signal_date,run_at,ticker,strategy,company,sector,country,price,score,verdict,action,"
                "labels_json,gates_json,vetoes_json,trace_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(signal_date,ticker,strategy) DO UPDATE SET "
                "run_at=excluded.run_at, company=excluded.company, sector=excluded.sector, "
                "country=excluded.country, price=excluded.price, score=excluded.score, "
                "verdict=excluded.verdict, action=excluded.action, labels_json=excluded.labels_json, "
                "gates_json=excluded.gates_json, vetoes_json=excluded.vetoes_json, trace_json=excluded.trace_json",
                (signal_date, now, tkr, strat, stock.get("company"), stock.get("sector"),
                 stock.get("country"), price_by.get(tkr), score, verdict, action_by.get(tkr, ""),
                 json.dumps(stock.get("labels", [])), json.dumps(stock.get("soft_gates", [])),
                 json.dumps(stock.get("vetoes", [])), json.dumps(stock)))
            signals += 1
            prev = store.con.execute(
                "SELECT verdict, score FROM recommendations WHERE ticker=? AND strategy=?",
                (tkr, strat)).fetchone()
            if prev:
                worse = _RANK.get(verdict, 1) > _RANK.get(prev["verdict"], 1)
                dropped = (prev["score"] or 0) - (score or 0) >= _DROP_ALERT
                if worse or dropped:
                    alerts.append({"ticker": tkr, "strategy": strat,
                                   "from": prev["verdict"], "to": verdict,
                                   "score_from": round(prev["score"] or 0, 1),
                                   "score_to": round(score or 0, 1)})
                store.con.execute(
                    "UPDATE recommendations SET verdict=?, action=?, score=?, last_seen=? "
                    "WHERE ticker=? AND strategy=?",
                    (verdict, action_by.get(tkr, ""), score, now, tkr, strat))
            else:
                store.con.execute(
                    "INSERT INTO recommendations(ticker,strategy,verdict,action,score,first_seen,last_seen) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (tkr, strat, verdict, action_by.get(tkr, ""), score, now, now))

            # paper portfolio: open on first Buy, close on Avoid
            held = store.con.execute(
                "SELECT status FROM paper_portfolio WHERE ticker=?", (tkr,)).fetchone()
            if verdict == "Buy" and not held:
                store.con.execute(
                    "INSERT INTO paper_portfolio(ticker,entered_at,entry_price,strategy,status,notes) "
                    "VALUES(?,?,?,?,?,?)", (tkr, now, price_by[tkr], strat, "open", "opened on Buy"))
                opened += 1
            elif verdict == "Avoid" and held and held["status"] == "open":
                store.con.execute(
                    "UPDATE paper_portfolio SET status=?, notes=? WHERE ticker=?",
                    ("closed", "closed on Avoid", tkr))
                closed += 1
            break   # one strategy row per ticker is enough for tracking
    store.commit()
    return {"tracked": len(price_by), "signals": signals, "alerts": alerts, "opened": opened, "closed": closed}


def open_positions(store) -> list[dict]:
    return [dict(r) for r in store.con.execute(
        "SELECT * FROM paper_portfolio WHERE status='open' ORDER BY entered_at DESC")]
