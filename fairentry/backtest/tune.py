"""Weight-tuning loop — search category weights that widen the Buy-filter's
benchmark-relative alpha spread, validated on held-out cohorts.

Why this is fast
----------------
The expensive parts of a backtest (reconstructing as-of metrics, computing
category item-scores, vetoes and soft-gates, and the forward return) are all
INDEPENDENT of the cross-category weights — weights only change how category
scores combine into the base score, hence the Buy/Watch/Avoid split. So we
precompute one observation per (name, cohort) ONCE, then evaluating a weight
vector is pure arithmetic over those observations. That makes searching hundreds
of weight vectors nearly free.

Honesty
-------
Tuning and scoring on the same data overstates results (overfitting). Cohorts
are split chronologically: we optimise on the EARLIER `train` cohorts and report
the spread on the later, unseen `test` cohorts. A tuned weight set is only worth
adopting if it improves the **test** spread, not just the train spread.
"""
from __future__ import annotations

import statistics

from ..scoring.engine import score_ticker, medians_from
from .harness import _dates, _asof_metrics, _price_on, _first_exit, _days


def precompute(store, cfg, hold_days: int = 30, step_days: int = 7,
               min_names: int = 20, settings=None) -> list[dict]:
    """One observation per (name, cohort): weight-independent category scores +
    veto/gate flags + benchmark-relative alpha. This is the only expensive pass."""
    settings = dict(settings or {"margin_of_safety_pct": 15, "target_upside_pct": 30})
    settings.pop("weights", None)   # category scores don't depend on weights
    dates = _dates(store)
    if len(dates) < 2 or _days(dates[0], dates[-1]) < hold_days:
        return []
    entries, last_pick = [], None
    for d in dates:
        if _days(d, dates[-1]) < hold_days:
            break
        if last_pick is None or _days(last_pick, d) >= step_days:
            entries.append(d); last_pick = d

    secs = store.securities()
    obs: list[dict] = []
    for entry in entries:
        exit_ = _first_exit(dates, entry, hold_days)
        if not exit_:
            continue
        asof = {}
        for sec in secs:
            m = _asof_metrics(store, sec["ticker"], entry)
            if "price" in m:
                asof[sec["ticker"]] = (sec, m)
        med = medians_from(cfg, [(sec["sector"], m) for sec, m in asof.values()])
        rows = []
        for tkr, (sec, m) in asof.items():
            p0 = _price_on(store, tkr, entry)
            p1 = _price_on(store, tkr, exit_)
            if not (p0 and p1 and p0 > 0):
                continue
            rec = score_ticker(cfg, sec, m, med, settings)
            rows.append((rec, (p1 / p0 - 1) * 100))
        if len(rows) < min_names:
            continue
        mkt = statistics.mean(r for _, r in rows)   # cross-sectional benchmark
        for rec, ret in rows:
            obs.append({
                "cohort": entry,
                "cat": {c["id"]: c["score"] for c in rec["categories"]},  # None-safe
                "vetoed": bool(rec["vetoes"]),
                "gated": bool(rec["soft_gates"]),
                "alpha": ret - mkt,
            })
    return obs


def evaluate(obs: list[dict], weights: dict, buy_b: float, watch_b: float) -> dict:
    """Assign each observation a verdict under `weights` and summarise alpha.
    Pure arithmetic — safe to call thousands of times."""
    buckets = {"Buy": [], "Watch": [], "Avoid": []}
    for o in obs:
        if o["vetoed"]:
            v = "Avoid"
        else:
            num = den = 0.0
            for cid, sc in o["cat"].items():
                if sc is not None:
                    w = weights.get(cid, 0)
                    num += w * sc; den += w
            base = num / den if den else 0.0
            v = "Buy" if base >= buy_b else "Watch" if base >= watch_b else "Avoid"
            if v == "Buy" and o["gated"]:
                v = "Watch"
        buckets[v].append(o["alpha"])

    def m(x):
        return round(statistics.mean(x), 3) if x else None
    ba, wa, aa = m(buckets["Buy"]), m(buckets["Watch"]), m(buckets["Avoid"])
    spread = round(ba - aa, 3) if (ba is not None and aa is not None) else None
    return {"buy": ba, "watch": wa, "avoid": aa, "spread": spread,
            "n_buy": len(buckets["Buy"]), "n_watch": len(buckets["Watch"]),
            "n_avoid": len(buckets["Avoid"]),
            "monotonic": None if None in (ba, wa, aa) else (ba >= wa >= aa)}


