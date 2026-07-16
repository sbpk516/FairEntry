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

import random
import statistics
from datetime import date, timedelta

from ..scoring.engine import sector_medians, medians_from, score_ticker
from ..screeners import REGISTRY as SCREENERS


def passes_screen(metrics: dict) -> bool:
    """True if the name passes ANY strategy screener as-of these metrics — mirrors
    build_board, which only scores/shows screened names. Backtesting the full
    universe would score names the live board never displays."""
    for mod in SCREENERS.values():
        try:
            if mod.passes(metrics)[0]:
                return True
        except Exception:
            continue
    return False


def _block_bootstrap_spread(per_obs, cohorts, B=1000, seed=42):
    """Block bootstrap the Buy−Avoid spread by RESAMPLING WHOLE COHORTS (blocks),
    which respects the heavy overlap between cohorts — so the CI reflects the true
    (much smaller) independent sample, not the inflated observation count.
    per_obs: {cohort: [(verdict, alpha)]}. Returns (lo, hi) 90% CI or None."""
    if len(cohorts) < 4:
        return None
    rng = random.Random(seed)
    spreads = []
    for _ in range(B):
        buys, avoids = [], []
        for _ in cohorts:
            c = rng.choice(cohorts)
            for v, a in per_obs[c]:
                (buys if v == "Buy" else avoids if v == "Avoid" else []).append(a)
        if buys and avoids:
            spreads.append(statistics.mean(buys) - statistics.mean(avoids))
    if len(spreads) < B // 2:
        return None
    spreads.sort()
    lo = spreads[int(0.05 * len(spreads))]
    hi = spreads[int(0.95 * len(spreads)) - 1]
    return round(lo, 2), round(hi, 2)

DEFAULT_HORIZONS = {"1w": 7, "1m": 30, "3m": 90, "6m": 180, "12m": 365}


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


def _price_on_or_after(store, ticker, target_date):
    r = store.con.execute(
        "SELECT value_num, substr(fetched_at,1,10) d FROM metrics_history "
        "WHERE ticker=? AND field_id='price' AND substr(fetched_at,1,10)>=? "
        "ORDER BY fetched_at ASC LIMIT 1", (ticker, target_date)).fetchone()
    return (r["value_num"], r["d"]) if r else (None, None)


def _signal_count(store):
    try:
        r = store.con.execute("SELECT COUNT(*) n FROM signal_events").fetchone()
        return r["n"] if r else 0
    except Exception:
        return 0


def _days(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _summarize_returns(rets):
    return {"n": len(rets),
            "avg_return_pct": round(statistics.mean(rets), 2),
            "median_return_pct": round(statistics.median(rets), 2),
            "hit_rate_pct": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1)}


def _run_signal_backtest(store, horizons):
    rows = [dict(r) for r in store.con.execute(
        "SELECT * FROM signal_events WHERE verdict IN ('Buy','Watch') "
        "ORDER BY signal_date, ticker, strategy")]
    if not rows:
        return {"ok": False, "reason": "no Buy/Watch signal events recorded yet. "
                "Run the daily pipeline to start the prospective ledger."}

    matured: dict[str, list[dict]] = {h: [] for h in horizons}
    for row in rows:
        p0 = row.get("price")
        if not p0 or p0 <= 0:
            continue
        start = date.fromisoformat(row["signal_date"])
        for h, days in horizons.items():
            target = (start + timedelta(days=days)).isoformat()
            p1, exit_date = _price_on_or_after(store, row["ticker"], target)
            if p1 and p1 > 0:
                rec = dict(row)
                rec["exit_date"] = exit_date
                rec["return_pct"] = (p1 / p0 - 1) * 100
                matured[h].append(rec)

    by_horizon = {}
    for h, recs in matured.items():
        if not recs:
            continue
        by_verdict = {}
        by_strategy = {}
        for key, bucket in (("verdict", by_verdict), ("strategy", by_strategy)):
            vals = {}
            for r in recs:
                vals.setdefault(r[key], []).append(r["return_pct"])
            bucket.update({k: _summarize_returns(v) for k, v in vals.items() if v})
        by_horizon[h] = {"signals": len(recs), "by_verdict": by_verdict,
                         "by_strategy": by_strategy,
                         "top": sorted(
                             [{"ticker": r["ticker"], "strategy": r["strategy"],
                               "verdict": r["verdict"], "return_pct": round(r["return_pct"], 2)}
                              for r in recs], key=lambda x: -x["return_pct"])[:10],
                         "bottom": sorted(
                             [{"ticker": r["ticker"], "strategy": r["strategy"],
                               "verdict": r["verdict"], "return_pct": round(r["return_pct"], 2)}
                              for r in recs], key=lambda x: x["return_pct"])[:10]}

    if not by_horizon:
        earliest = rows[0]["signal_date"]
        latest_price_date = _dates(store)[-1] if _dates(store) else "n/a"
        return {"ok": False, "signals": len(rows), "entry": earliest,
                "exit": latest_price_date,
                "reason": "signal ledger is recording, but no signal has reached "
                          "the shortest forward horizon yet."}

    return {"ok": True, "mode": "signal_events", "signals": len(rows),
            "horizons": by_horizon}


