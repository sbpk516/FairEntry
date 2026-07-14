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


# ---------------------------------------------------------------------------
# Rolling, benchmark-relative backtest
# ---------------------------------------------------------------------------
def _first_exit(dates: list[str], entry: str, hold_days: int) -> str | None:
    """First snapshot date at least `hold_days` after `entry` (nearest forward)."""
    for d in dates:
        if _days(entry, d) >= hold_days:
            return d
    return None


def run_rolling(store, cfg, hold_days: int = 30, step_days: int = 7,
                min_names: int = 20, settings=None) -> dict:
    """Rolling, benchmark-relative backtest.

    Instead of one entry->exit window, this replays MANY overlapping cohorts:
    every ~`step_days` it takes an entry date, scores the whole universe as-of
    then, and measures each name's forward return over ~`hold_days`. Returns are
    made benchmark-relative by subtracting that cohort's cross-sectional mean
    (the average of every scored name that cohort) — so we measure *stock
    selection* (did Buys beat the average stock?), not market direction.

    Aggregated across all cohorts we report, per verdict: n, mean & median
    alpha, hit-rate (alpha > 0), and mean raw return. The headline is
    `buy_minus_avoid` (Buy mean alpha − Avoid mean alpha) and `monotonic`
    (Buy >= Watch >= Avoid on mean alpha) — a healthy gate is monotonic with a
    positive spread.
    """
    settings = settings or {"margin_of_safety_pct": 15, "target_upside_pct": 30}
    dates = _dates(store)
    if len(dates) < 2:
        return {"ok": False, "reason": f"insufficient history: {len(dates)} snapshot date(s)."}
    if _days(dates[0], dates[-1]) < hold_days:
        return {"ok": False, "reason": f"history spans {_days(dates[0], dates[-1])}d; "
                f"need >= hold_days ({hold_days}d) to measure one forward window."}

    # entry dates: spaced >= step_days apart, each with a full hold window ahead
    entries, last_pick = [], None
    for d in dates:
        if _days(d, dates[-1]) < hold_days:
            break
        if last_pick is None or _days(last_pick, d) >= step_days:
            entries.append(d); last_pick = d

    medians = sector_medians(cfg, store)      # see module note on point-in-time
    secs = store.securities()
    alpha = {"Buy": [], "Watch": [], "Avoid": []}
    raw = {"Buy": [], "Watch": [], "Avoid": []}
    cohorts = []
    for entry in entries:
        exit_ = _first_exit(dates, entry, hold_days)
        if not exit_:
            continue
        rows = []
        for sec in secs:
            m = _asof_metrics(store, sec["ticker"], entry)
            if "price" not in m:
                continue
            p0 = _price_on(store, sec["ticker"], entry)
            p1 = _price_on(store, sec["ticker"], exit_)
            if not (p0 and p1 and p0 > 0):
                continue
            rec = score_ticker(cfg, sec, m, medians, settings)
            rows.append((rec["verdict"], (p1 / p0 - 1) * 100))
        if len(rows) < min_names:
            continue
        mkt = statistics.mean(r for _, r in rows)   # cross-sectional benchmark
        cohort_alpha = {"Buy": [], "Watch": [], "Avoid": []}
        for v, r in rows:
            alpha.setdefault(v, []).append(r - mkt)
            raw.setdefault(v, []).append(r)
            cohort_alpha.setdefault(v, []).append(r - mkt)
        cohorts.append({
            "entry": entry, "exit": exit_, "n": len(rows),
            "mkt_return_pct": round(mkt, 2),
            "buy_alpha_pct": round(statistics.mean(cohort_alpha["Buy"]), 2) if cohort_alpha["Buy"] else None,
            "buy_n": len(cohort_alpha["Buy"]),
        })

    if not cohorts:
        return {"ok": False, "reason": "no cohort had enough names with a full forward window."}

    def stats(vals):
        return {"n": len(vals),
                "mean_alpha_pct": round(statistics.mean(vals), 2),
                "median_alpha_pct": round(statistics.median(vals), 2),
                "hit_rate_pct": round(sum(1 for a in vals if a > 0) / len(vals) * 100, 1)}

    by_verdict = {v: {**stats(a), "mean_raw_return_pct": round(statistics.mean(raw[v]), 2)}
                  for v, a in alpha.items() if a}
    buy_a = by_verdict.get("Buy", {}).get("mean_alpha_pct")
    avoid_a = by_verdict.get("Avoid", {}).get("mean_alpha_pct")
    watch_a = by_verdict.get("Watch", {}).get("mean_alpha_pct")
    spread = round(buy_a - avoid_a, 2) if (buy_a is not None and avoid_a is not None) else None
    monotonic = None
    if None not in (buy_a, watch_a, avoid_a):
        monotonic = buy_a >= watch_a >= avoid_a

    return {"ok": True, "hold_days": hold_days, "step_days": step_days,
            "cohorts": len(cohorts), "window": [dates[0], dates[-1]],
            "by_verdict": by_verdict, "buy_minus_avoid_pct": spread,
            "monotonic": monotonic, "per_cohort": cohorts}
