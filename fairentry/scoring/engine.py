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
def _median_metrics(cfg) -> set:
    """Metrics that need a sector median: valuation multiples (peer P/E, P/S, P/B,
    P/FCF for the multi-method fair value) plus any sector_rel rule metric."""
    needed = {"fwd_pe", "ps_ratio", "pb_ratio", "pfcf_ratio"}
    for cat in cfg.categories.values():
        for it in cat["items"]:
            if it["rule"].get("type") == "sector_rel":
                needed.add(it["metric"])
    return needed


def medians_from(cfg, pairs) -> dict:
    """{sector: {metric: median}} from (sector, metrics) pairs. `metrics` values
    may be raw numbers or {"value": ...} dicts. Use this to compute medians from
    any snapshot — current OR point-in-time (as-of a past date, for backtesting)."""
    needed = _median_metrics(cfg)
    by_sector: dict = {}
    for sector, m in pairs:
        for mid in needed:
            v = m.get(mid, {})
            v = v.get("value") if isinstance(v, dict) else v
            if isinstance(v, (int, float)):
                by_sector.setdefault(sector, {}).setdefault(mid, []).append(v)
    return {s: {mid: statistics.median(vs) for mid, vs in d.items() if vs}
            for s, d in by_sector.items()}


def sector_medians(cfg, store) -> dict:
    """Sector medians from the CURRENT snapshot (live scoring)."""
    return medians_from(cfg, ((sec["sector"], store.metrics_for(sec["ticker"]))
                              for sec in store.securities()))


def _safe_eval(expr, ns):
    expr = expr.replace(" true", " True").replace(" false", " False")
    expr = expr.replace("== true", "== True").replace("== false", "== False")
    try:
        return eval(expr, {"__builtins__": {}}, ns)  # noqa: S307 (own trusted config)
    except Exception:
        return None   # unevaluable (e.g. metric missing) -> treated as not firing


