"""Backtest harness — replay the scoring model over point-in-time history and
measure whether Buys actually outperformed.

Uses metrics_history (append-only). For an entry snapshot date, it reconstructs
each ticker's metrics as-of that date, scores them, then measures the forward
return to a later snapshot — bucketed by verdict. With little history it reports
"insufficient history" gracefully; results sharpen as daily runs accumulate.

Note: sector medians use the current snapshot (a mild anachronism); acceptable
for a first harness, refine once history is deep.
"""
from __future__ import annotations

import statistics
from datetime import date

from ..scoring.engine import sector_medians, score_ticker


def _dates(store) -> list[str]:
    return [r["d"] for r in store.con.execute(
        "SELECT DISTINCT substr(fetched_at,1,10) d FROM metrics_history "
        "WHERE field_id='price' ORDER BY d")]


def _asof_metrics(store, ticker, asof) -> dict:
    out = {}
    for r in store.con.execute(
            "SELECT field_id, value_num, value_text, source, MAX(fetched_at) fa "
            "FROM metrics_history WHERE ticker=? AND substr(fetched_at,1,10)<=? "
            "GROUP BY field_id", (ticker, asof)):
        out[r["field_id"]] = {
            "value": r["value_num"] if r["value_num"] is not None else r["value_text"],
            "source": r["source"], "fetched_at": r["fa"]}
    return out


def _price_on(store, ticker, asof):
    r = store.con.execute(
        "SELECT value_num FROM metrics_history WHERE ticker=? AND field_id='price' "
        "AND substr(fetched_at,1,10)<=? ORDER BY fetched_at DESC LIMIT 1", (ticker, asof)).fetchone()
    return r["value_num"] if r else None


def _days(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def run(store, cfg, min_days: int = 14, settings=None) -> dict:
    settings = settings or {"margin_of_safety_pct": 15, "target_upside_pct": 30}
    dates = _dates(store)
    if len(dates) < 2:
        return {"ok": False, "reason": f"insufficient history: {len(dates)} snapshot date(s). "
                "Backtest populates as the daily pipeline runs."}
    entry, exit_ = dates[0], dates[-1]
    span = _days(entry, exit_)
    if span < min_days:
        return {"ok": False, "entry": entry, "exit": exit_, "span_days": span,
                "reason": f"only {span}d of history; need >= {min_days}d for a meaningful read. "
                "It will be ready as daily snapshots accumulate."}

    medians = sector_medians(cfg, store)  # current medians (see module note)
    buckets: dict[str, list[float]] = {"Buy": [], "Watch": [], "Avoid": []}
    for sec in store.securities():
        m = _asof_metrics(store, sec["ticker"], entry)
        if "price" not in m:
            continue
        rec = score_ticker(cfg, sec, m, medians, settings)
        p0, p1 = _price_on(store, sec["ticker"], entry), _price_on(store, sec["ticker"], exit_)
        if p0 and p1 and p0 > 0:
            buckets.setdefault(rec["verdict"], []).append((p1 / p0 - 1) * 100)

    summary = {}
    for v, rets in buckets.items():
        if rets:
            summary[v] = {"n": len(rets), "avg_return_pct": round(statistics.mean(rets), 2),
                          "hit_rate_pct": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1)}
    return {"ok": True, "entry": entry, "exit": exit_, "span_days": span, "by_verdict": summary}
