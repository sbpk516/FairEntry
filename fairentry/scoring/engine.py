"""Deterministic scoring engine (Layer A + verdict).

Reads config + a ticker's stored metrics, computes tested item -> category ->
base score with a full trace, applies fair value, then tested vetoes and gates.
AI, news, and every factor not reproduced by the historical replay are exported
as context only and cannot change Buy / Watch / Avoid.
"""
from __future__ import annotations

import statistics

from .rules import apply_rule
from .fair_value import fair_value


def growth_qualification(flat: dict, valuation: dict) -> dict:
    """Decide whether business direction is good enough for a Buy.

    Growth does not need to be fast. A stable business can qualify when its
    price is deeply below fair value. Otherwise at least one strong numerical
    improvement is required. News and narrative never satisfy this gate.
    """
    revenue = flat.get("rev_growth_qoq")
    operating_margin = flat.get("oper_margin")
    fair_gap = valuation.get("intrinsic_gap_pct")

    stable_signals = []
    if isinstance(revenue, (int, float)) and revenue >= -2:
        stable_signals.append("Revenue is stable or growing")
    if isinstance(operating_margin, (int, float)) and operating_margin >= 0:
        stable_signals.append("The business is currently profitable at the operating level")
    deeply_undervalued = isinstance(fair_gap, (int, float)) and fair_gap >= 30
    stable_and_cheap = len(stable_signals) == 2 and deeply_undervalued

    improvement_signals = []
    if isinstance(revenue, (int, float)) and revenue >= 8:
        improvement_signals.append("Revenue increased meaningfully")
    meaningfully_improving = bool(improvement_signals)

    qualified = stable_and_cheap or meaningfully_improving
    if stable_and_cheap:
        path = "stable_and_deeply_undervalued"
        explanation = (
            "Revenue is stable, the business has a non-negative operating "
            "margin, and the price is at least 30% below the central "
            "fair-value estimate."
        )
    elif meaningfully_improving:
        path = "meaningfully_improving"
        explanation = improvement_signals[0] + "."
    else:
        path = "not_yet_qualified"
        explanation = (
            "The available numbers do not yet show stable growth at a deep "
            "discount or a meaningful business improvement."
        )
    return {
        "qualified": qualified,
        "path": path,
        "explanation": explanation,
        "stable_signals": stable_signals,
        "improvement_signals": improvement_signals,
        "fair_value_gap_pct": fair_gap,
        "thresholds": {
            "stable_revenue_floor_pct": -2,
            "stable_operating_margin_floor_pct": 0,
            "deep_discount_pct": 30,
            "meaningful_revenue_growth_pct": 8,
        },
    }


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
                              for sec in store.active_securities()))


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

    # flat metric map + provenance
    flat = {k: (v["value"] if isinstance(v, dict) else v) for k, v in metrics_raw.items()}
    prov = {k: v for k, v in metrics_raw.items()}

    med = medians.get(sec["sector"], {})
    fv = fair_value(metrics_raw, mos, med)
    growth_check = growth_qualification(flat, fv)
    flat.update({"intrinsic_gap_pct": fv["intrinsic_gap_pct"],
                 "upside_pct": fv["upside_pct"], "valuation_label": fv["valuation_label"],
                 "growth_qualified": growth_check["qualified"]})
    categories, cat_scores = [], {}
    for cid, cat in cfg.categories.items():
        items, num, den, observed_den = [], 0.0, 0.0, 0.0
        for it in cat["items"]:
            val = flat.get(it["metric"])
            score, how = apply_rule(it["rule"], val, med.get(it["metric"]))
            decision_status = it.get("decision_status", "tested")
            rec = {"id": it["id"], "label": it["label"], "weight": it["weight"],
                   "metric": it["metric"], "actual": val, "expected": it.get("expected", ""),
                   "definition": it.get("definition", ""), "formula": it.get("formula", ""),
                   "rule": how, "score": None if score is None else round(score),
                   "decision_status": decision_status,
                   "source": prov.get(it["metric"], {}).get("source"),
                   "fetched_at": prov.get(it["metric"], {}).get("fetched_at"),
                   "status": ("unknown" if score is None else "satisfied" if score >= 70
                              else "partial" if score >= 45 else "failed")}
            items.append(rec)
            if score is not None and decision_status == "tested":
                num += it["weight"] * score
                den += it["weight"]
                if val is not None:
                    observed_den += it["weight"]
        cscore = round(num / den) if den else None
        configured_item_weight = sum(
            i["weight"] for i in cat["items"]
            if i.get("decision_status", "tested") == "tested"
        )
        for item in items:
            item["contribution"] = (round(item["weight"] * item["score"] / den, 2)
                                    if item["score"] is not None
                                    and item["decision_status"] == "tested" and den else None)
        cat_scores[cid] = cscore
        categories.append({"id": cid, "label": cat["label"], "weight": weights.get(cid, cat["weight"]),
                           "decision_status": ("tested" if configured_item_weight
                                               else "information_only"),
                           "score": cscore,
                           "coverage": (round(observed_den / configured_item_weight * 100)
                                        if configured_item_weight else None),
                           "available_item_weight": den,
                           "observed_item_weight": observed_den,
                           "configured_item_weight": configured_item_weight,
                           "missing_item_weight": configured_item_weight - den,
                           "items": items})

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

    # Compatibility field retained for existing JSON consumers. It is fixed at
    # zero so an AI/news review can never alter the tested verdict.
    modifier = 0
    preliminary = base

    # -- vetoes / gates namespace --
    ns = dict(flat)
    ns.update({f"category_{cid}": s for cid, s in cat_scores.items()})
    ns["target_upside"] = target_upside
    ns["upside_pct"] = fv["upside_pct"]
    ns["valuation_label"] = fv["valuation_label"]

    vetoes, context_warnings = [], []
    for v in cfg.scoring.get("vetoes", []):
        if _safe_eval(v["when"], ns) is not True:
            continue
        row = {"id": v["id"], "reason": v["reason"], "condition": v["when"],
               "result": True, "decision_status": v.get("decision_status", "tested")}
        if row["decision_status"] == "tested":
            row["effect"] = "Force Avoid"
            vetoes.append(row)
        else:
            row["effect"] = "Information-only safety warning; verdict unchanged"
            context_warnings.append(row)
    gates = []
    for g in cfg.scoring.get("soft_gates", []):
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
        "formula": "final score = round(weighted score from tested factors only)",
        "base_score": base,
        "base_numerator": round(bnum, 2),
        "available_category_weight": bden,
        "thesis_modifier": modifier,
        "preliminary_score": preliminary,
        "final_score": round(preliminary),
        "thresholds": {"buy": buy_b, "watch": watch_b},
        "score_band_verdict": score_band_verdict,
        "vetoes": vetoes,
        "context_warnings": context_warnings,
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
        "growth_qualification": growth_check,
        "research_metrics": {
            "debt_to_assets_pct": flat.get("debt_to_assets_pct"),
            "debt_to_assets_yago_pct": flat.get("debt_to_assets_yago_pct"),
            "debt_to_assets_change_yoy_pp": flat.get("debt_to_assets_change_yoy_pp"),
        },
        "vetoes": vetoes, "context_warnings": context_warnings, "soft_gates": gates,
        "coverage_pct": 100 if bden else 0,
        "factor_coverage_pct": round(
            sum(c["weight"] * c["coverage"] for c in categories
                if c["decision_status"] == "tested") /
            sum(c["weight"] for c in categories
                if c["decision_status"] == "tested"), 1)
            if any(c["decision_status"] == "tested" for c in categories) else 0,
        "decision_trace": decision_trace,
    }
