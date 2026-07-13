"""Deterministic scoring engine (Layer A + verdict).

Reads config + a ticker's stored metrics, computes item -> category -> base
score with a full trace (matching the UI's drill-down contract), applies the
fair value, then vetoes/soft-gates/thesis-modifier -> Buy / Watch / Avoid.

Everything is a pure function of stored inputs (reproducible / backtestable).
The LLM thesis modifier is injected in Phase 4; here it defaults to 0.
"""
from __future__ import annotations

import statistics

from .rules import apply_rule
from .fair_value import fair_value


# ---- sector medians (for sector_rel rules) --------------------------------
def sector_medians(cfg, store) -> dict:
    """{sector: {metric: median}} for every metric used by a sector_rel rule."""
    metrics_needed = set()
    for cat in cfg.categories.values():
        for it in cat["items"]:
            if it["rule"].get("type") == "sector_rel":
                metrics_needed.add(it["metric"])
    by_sector: dict = {}
    for sec in store.securities():
        s, t = sec["sector"], sec["ticker"]
        m = store.metrics_for(t)
        for mid in metrics_needed:
            v = m.get(mid, {}).get("value")
            if isinstance(v, (int, float)):
                by_sector.setdefault(s, {}).setdefault(mid, []).append(v)
    return {s: {mid: statistics.median(vs) for mid, vs in d.items() if vs}
            for s, d in by_sector.items()}


def _safe_eval(expr, ns):
    try:
        return eval(expr, {"__builtins__": {}}, ns)  # noqa: S307 (own trusted config)
    except Exception:
        return None   # unevaluable (e.g. metric missing) -> treated as not firing


def score_ticker(cfg, sec, metrics_raw, medians, settings) -> dict:
    """Return the full scored record for one ticker (trace + verdict)."""
    mos = settings.get("margin_of_safety_pct", 15)
    target_upside = settings.get("target_upside_pct", 30)
    weights = settings.get("weights") or {cid: c["weight"] for cid, c in cfg.categories.items()}

    # flat metric map + provenance
    flat = {k: (v["value"] if isinstance(v, dict) else v) for k, v in metrics_raw.items()}
    prov = {k: v for k, v in metrics_raw.items()}

    fv = fair_value(metrics_raw, mos)
    flat.update({"intrinsic_gap_pct": fv["intrinsic_gap_pct"],
                 "upside_pct": fv["upside_pct"], "valuation_label": fv["valuation_label"]})

    med = medians.get(sec["sector"], {})
    categories, cat_scores = [], {}
    for cid, cat in cfg.categories.items():
        items, num, den = [], 0.0, 0.0
        for it in cat["items"]:
            val = flat.get(it["metric"])
            score, how = apply_rule(it["rule"], val, med.get(it["metric"]))
            rec = {"id": it["id"], "label": it["label"], "weight": it["weight"],
                   "metric": it["metric"], "actual": val, "expected": it.get("expected", ""),
                   "rule": how, "score": None if score is None else round(score),
                   "source": prov.get(it["metric"], {}).get("source"),
                   "fetched_at": prov.get(it["metric"], {}).get("fetched_at")}
            items.append(rec)
            if score is not None:
                num += it["weight"] * score
                den += it["weight"]
        cscore = round(num / den) if den else None
        cat_scores[cid] = cscore
        categories.append({"id": cid, "label": cat["label"], "weight": weights.get(cid, cat["weight"]),
                           "score": cscore, "coverage": round(den / sum(i["weight"] for i in cat["items"]) * 100),
                           "items": items})

    # base score = weighted avg of covered categories
    bnum = sum(weights.get(cid, cfg.categories[cid]["weight"]) * s
               for cid, s in cat_scores.items() if s is not None)
    bden = sum(weights.get(cid, cfg.categories[cid]["weight"])
               for cid, s in cat_scores.items() if s is not None)
    base = round(bnum / bden, 1) if bden else 0.0

    modifier = settings.get("thesis_modifier", 0)   # Phase 4 injects the real value
    preliminary = round(base + modifier, 1)

    # -- vetoes / gates namespace --
    ns = dict(flat)
    ns.update({f"category_{cid}": s for cid, s in cat_scores.items()})
    ns["target_upside"] = target_upside
    ns["upside_pct"] = fv["upside_pct"]
    ns["valuation_label"] = fv["valuation_label"]

    vetoes = [{"id": v["id"], "reason": v["reason"]}
              for v in cfg.scoring.get("vetoes", []) if _safe_eval(v["when"], ns) is True]
    gates = [{"id": g["id"], "reason": g["reason"]}
             for g in cfg.scoring.get("soft_gates", []) if _safe_eval(g["when"], ns) is True]

    buy_b, watch_b = cfg.verdict_bands["buy"], cfg.verdict_bands["watch"]
    if vetoes:
        verdict = "Avoid"
    else:
        verdict = "Buy" if preliminary >= buy_b else "Watch" if preliminary >= watch_b else "Avoid"
        if verdict == "Buy" and gates:
            verdict = "Watch"

    return {
        "ticker": sec["ticker"], "company": sec["company"], "sector": sec["sector"],
        "price": flat.get("price"),
        "base_score": base, "thesis_modifier": modifier, "preliminary": preliminary,
        "score": round(preliminary), "verdict": verdict,
        "categories": categories, "valuation": fv,
        "vetoes": vetoes, "soft_gates": gates,
        "coverage_pct": round(bden / sum(weights.get(cid, cfg.categories[cid]["weight"])
                                         for cid in cfg.categories) * 100) if bden else 0,
    }