def run(store, cfg, min_days: int = 14, settings=None) -> dict:
    settings = settings or {"margin_of_safety_pct": 15, "target_upside_pct": 30}
    if _signal_count(store):
        return _run_signal_backtest(store, DEFAULT_HORIZONS)

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
            summary[v] = _summarize_returns(rets)
    return {"ok": True, "mode": "snapshot_replay", "entry": entry, "exit": exit_,
            "span_days": span, "by_verdict": summary}


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
                min_names: int = 20, settings=None, screened_only: bool = True,
                warmup_days: int = 300, bootstrap: int = 1000) -> dict:
    """Rolling, benchmark-relative backtest.

    Replays MANY overlapping cohorts: every ~`step_days` it takes an entry date,
    scores the names that pass a screener as-of then (matching the live board,
    which only shows screened names), and measures each name's forward return
    over ~`hold_days`. Returns are made benchmark-relative by subtracting that
    cohort's cross-sectional mean (the average scored name) — so we measure
    *stock selection* (did Buys beat the average candidate?), not market drift.

    `screened_only` restricts to screener-passing names (True = product-faithful).
    `warmup_days` skips the earliest cohorts so momentum/trend metrics have enough
    price history. Because cohorts overlap heavily (n over-counts), the Buy−Avoid
    spread comes with a **block-bootstrap 90% CI** (resampling whole cohorts).
    """
    settings = settings or {"margin_of_safety_pct": 15, "target_upside_pct": 30}
    dates = _dates(store)
    if len(dates) < 2:
        return {"ok": False, "reason": f"insufficient history: {len(dates)} snapshot date(s)."}
    if _days(dates[0], dates[-1]) < hold_days + warmup_days:
        return {"ok": False, "reason": f"history spans {_days(dates[0], dates[-1])}d; "
                f"need >= warmup ({warmup_days}d) + hold ({hold_days}d)."}

    warmup_cut = dates[0]
    for d in dates:                       # first date past the warmup window
        if _days(dates[0], d) >= warmup_days:
            warmup_cut = d
            break
    # entry dates: after warmup, spaced >= step_days apart, each with a full hold ahead
    entries, last_pick = [], None
    for d in dates:
        if d < warmup_cut:
            continue
        if _days(d, dates[-1]) < hold_days:
            break
        if last_pick is None or _days(last_pick, d) >= step_days:
            entries.append(d); last_pick = d

    secs = store.securities()
    alpha = {"Buy": [], "Watch": [], "Avoid": []}
    raw = {"Buy": [], "Watch": [], "Avoid": []}
    cohorts = []
    per_obs = {}        # {cohort_entry: [(verdict, alpha)]} for the block bootstrap
    for entry in entries:
        exit_ = _first_exit(dates, entry, hold_days)
        if not exit_:
            continue
        # candidates = screener-passing names as-of `entry` (matches the live board)
        asof = {}
        for sec in secs:
            m = _asof_metrics(store, sec["ticker"], entry)
            if "price" not in m:
                continue
            if screened_only and not passes_screen(m):
                continue
            asof[sec["ticker"]] = (sec, m)
        # point-in-time sector medians from those names' AS-OF metrics (no look-ahead)
        med = medians_from(cfg, [(sec["sector"], m) for sec, m in asof.values()])
        rows = []
        for tkr, (sec, m) in asof.items():
            p0 = _price_on(store, tkr, entry)
            p1 = _price_on(store, tkr, exit_)
            if not (p0 and p1 and p0 > 0):
                continue
            rec = score_ticker(cfg, sec, m, med, settings)
            rows.append((rec["verdict"], (p1 / p0 - 1) * 100))
        if len(rows) < min_names:
            continue
        mkt = statistics.mean(r for _, r in rows)   # cross-sectional benchmark
        cohort_alpha = {"Buy": [], "Watch": [], "Avoid": []}
        per_obs[entry] = []
        for v, r in rows:
            alpha.setdefault(v, []).append(r - mkt)
            raw.setdefault(v, []).append(r)
            cohort_alpha.setdefault(v, []).append(r - mkt)
            per_obs[entry].append((v, r - mkt))
        cohorts.append({
            "entry": entry, "exit": exit_, "n": len(rows),
            "mkt_return_pct": round(mkt, 2),
            "buy_alpha_pct": round(statistics.mean(cohort_alpha["Buy"]), 2) if cohort_alpha["Buy"] else None,
            "buy_n": len(cohort_alpha["Buy"]),
        })

    if not cohorts:
        return {"ok": False, "reason": "no cohort had enough screened names with a full forward window."}

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

    # block-bootstrap 90% CI for the spread (resamples whole cohorts, so it
    # reflects the real independent sample despite heavy cohort overlap)
    entry_list = [c["entry"] for c in cohorts]
    ci = _block_bootstrap_spread(per_obs, entry_list, B=bootstrap) if bootstrap else None
    significant = bool(ci and ci[0] > 0)   # 90% CI lower bound above zero

    return {"ok": True, "hold_days": hold_days, "step_days": step_days,
            "cohorts": len(cohorts), "window": [dates[0], dates[-1]],
            "screened_only": screened_only, "warmup_days": warmup_days,
            "by_verdict": by_verdict, "buy_minus_avoid_pct": spread,
            "spread_ci90": list(ci) if ci else None, "significant": significant,
            "monotonic": monotonic, "per_cohort": cohorts}