def _normalize(w: dict) -> dict:
    tot = sum(w.values()) or 1
    return {k: round(v / tot * 100, 2) for k, v in w.items()}


def _ascend(score_fn, start: dict, cats: list[str], step: float = 2.0,
            rounds: int = 12, floor: float = 2.0, ceil: float = 40.0) -> tuple[dict, float]:
    """Coordinate ascent on the weight simplex for an arbitrary score function."""
    weights = _normalize(dict(start))
    best = score_fn(weights)
    improved, it = True, 0
    while improved and it < rounds:
        improved, it = False, it + 1
        for cid in cats:
            for delta in (step, -step):
                cand = dict(weights)
                cand[cid] = cand[cid] + delta
                if cand[cid] < floor or cand[cid] > ceil:
                    continue
                cand = _normalize(cand)
                s = score_fn(cand)
                if s > best + 1e-6:
                    best, weights, improved = s, cand, True
    return weights, best


def search(train_obs: list[dict], cats: list[str], buy_b: float, watch_b: float,
           start: dict, step: float = 2.0, rounds: int = 12,
           floor: float = 2.0, ceil: float = 40.0, min_buy: int = 50) -> tuple[dict, float]:
    """Single-objective search: maximise the train Buy−Avoid spread."""
    def score(w):
        r = evaluate(train_obs, w, buy_b, watch_b)
        return -1e9 if (r["spread"] is None or r["n_buy"] < min_buy) else r["spread"]
    return _ascend(score, start, cats, step, rounds, floor, ceil)