def score_ticker(cfg, sec, metrics_raw, medians, settings) -> dict:
    """Return the full scored record for one ticker (trace + verdict)."""
    mos = settings.get("margin_of_safety_pct", 15)
    target_upside = settings.get("target_upside_pct", 30)
    weights = settings.get("weights") or {cid: c["weight"] for cid, c in cfg.categories.items()}
    features = settings.get("model_features") or {}
    ps_direction_fix = features.get("ps_direction_fix", True)
    coverage_gates = features.get("coverage_gates", True)

    # flat metric map + provenance
    flat = {k: (v["value"] if isinstance(v, dict) else v) for k, v in metrics_raw.items()}
    prov = {k: v for k, v in metrics_raw.items()}

    med = medians.get(sec["sector"], {})
    fv = fair_value(metrics_raw, mos, med, sec, features)
    flat.update({"intrinsic_gap_pct": fv["intrinsic_gap_pct"],
                 "upside_pct": fv["upside_pct"], "valuation_label": fv["valuation_label"]})
    categories, cat_scores = [], {}
    for cid, cat in cfg.categories.items():
        items, num, den, covered_weight = [], 0.0, 0.0, 0.0
        for it in cat["items"]:
            val = flat.get(it["metric"])
            rule = it["rule"]
            if it["id"] == "sales_value" and not ps_direction_fix:
                rule = {**rule, "legacy_lower_better_inversion": True}
            score, how = apply_rule(rule, val, med.get(it["metric"]))
            rec = {"id": it["id"], "label": it["label"], "weight": it["weight"],
                   "metric": it["metric"], "actual": val, "expected": it.get("expected", ""),
                   "definition": it.get("definition", ""), "formula": it.get("formula", ""),
                   "rule": how, "score": None if score is None else round(score),
                   "source": prov.get(it["metric"], {}).get("source"),
                   "fetched_at": prov.get(it["metric"], {}).get("fetched_at"),
                   "status": ("unknown" if score is None else "satisfied" if score >= 70
                              else "partial" if score >= 45 else "failed")}
            items.append(rec)
            if score is not None:
                num += it["weight"] * score
                den += it["weight"]
                if val is not None:
                    covered_weight += it["weight"]
        cscore = round(num / den) if den else None
        configured_item_weight = sum(i["weight"] for i in cat["items"])
        for item in items:
            item["contribution"] = (round(item["weight"] * item["score"] / den, 2)
                                    if item["score"] is not None and den else None)
        cat_scores[cid] = cscore
        categories.append({"id": cid, "label": cat["label"], "weight": weights.get(cid, cat["weight"]),
                           "score": cscore,
                           "coverage": round(covered_weight / configured_item_weight * 100),
                           "available_item_weight": covered_weight,
                           "configured_item_weight": configured_item_weight,
                           "missing_item_weight": configured_item_weight - covered_weight,
                           "items": items})

    # Overall coverage is the category-weighted share of item evidence, not
    # merely the share of categories with at least one value.
    total_category_weight = sum(weights.get(cid, cfg.categories[cid]["weight"])
                                for cid in cfg.categories)
    overall_coverage = round(sum(
        weights.get(c["id"], cfg.categories[c["id"]]["weight"]) * c["coverage"] / 100
        for c in categories
    ) / total_category_weight * 100) if total_category_weight else 0

    # base score = weighted avg of covered categories
    bnum = sum(weights.get(cid, cfg.categories[cid]["weight"]) * s
               for cid, s in cat_scores.items() if s is not None)
    bden = sum(weights.get(cid, cfg.categories[cid]["weight"])
               for cid, s in cat_scores.items() if s is not None)
    base = round(bnum / bden, 1) if bden else 0.0
    for category in categories:
        category["contribution"] = (
            round(category["weight"] * category["score"] / bden, 2)
            if category["score"] is not None and bden else None)

    modifier = settings.get("thesis_modifier", 0)   # Phase 4 injects the real value
    preliminary = round(base + modifier, 1)

    # -- vetoes / gates namespace --
    ns = dict(flat)
    ns.update({f"category_{cid}": s for cid, s in cat_scores.items()})
    ns.update({f"coverage_{c['id']}": c["coverage"] for c in categories})
    ns["coverage_pct"] = overall_coverage
    ns["valuation_confidence"] = fv["valuation_confidence"]
    ns["target_upside"] = target_upside
    ns["upside_pct"] = fv["upside_pct"]
    ns["valuation_label"] = fv["valuation_label"]

    vetoes = [{"id": v["id"], "reason": v["reason"], "condition": v["when"],
               "result": True, "effect": "Force Avoid"}
              for v in cfg.scoring.get("vetoes", []) if _safe_eval(v["when"], ns) is True]
    gates = []
    for g in cfg.scoring.get("soft_gates", []):
        if not coverage_gates and g["id"] in {"survival_coverage", "overall_coverage"}:
            continue
        fired = _safe_eval(g["when"], ns)
        if fired is True:
            gates.append({"id": g["id"], "reason": g["reason"], "condition": g["when"],
                          "result": True, "effect": "Cap Buy to Watch"})
        elif fired is None:
            gates.append({"id": g["id"], "reason": f"{g['reason']} (missing data)",
                          "condition": g["when"], "result": None,
                          "effect": "Cap Buy to Watch because required data is missing"})

    buy_b, watch_b = cfg.verdict_bands["buy"], cfg.verdict_bands["watch"]
    if vetoes:
        score_band_verdict = "Buy" if preliminary >= buy_b else "Watch" if preliminary >= watch_b else "Avoid"
        verdict = "Avoid"
    else:
        score_band_verdict = "Buy" if preliminary >= buy_b else "Watch" if preliminary >= watch_b else "Avoid"
        verdict = score_band_verdict
        if verdict == "Buy" and gates:
            verdict = "Watch"

    decision_trace = {
        "formula": "final score = round(base score + thesis modifier)",
        "base_score": base,
        "base_numerator": round(bnum, 2),
        "available_category_weight": bden,
        "thesis_modifier": modifier,
        "preliminary_score": preliminary,
        "final_score": round(preliminary),
        "thresholds": {"buy": buy_b, "watch": watch_b},
        "score_band_verdict": score_band_verdict,
        "vetoes": vetoes,
        "soft_gates": gates,
        "final_verdict": verdict,
        "explanation": ("A hard veto forced Avoid." if vetoes else
                        "A soft gate capped Buy to Watch." if score_band_verdict == "Buy" and gates else
                        "The final verdict follows the configured score band."),
    }

    return {
        "ticker": sec["ticker"], "company": sec["company"], "sector": sec["sector"],
        "country": sec.get("country"),
        "price": flat.get("price"),
        "base_score": base, "thesis_modifier": modifier, "preliminary": preliminary,
        "score": round(preliminary), "verdict": verdict,
        "categories": categories, "valuation": fv,
        "vetoes": vetoes, "soft_gates": gates,
        "coverage_pct": overall_coverage,
        "coverage_confidence": "high" if overall_coverage >= 85 else
                               "medium" if overall_coverage >= 70 else "low",
        "decision_trace": decision_trace,
    }
