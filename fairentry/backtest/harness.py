"""Backtest harness — replay the scoring model over point-in-time history and
measure whether Buys actually outperformed.

Uses metrics_history (append-only). For an entry snapshot date, it reconstructs
each ticker's metrics as-of that date, scores them, then measures the forward
return to a later snapshot — bucketed by verdict. With little history it reports
"insufficient history" gracefully; results sharpen as daily runs accumulate.

Rolling replay sector medians are reconstructed from each cohort's as-of
metrics. The simple one-window compatibility runner still uses current medians;
use ``run_rolling`` for strategy validation.
"""
from __future__ import annotations

import random
import statistics
import hashlib
import json
from datetime import date, timedelta

from ..scoring.engine import sector_medians, medians_from, score_ticker
from ..screeners import REGISTRY as SCREENERS
from .strategy import load_strategy
from .targets import targets_for
from .evidence import quality_for, price_series, evaluate_path, summarize_targets, summarize_methods


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


def screen_evidence(metrics: dict) -> list[dict]:
    out = []
    for key, mod in SCREENERS.items():
        try:
            passed, detail = mod.passes(metrics)
            out.append({"id": key, "passed": bool(passed), "detail": detail})
        except Exception as exc:
            out.append({"id": key, "passed": False, "detail": f"unavailable: {exc}"})
    return out


def _quality_metrics(metrics: dict, strategy) -> dict:
    """Apply the strategy's declared source policy before screening/scoring."""
    if strategy.data_quality_mode == "experimental":
        return metrics
    excluded = set(strategy.strict_excluded_sources)
    filtered = {k: v for k, v in metrics.items() if v.get("source") not in excluded}
    if strategy.data_quality_mode == "strict":
        allowed = {"sec_hist", "seed_price", "seed_hist", "computed", "FairEntry breakout_v2"}
        filtered = {k: v for k, v in filtered.items() if v.get("source") in allowed}
    return filtered


def _screen_memberships(metrics: dict) -> tuple[list[str], list[dict]]:
    evidence = screen_evidence(metrics)
    passed = [row["id"] for row in evidence if row["passed"]]
    return passed, evidence


def _live_primary_strategy(passed: list[str]) -> str | None:
    """Mirror build_board: Deep Value wins ties; otherwise Quality Growth."""
    if "deep_value" in passed:
        return "deep_value"
    if "quality_growth" in passed:
        return "quality_growth"
    return None


def _settings_for_strategy(cfg, base: dict, strategy_key: str | None) -> tuple[dict, str | None]:
    out = dict(base)
    preset_name = cfg.defaults.get("strategy_presets", {}).get(strategy_key) if strategy_key else None
    preset = cfg.scoring.get("presets", {}).get(preset_name) if preset_name else None
    if preset:
        out["weights"] = dict(preset)
    return out, preset_name


def _compact_categories(categories):
    """Keep every factor value/result while avoiding repeated prose in a large
    static artifact. Definitions and formulas remain versioned in scoring.yaml."""
    return [{"id": c["id"], "label": c["label"], "weight": c["weight"],
             "score": c["score"], "coverage": c.get("coverage"),
             "items": [{k: i.get(k) for k in ("id", "label", "metric", "actual", "expected",
                                                    "score", "source", "fetched_at", "status")}
                       for i in c.get("items", [])]}
            for c in categories]