# ---------------------------------------------------------------------------
# Hardened, regime-robust tuning
# ---------------------------------------------------------------------------
def _fold_sets(cohorts: list[str], k: int) -> list[set]:
    """Split the sorted cohort timeline into k contiguous (blocked) time folds."""
    n = len(cohorts)
    return [set(cohorts[i * n // k:(i + 1) * n // k]) for i in range(k)]


def _subset(obs, cohort_set):
    return [o for o in obs if o["cohort"] in cohort_set]


def robust_tune(store, cfg, holds=(20, 30, 60), step_days: int = 7, folds: int = 4,
                reg: float = 0.15, min_names: int = 20, min_buy: int = 30,
                settings=None) -> dict:
    """Regime-robust weight tuning. A weight set is judged by its **worst-case**
    Buy−Avoid spread across every (time-fold × hold-window) slice — so it can't
    win by spiking in one lucky regime — with an L1 penalty pulling it toward the
    current weights (won't gut a category on thin evidence). The LAST time fold is
    a final holdout, never used during the search. Recommends only.
    """
    obs_by_hold = {h: precompute(store, cfg, h, step_days, min_names, settings) for h in holds}
    obs_by_hold = {h: ob for h, ob in obs_by_hold.items() if ob}
    if not obs_by_hold:
        return {"ok": False, "reason": "insufficient history for the requested windows."}

    cohorts = sorted({o["cohort"] for ob in obs_by_hold.values() for o in ob})
    if len(cohorts) < folds + 1:
        folds = max(2, len(cohorts) - 1)
    fset = _fold_sets(cohorts, folds)
    selection, holdout = fset[:-1], fset[-1]

    cats = list(cfg.categories.keys())
    buy_b, watch_b = cfg.verdict_bands["buy"], cfg.verdict_bands["watch"]
    default = _normalize({cid: c["weight"] for cid, c in cfg.categories.items()})

    def spread_on(w, cohort_set, h):
        r = evaluate(_subset(obs_by_hold[h], cohort_set), w, buy_b, watch_b)
        return r["spread"] if (r["spread"] is not None and r["n_buy"] >= min_buy) else None

    def worst_selection(w):
        vals = []
        for cs in selection:
            for h in obs_by_hold:
                s = spread_on(w, cs, h)
                if s is None:
                    return None
                vals.append(s)
        return min(vals) if vals else None

    def robust_score(w):
        worst = worst_selection(w)
        if worst is None:
            return -1e9
        penalty = reg * sum(abs(w[c] - default[c]) for c in cats) / 100.0
        return worst - penalty

    starts = [default] + [_normalize({**default, **p}) for p in cfg.scoring.get("presets", {}).values()]
    best_start = max(starts, key=robust_score)
    tuned, _ = _ascend(robust_score, best_start, cats)

    def report(w):
        return {
            "weights": w,
            "worst_selection_spread": worst_selection(w),
            "holdout": {h: spread_on(w, holdout, h) for h in obs_by_hold},
            "selection_folds": [
                {"range": [min(cs), max(cs)], "spread": {h: spread_on(w, cs, h) for h in obs_by_hold}}
                for cs in selection],
        }

    drep, trep = report(default), report(tuned)

    # verdict: adopt only if tuned wins the final holdout at EVERY hold window,
    # never loses one materially, and is no worse on the worst-case selection slice
    holds_list = list(obs_by_hold)
    better = sum(1 for h in holds_list
                 if trep["holdout"][h] is not None and drep["holdout"][h] is not None
                 and trep["holdout"][h] > drep["holdout"][h] + 0.3)
    worse = sum(1 for h in holds_list
                if trep["holdout"][h] is not None and drep["holdout"][h] is not None
                and trep["holdout"][h] < drep["holdout"][h] - 0.3)
    robust_ok = (trep["worst_selection_spread"] is not None and drep["worst_selection_spread"] is not None
                 and trep["worst_selection_spread"] >= drep["worst_selection_spread"] - 0.3)
    if worse == 0 and robust_ok and better >= (len(holds_list) + 1) // 2:
        verdict, note = "adopt", f"tuned wins {better}/{len(holds_list)} holdout windows, none worse, robust holds."
    elif worse > 0 or not robust_ok:
        verdict, note = "overfit", "tuned is worse on some holdout window or the worst-case regime — keep defaults."
    else:
        verdict, note = "no_gain", "tuned ≈ default out-of-sample — keep defaults."

    return {"ok": True, "holds": holds_list, "folds": folds, "step_days": step_days, "reg": reg,
            "cohorts": len(cohorts), "holdout_range": [min(holdout), max(holdout)],
            "default": drep, "tuned": trep, "verdict": verdict, "note": note}


def tune(store, cfg, hold_days: int = 30, step_days: int = 7, min_names: int = 20,
         test_frac: float = 0.3, settings=None) -> dict:
    """Precompute, split cohorts train/test chronologically, search weights on
    train, and report train+test spreads for the default weights, each preset,
    and the tuned vector."""
    obs = precompute(store, cfg, hold_days, step_days, min_names, settings)
    if not obs:
        return {"ok": False, "reason": "insufficient history for the requested window."}

    cohorts = sorted({o["cohort"] for o in obs})
    cut = cohorts[int(len(cohorts) * (1 - test_frac))] if len(cohorts) > 3 else cohorts[-1]
    train = [o for o in obs if o["cohort"] < cut]
    test = [o for o in obs if o["cohort"] >= cut]
    if not train or not test:
        train, test = obs, obs   # too few cohorts to split — report in-sample

    cats = list(cfg.categories.keys())
    buy_b, watch_b = cfg.verdict_bands["buy"], cfg.verdict_bands["watch"]
    default = {cid: c["weight"] for cid, c in cfg.categories.items()}

    def report(w):
        return {"weights": w,
                "train": evaluate(train, w, buy_b, watch_b),
                "test": evaluate(test, w, buy_b, watch_b)}

    candidates = {"default": report(default)}
    for name, preset in cfg.scoring.get("presets", {}).items():
        w = _normalize({**default, **preset})
        candidates[f"preset:{name}"] = report(w)

    # tune from the best starting point (default or a preset) on train spread
    starts = [default] + [{**default, **p} for p in cfg.scoring.get("presets", {}).values()]
    best_start = max(starts, key=lambda w: evaluate(train, _normalize(w), buy_b, watch_b)["spread"] or -1e9)
    tuned_w, tuned_train_spread = search(train, cats, buy_b, watch_b, best_start)
    candidates["tuned"] = report(tuned_w)

    return {"ok": True, "hold_days": hold_days, "step_days": step_days,
            "cohorts": len(cohorts), "train_cohorts": len({o["cohort"] for o in train}),
            "test_cohorts": len({o["cohort"] for o in test}),
            "cut_date": cut, "cats": cats, "candidates": candidates,
            "default_test_spread": candidates["default"]["test"]["spread"],
            "tuned_test_spread": candidates["tuned"]["test"]["spread"]}