def _compact_valuation(valuation):
    return {k: valuation.get(k) for k in ("fair_low", "fair_base", "fair_high", "buy_zone",
                                           "upside_pct", "valuation_label", "method_count") } | {
        "methods": [{k: m.get(k) for k in ("name", "key", "fair", "upside")}
                    for m in valuation.get("methods", [])]}


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
                warmup_days: int = 300, bootstrap: int = 1000,
                strategy=None, include_evidence: bool = True) -> dict:
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
    strategy = strategy or load_strategy()
    # Explicit argument remains useful for tests/CLI; strategy is the default.
    if screened_only is True:
        screened_only = strategy.screened_only
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
    strategy_alpha: dict[str, dict[str, list[float]]] = {}
    strategy_raw: dict[str, dict[str, list[float]]] = {}
    cohorts = []
    per_obs = {}        # {cohort_entry: [(verdict, alpha)]} for the block bootstrap
    observations = []
    for entry in entries:
        exit_ = _first_exit(dates, entry, hold_days)
        if not exit_:
            continue
        # candidates = screener-passing names as-of `entry` (matches the live board)
        asof = {}
        for sec in secs:
            m = _quality_metrics(_asof_metrics(store, sec["ticker"], entry), strategy)
            if "price" not in m:
                continue
            memberships, screening = _screen_memberships(m)
            if screened_only and not memberships:
                continue
            asof[sec["ticker"]] = (sec, m, memberships, screening)
        # point-in-time sector medians from those names' AS-OF metrics (no look-ahead)
        med = medians_from(cfg, [(sec["sector"], m) for sec, m, _, _ in asof.values()])
        rows = []
        cohort_observations = []
        for tkr, (sec, m, memberships, screening) in asof.items():
            p0 = _price_on(store, tkr, entry)
            p1 = _price_on(store, tkr, exit_)
            if not (p0 and p1 and p0 > 0):
                continue
            primary_strategy = _live_primary_strategy(memberships)
            scoring_settings, preset_name = _settings_for_strategy(cfg, settings, primary_strategy)
            rec = score_ticker(cfg, sec, m, med, scoring_settings)
            # Apply the execution contract to the measured investment return,
            # not only to the target-evidence card. Because the configured
            # costs are entry-side basis points, the adjusted entry is the
            # denominator for both raw return and benchmark-relative alpha.
            adjusted_entry = p0 * (1 + (strategy.slippage_bps + strategy.transaction_cost_bps) / 10000)
            rows.append((rec["verdict"], (p1 / adjusted_entry - 1) * 100, primary_strategy))
            if include_evidence:
                targets = targets_for(rec, m, strategy)
                longest_target = max((t.get("expiry_days", strategy.target_expiry_days)
                                      for t in targets.values()), default=strategy.target_expiry_days)
                path = price_series(store, tkr, entry, max(strategy.horizons_days + (longest_target,)))
                outcome = evaluate_path(path, adjusted_entry, targets, strategy.horizons_days, entry)
                practical = outcome.get("targets", {}).get("practical")
                if not practical or not practical.get("price") or not practical.get("status"):
                    raise RuntimeError(f"target completeness invariant failed for {tkr} on {entry}")
                q = quality_for(m)
                cohort_observations.append({
                    "observation_id": f"{entry}:{tkr}", "ticker": tkr,
                    "company": sec.get("company"), "sector": sec.get("sector"),
                    "strategy_key": primary_strategy, "strategy_memberships": memberships,
                    "preset_name": preset_name,
                    "entry_date": entry, "entry_price": round(adjusted_entry, 2),
                    "raw_close": round(p0, 2), "verdict": rec["verdict"],
                    "score": rec["score"], "coverage_pct": rec.get("coverage_pct"),
                    "screening": screening, "weights": scoring_settings.get("weights") or
                        {cid: c["weight"] for cid, c in cfg.categories.items()},
                    "thresholds": cfg.verdict_bands, "vetoes": rec["vetoes"],
                    "soft_gates": rec["soft_gates"], "categories": _compact_categories(rec["categories"]),
                    "valuation": _compact_valuation(rec["valuation"]), "targets": targets,
                    "practical_target": practical,
                    "data_quality": q, "outcome": outcome,
                })
        if len(rows) < min_names:
            continue
        # Evidence and calibration denominators must describe the same accepted
        # cohorts as the headline alpha calculation. Do not leak observations
        # from a cohort rejected by the minimum-population rule.
        observations.extend(cohort_observations)
        mkt = statistics.mean(r for _, r, _ in rows)   # cross-sectional benchmark
        cohort_alpha = {"Buy": [], "Watch": [], "Avoid": []}
        per_obs[entry] = []
        for v, r, strategy_key in rows:
            a = r - mkt
            alpha.setdefault(v, []).append(a)
            raw.setdefault(v, []).append(r)
            cohort_alpha.setdefault(v, []).append(a)
            per_obs[entry].append((v, a))
            skey = strategy_key or "unscreened"
            strategy_alpha.setdefault(skey, {}).setdefault(v, []).append(a)
            strategy_raw.setdefault(skey, {}).setdefault(v, []).append(r)
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
    by_strategy = {}
    for strategy_key, verdicts in strategy_alpha.items():
        summary = {v: {**stats(vals),
                       "mean_raw_return_pct": round(statistics.mean(strategy_raw[strategy_key][v]), 2)}
                   for v, vals in verdicts.items() if vals}
        buy = summary.get("Buy", {}).get("mean_alpha_pct")
        avoid = summary.get("Avoid", {}).get("mean_alpha_pct")
        by_strategy[strategy_key] = {
            "preset": cfg.defaults.get("strategy_presets", {}).get(strategy_key),
            "weights": (cfg.scoring.get("presets", {}).get(
                cfg.defaults.get("strategy_presets", {}).get(strategy_key)) or {}),
            "by_verdict": summary,
            "buy_minus_avoid_pct": round(buy - avoid, 2)
                if buy is not None and avoid is not None else None,
        }
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

    run_basis = {"strategy": strategy.to_dict(), "window": [dates[0], dates[-1]],
                 "hold": hold_days, "step": step_days, "settings": settings}
    run_id = "bt-" + hashlib.sha256(json.dumps(run_basis, sort_keys=True).encode()).hexdigest()[:12]
    return {"ok": True, "run_id": run_id, "strategy": strategy.to_dict(),
            "data_quality": {"warning": "Seeded universe uses today's survivors; inspect each observation grade.",
                             "universe_bias": "current_top_n_survivorship",
                             "source_policy": strategy.data_quality_mode},
            "contract_capabilities": {
                "entry": ["snapshot_close"], "target_hit": ["close"],
                "benchmark": ["cohort_mean"],
                "data_quality": ["strict", "mostly_point_in_time", "experimental"],
                "unsupported_values": "rejected_at_strategy_load",
            },
            "survivorship_control": {
                "seeded_universe_biased": True,
                "prospective_signal_events": _signal_count(store),
                "status": "maturing" if _signal_count(store) else "not_started_in_this_store",
            },
            "hold_days": hold_days, "step_days": step_days,
            "execution": {"slippage_bps": strategy.slippage_bps,
                          "transaction_cost_bps": strategy.transaction_cost_bps,
                          "return_basis": "cost_adjusted_entry"},
            "cohorts": len(cohorts), "window": [dates[0], dates[-1]],
            "screened_only": screened_only, "warmup_days": warmup_days,
            "by_verdict": by_verdict, "buy_minus_avoid_pct": spread,
            "by_strategy": by_strategy,
            "spread_ci90": list(ci) if ci else None, "significant": significant,
            "monotonic": monotonic, "per_cohort": cohorts,
            "target_summary": summarize_targets(observations, strategy.primary_target) if include_evidence else {},
            "target_method_summary": summarize_methods(observations) if include_evidence else {},
            "observations": observations if include_evidence else []}
