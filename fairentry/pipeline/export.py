"""Build the JSON the UI reads. One record per ticker (its qualifying
strategies), scored under the strategy preset, mapped to the UI's drill-down
shape (categories/items with actual/expected/rule/score, valuation, verdict).
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from ..analytics.breakout_setup import build_context as build_breakout_setup
from ..analytics.chart_history import write_chart_files
from ..analytics.demand_momentum import build_context as build_demand_momentum
from ..analytics.high_conviction import build_high_conviction_research
from ..analytics.valuation_context import build_valuation_context
from ..alerts import strong_business_wma_candidates, wma_alerts
from ..scoring.engine import sector_medians, score_ticker
from ..scoring.targets import build_target_plan
from ..screeners import REGISTRY as SCREENERS
from ..backtest.universe import deduplicate_issuers
from ..qualitative import normalize_observation

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "web" / "data" / "board.json"


def _preset_weights(cfg, strategy_key):
    presets = cfg.scoring.get("presets", {})
    name = cfg.defaults.get("strategy_presets", {}).get(strategy_key)
    return presets.get(name)


def _cat_score(rec, cat_id):
    for c in rec.get("categories", []):
        if c.get("id") == cat_id:
            return c.get("score")
    return None


def _metric_value(rec, metric_id):
    for c in rec.get("categories", []):
        for i in c.get("items", []):
            if i.get("metric") == metric_id:
                return i.get("actual")
    return None


def _quality_label(score):
    if score is None:
        return None
    if score >= 85:
        return ["Quality: excellent", "good"]
    if score >= 70:
        return ["Quality: strong", "good"]
    if score >= 55:
        return ["Quality: solid", "info"]
    if score >= 40:
        return ["Quality: mixed", "warn"]
    return ["Quality: weak", "bad"]


def _growth_label(rec):
    rev = _metric_value(rec, "rev_growth_qoq")
    if isinstance(rev, (int, float)):
        style = "good" if rev >= 15 else "info" if rev >= 5 else "warn" if rev >= 0 else "bad"
        return [f"Growth {rev:+.0f}%", style]
    score = _cat_score(rec, "growth")
    if score is None:
        return None
    if score >= 75:
        return ["Growth: strong", "good"]
    if score >= 55:
        return ["Growth: steady", "info"]
    if score >= 40:
        return ["Growth: slow", "warn"]
    return ["Growth: weak", "bad"]


def _entry_label(rec):
    verdict = rec.get("verdict")
    fv = rec.get("valuation", {})
    gates = {g.get("id") for g in rec.get("soft_gates", [])}
    if verdict == "Avoid" or rec.get("vetoes"):
        return ["Entry: avoid", "bad"]
    if "expensive" in gates or fv.get("valuation_label") == "expensive":
        return ["Entry: stretched", "warn"]
    if "upside_below_target" in gates:
        return ["Entry: thin upside", "warn"]
    if "survival_floor" in gates:
        return ["Entry: risky", "bad"]
    price, buy_zone = rec.get("price"), fv.get("buy_zone")
    if verdict == "Buy":
        if price and buy_zone and price <= buy_zone:
            return ["Entry: buy zone", "good"]
        return ["Entry: acceptable", "info"]
    if price and buy_zone and price > buy_zone:
        return ["Entry: pullback", "warn"]
    return ["Entry: watch", "warn"]


def _labels(rec):
    out = []
    country = (rec.get("country") or "").strip()
    if country and country.lower() not in {"usa", "us", "united states", "united states of america"}:
        out.append([country, "info"])
    for label in (_quality_label(_cat_score(rec, "quality")), _growth_label(rec), _entry_label(rec)):
        if label:
            out.append(label)
    up = rec["valuation"]["upside_pct"]
    if up is not None:
        out.append([f"Valuation upside {'+' if up >= 0 else ''}{up:.0f}%", "good" if up >= 20 else "warn" if up >= 0 else "bad"])
    out.append([rec["valuation"]["valuation_label"], "good" if rec["valuation"]["valuation_label"] == "cheap"
                else "bad" if rec["valuation"]["valuation_label"] == "expensive" else "info"])
    for v in rec["vetoes"]:
        out.append([v["reason"], "bad"])
    return out[:5]


def _action(rec):
    v = rec.get("display_verdict", rec["verdict"])
    if v == "Avoid":
        return {"action": "Avoid", "size": "—", "entry": (rec["vetoes"][0]["reason"] if rec["vetoes"]
                else "Weak score."), "add": "—", "stop": "—", "review": "—"}
    if v in {"Buy", "Quant Buy"}:
        return {"action": "Quant Buy" if v == "Quant Buy" else "Buy Now", "size": "3%", "entry": "Clears the quantitative gates.",
                "add": "On confirmation.", "stop": "Thesis kill-switch (reasoning layer, pending).",
                "review": "Next earnings"}
    return {"action": "Watch", "size": "starter", "entry": (rec["soft_gates"][0]["reason"] if rec["soft_gates"]
            else "Not yet actionable."), "add": "—", "stop": "—", "review": "—"}


def _card_summary(rec, thesis, strategy_key):
    """Small, plain-English board summary; full evidence remains in the drawer."""
    reasons, risks = [], []

    def add(target, text):
        text = str(text or "").strip().rstrip(".")
        if text and text.lower() not in {value.lower() for value in target}:
            target.append(text + ".")

    growth_check = rec.get("growth_qualification") or {}
    if growth_check.get("qualified"):
        add(reasons, growth_check.get("explanation"))

    priority = {
        "growth": 0, "catalysts": 1, "valuation": 2, "confirmation": 3,
        "quality": 4, "survival": 5, "risk": 6,
    }
    evidence = []
    for category in rec.get("categories", []):
        for item in category.get("items", []):
            score = item.get("score")
            if (item.get("decision_status", "tested") == "tested"
                    and isinstance(score, (int, float))):
                evidence.append((priority.get(category["id"], 9), score, item))

    def item_text(item):
        actual = item.get("actual")
        if isinstance(actual, (int, float)):
            if item.get("metric") in {
                "rev_growth_qoq", "eps_growth_next_y", "gross_margin", "oper_margin",
                "roic", "share_count_yoy", "intrinsic_gap_pct", "inst_trans",
                "short_float",
            }:
                actual = f"{actual:+.1f}%"
            elif item.get("metric") == "debt_eq":
                actual = f"{actual:.1f} times equity"
        return f"{item['label']}: {actual}" if actual is not None else item["label"]

    used_reason_categories = set()
    for category_rank, score, item in sorted(evidence, key=lambda row: (row[0], -row[1])):
        if score < 55 or len(reasons) >= 3:
            continue
        if category_rank in used_reason_categories:
            continue
        add(reasons, item_text(item))
        used_reason_categories.add(category_rank)

    for veto in rec.get("vetoes", []):
        add(risks, veto.get("reason"))
    for warning in rec.get("context_warnings", []):
        add(risks, "Current warning (information only): " + warning.get("reason", ""))
    for gate in rec.get("soft_gates", []):
        add(risks, gate.get("reason"))
    for _, score, item in sorted(evidence, key=lambda row: (row[1], row[0])):
        if score >= 45 or len(risks) >= 3:
            continue
        add(risks, item_text(item))

    return {
        "strategy": "Deep Value" if strategy_key == "deep_value" else "Quality Growth",
        "holding_period": "1–3 years" if strategy_key == "deep_value" else "2–5 years",
        "strongest_reasons": reasons[:3],
        "largest_risks": risks[:3],
    }


def _export_categories(rec, breakout):
    """Export the backend arithmetic plus the raw breakout evidence behind any
    computed factor. The UI renders this object; it never recreates provenance."""
    evidence_by_metric = {
        f.get("scoring_metric"): f for f in ((breakout or {}).get("factors") or [])
        if f.get("scoring_metric")
    }
    categories = []
    for category in rec["categories"]:
        items = []
        for item in category["items"]:
            evidence = evidence_by_metric.get(item["metric"], {})
            items.append({
                "id": item["id"], "metric": item["metric"], "label": item["label"],
                "weight": item["weight"], "score": item["score"],
                "status": item.get("status"),
                "decision_status": item.get("decision_status", "tested"),
                "contribution": item.get("contribution"),
                "actual": "n/a" if item["actual"] is None else str(item["actual"]),
                "raw_actual": evidence.get("actual"),
                "expected": evidence.get("expected") or item["expected"],
                "definition": item.get("definition", ""),
                "rule": item["rule"],
                "formula": evidence.get("formula") or item.get("formula", ""),
                "evidence": evidence.get("evidence"),
                "source": evidence.get("source") or item["source"] or "-",
                "observed_at": evidence.get("observed_at"),
                "fetched_at": item.get("fetched_at") or "-",
                "calculation_version": evidence.get("calculation_version"),
            })
        categories.append({
            "id": category["id"], "label": category["label"],
            "score": category["score"], "weight": category["weight"],
            "decision_status": category.get("decision_status", "tested"),
            "coverage": category.get("coverage"),
            "contribution": category.get("contribution"),
            "available_item_weight": category.get("available_item_weight"),
            "configured_item_weight": category.get("configured_item_weight"),
            "missing_item_weight": category.get("missing_item_weight"),
            "items": items,
        })
    return categories


# ---------------------------------------------------------------------------
# Demand & Momentum — CONTEXT ONLY.
# This is a human-readable read of "is demand growing / is money rotating in",
# built purely from data already in the store. It is deliberately NOT part of
# the score and does NOT influence Buy / Watch / Avoid — those come only from the
# config-driven, backtest-verifiable scoring model. Anything we can't verify in
# the backtest stays out of the verdict and lives here as context instead.
# ---------------------------------------------------------------------------
def _dm_num(mt, k):
    v = mt.get(k, {})
    v = v.get("value") if isinstance(v, dict) else v
    return v if isinstance(v, (int, float)) else None


def demand_momentum(mt: dict) -> dict:
    """Informational only. Returns {demand, momentum} each with a label, a
    one-line read, and the evidence numbers behind it. Never feeds the score."""
    rev = _dm_num(mt, "rev_growth_qoq")          # sales growth Q/Q
    epsn = _dm_num(mt, "eps_growth_next_y")       # forward EPS growth estimate
    perf = _dm_num(mt, "perf_year")               # 1-year price performance
    relv = _dm_num(mt, "rel_volume")              # relative volume (activity)
    revs = _dm_num(mt, "estimate_revision_score")  # 0-100, >50 = targets rising
    rec_ = _dm_num(mt, "analyst_recom")           # 1=Strong Buy .. 5=Sell

    # ---- Demand: is the business winning growth, and are estimates rising? ----
    d_ev = []
    if rev is not None:
        d_ev.append(f"Sales {rev:+.0f}% q/q")
    if epsn is not None:
        d_ev.append(f"EPS est next yr {epsn:+.0f}%")
    if revs is not None:
        d_ev.append("analyst targets " + ("rising" if revs >= 55 else "falling" if revs <= 45 else "flat"))
    strong = ((rev is not None and rev >= 15) or (epsn is not None and epsn >= 20)) \
        and (revs is None or revs >= 50)
    soft = (rev is not None and rev < 0) and (epsn is None or epsn < 5)
    d_label = "strong" if strong else "soft" if soft else "steady" if d_ev else "n/a"
    d_read = {"strong": "Demand growing and expectations holding up.",
              "steady": "Moderate demand; nothing decisive either way.",
              "soft": "Demand shrinking — top line under pressure.",
              "n/a": "Not enough data to read demand."}[d_label]

    # ---- Momentum: is money rotating into the stock right now? ----
    m_ev = []
    if perf is not None:
        m_ev.append(f"1-yr {perf:+.0f}%")
    if relv is not None:
        m_ev.append(f"rel. volume {relv:.1f}x")
    if rec_ is not None:
        m_ev.append("analyst consensus " + ("Buy" if rec_ <= 2 else "Sell" if rec_ >= 3.5 else "Hold"))
    rotating = (perf is not None and perf >= 15) and (relv is None or relv >= 1.0)
    outfav = (perf is not None and perf < -10)
    m_label = "rotating in" if rotating else "out of favor" if outfav else "neutral" if m_ev else "n/a"
    m_read = {"rotating in": "Uptrend with active interest — money is showing up.",
              "neutral": "No clear accumulation or distribution.",
              "out of favor": "Downtrend — money is leaving, not arriving.",
              "n/a": "Not enough data to read momentum."}[m_label]

    return {
        "demand": {"label": d_label, "read": d_read, "evidence": d_ev},
        "momentum": {"label": m_label, "read": m_read, "evidence": m_ev},
        "disclaimer": "Context only — not part of the score. Does not affect the "
                      "Buy / Watch / Avoid verdict.",
    }


def _map(rec, strategies, strategy_key):
    fv = rec["valuation"]
    th = rec.get("_thesis")
    if th:
        # UI's situationHTML reads arrays: [reason, status, severity, temp/struct, duration, evidence]
        situation = [[s.get("reason", ""), s.get("status", "active"), s.get("severity", "medium"),
                      th.get("temporary_vs_structural", "unknown"),
                      th.get("expected_timeframe", ""), s.get("evidence", "")]
                     for s in (th.get("situation") or [])]
        news = [{"date": n.get("date", ""), "headline": n.get("headline", ""),
                 "source": n.get("source", ""), "url": n.get("url", ""),
                 "categories": n.get("categories", [])}
                for n in (th.get("_news") or [])]
        watchlist = [{"name": w.get("name", ""), "type": w.get("type", ""),
                      "where": w.get("where", ""), "why": w.get("why", "")}
                     for w in (th.get("watchlist_sources") or []) if w.get("name")]
        thesis = {"type": "recovery" if strategy_key == "deep_value" else "growth",
                  "score": th.get("thesis_score", 50), "label": th.get("temporary_vs_structural", "—"),
                  "summary": th.get("summary", ""), "situation": situation,
                  "kill": th.get("kill_switch", ""), "provider": th.get("_provider", "—"),
                  "reviewed_at": th.get("_reviewed_at"),
                  "news": news, "watchlist": watchlist}
    else:
        thesis = {"type": "recovery" if strategy_key == "deep_value" else "growth",
                  "score": 50, "label": "AI review pending",
                  "summary": "Scored on the numbers only — the AI deep-dive (news, "
                             "recovery thesis, and sources to follow) runs on a focused "
                             "weekly shortlist, so this name doesn't have one yet.",
                  "situation": [], "kill": "", "provider": "—", "reviewed_at": None,
                  "news": [], "watchlist": []}
    thesis["drivers"] = th.get("thesis_drivers", []) if th else []
    thesis["leading_indicators"] = th.get("leading_indicators", []) if th else []
    thesis["risks"] = th.get("thesis_risks", []) if th else []
    thesis["capital_allocation"] = th.get("capital_allocation", []) if th else []
    thesis["driver_history"] = {
        "point_in_time_ready": False,
        "backtest_eligible": False,
        "reason": "Structured thesis drivers remain context-only until dated histories exist."
    }
    breakout = deepcopy(rec.get("_breakout_setup"))
    if breakout:
        qualitative = []
        raw_qualitative = list(((th or {}).get("breakout_evidence") or []))
        used = set()
        standard_qualitative = (
            ("management_execution", "Management Execution", "management",
             "specific, dated evidence that management delivered against guidance or a stated recovery/growth plan"),
            ("catalyst_visibility", "Catalyst Visibility", "catalyst",
             "a specific, dated catalyst with a credible path to affect business results or market expectations"),
            ("policy_impact", "Government Policy Impact", "catalyst",
             "a specific government action from a named source with a direct path to affect this company or sector"),
            ("investment_expansion", "Investment and Expansion", "management",
             "a dated expansion, contract, capacity investment, or acquisition with a measurable path to future revenue or profit"),
            ("earnings_review", "Earnings, Guidance and Transcript Review", "earnings",
             "dated results and guidance compared with expectations and management's prior commitments; unknown when no transcript evidence is supplied"),
            ("operational_disruption", "Operational Disruption", "operations",
             "specific shutdown, shortage, regulatory or technology-transition evidence with an identified business effect"),
            ("external_events", "External Events", "external",
             "specific company exposure to a natural disaster, war, pandemic or geopolitical event"),
        )

        reserved_ids = {row[0] for row in standard_qualitative}

        def matching_evidence(factor_id, subgroup):
            # Preserve the four named transparency rows even if the provider
            # returns them in a different order.
            for index, evidence in enumerate(raw_qualitative):
                if index in used:
                    continue
                if evidence.get("id") == factor_id:
                    used.add(index)
                    return evidence
            # Older cached reviews may contain only generic management or
            # catalyst rows. Let those populate the two generic rows, but never
            # relabel a named policy/expansion row as something else.
            if factor_id in {"management_execution", "catalyst_visibility"}:
                for index, evidence in enumerate(raw_qualitative):
                    if index in used or evidence.get("id") in reserved_ids:
                        continue
                    if evidence.get("group") == subgroup:
                        used.add(index)
                        return evidence
            return {}

        ordered_evidence = []
        for factor_id, label, subgroup, expected in standard_qualitative:
            evidence = matching_evidence(factor_id, subgroup)
            ordered_evidence.append((factor_id, label, subgroup, expected, evidence))
        for index, evidence in enumerate(raw_qualitative):
            if index not in used:
                ordered_evidence.append((
                    evidence.get("id") or "qualitative_evidence",
                    evidence.get("label", "Qualitative evidence"),
                    evidence.get("group", "catalyst"),
                    "specific, dated evidence supporting durable continuation",
                    evidence,
                ))

        for factor_id, label, subgroup, expected, ev in ordered_evidence:
            status = ev.get("status", "unknown")
            if status not in {"satisfied", "partial", "failed", "contradicted", "unknown"}:
                status = "unknown"
            qualitative.append({
                "id": factor_id,
                "group": "human_and_catalyst",
                "subgroup": subgroup,
                "label": label,
                "status": status,
                "actual": ev.get("evidence") or "n/a",
                "expected": expected,
                "score_metric": None,
                "formula": "Information only; excluded from the tested score and verdict",
                "evidence": ev.get("evidence") or "No specific evidence was supplied; status remains Unknown.",
                "source": ev.get("source") or (th.get("_provider", "-") if th else "AI review pending"),
                "observed_at": ev.get("date", ""),
                "calculation_version": "thesis_v6",
                "modifier_effect": "Decision effect: none",
                **normalize_observation(
                    ev, category=("risk" if subgroup in {"contradiction", "operations", "external"}
                                  else "growth" if subgroup == "earnings" else "catalysts"),
                    subcategory=subgroup,
                ),
            })
        breakout["factors"] = (breakout.get("factors") or []) + qualitative
        statuses = ("satisfied", "partial", "failed", "contradicted", "unknown")
        breakout["counts"] = {k: sum(1 for f in breakout["factors"] if f.get("status") == k)
                              for k in statuses}
        breakout["counts"]["total"] = len(breakout["factors"])
        breakout["qualitative_note"] = (
            "Qualitative and human evidence is information only. It does not change "
            "the tested score, verdict, or deterministic breakout label."
        )
    qualitative_by_category = {category: [] for category in (
        "quality", "survival", "growth", "valuation", "confirmation", "catalysts", "risk"
    )}
    for factor in ((breakout or {}).get("factors") or []):
        if factor.get("quantifiable") is False:
            category = factor.get("category")
            if category in qualitative_by_category:
                qualitative_by_category[category].append(factor)
    rec["qualitative_context"] = {
        "score_effect": 0,
        "verdict_effect": "none",
        "policy": "Information only; displayed inside the existing category and excluded from scoring.",
        "categories": qualitative_by_category,
    }
    high_conviction = build_high_conviction_research(
        verdict=rec.get("verdict"), price=rec.get("price"), vetoes=rec.get("vetoes"),
        valuation_agreement=fv.get("method_agreement"),
        business_durability=rec.get("_business_durability"),
        stress_resilience=rec.get("_stress_resilience"),
        entry_exit_evidence=rec.get("_entry_exit_evidence"),
        qualitative_context=rec["qualitative_context"],
    )
    financial_strength = _cat_score(rec, "survival")
    growth_score = _cat_score(rec, "growth")
    expected_eps_growth = _metric_value(rec, "eps_growth_next_y")
    is_buy = rec.get("verdict") == "Buy"
    no_hard_veto = not bool(rec.get("vetoes"))
    financial_strength_qualified = bool(
        is_buy and no_hard_veto
        and isinstance(financial_strength, (int, float))
        and financial_strength >= 70
    )
    confidence_tier = {
        "id": ("financial_strength_qualified" if financial_strength_qualified else
               "standard_buy" if is_buy else "not_applicable"),
        "label": ("Financial-strength qualified Buy" if financial_strength_qualified else
                  "Standard Buy" if is_buy else "Not a Buy candidate"),
        "eligible": False,
        "passes_financial_strength_rule": financial_strength_qualified,
        "score_effect": 0,
        "verdict_effect": "none",
        "policy_version": "confidence_tier_v2_revalidated",
        "basis": (
            "Buy verdict, Financial Strength at least 70, and no hard veto. "
            "The full point-in-time replay did not validate this as High-confidence; "
            "it remains information-only and does not change the verdict."
        ),
        "historical_evidence": {
            "completed_episodes": 278,
            "successes": 123,
            "failures": 155,
            "observed_success_rate_pct": 44.2,
            "confidence_interval_90_pct": [39.4, 49.2],
            "target": "+30% within one year",
            "comparison_all_buy_rate_pct": 43.0,
            "validated": False,
            "replay": "112 quarterly point-in-time cohorts, 1998-2026, $10M floor",
        },
        "inputs": {
            "financial_strength": {"value": financial_strength, "minimum": 70,
                                   "required": True,
                                   "passes": isinstance(financial_strength, (int, float))
                                   and financial_strength >= 70},
            "growth_score": {"value": growth_score, "required": False,
                             "reason": "The current Growth score did not improve historical precision when added to Financial Strength."},
            "expected_eps_growth": {
                "value_pct": expected_eps_growth, "confirmation_threshold_pct": 15,
                "passes_provisional_confirmation": (
                    isinstance(expected_eps_growth, (int, float))
                    and expected_eps_growth >= 15),
                "decision_status": "information_only",
                "required": False,
                "reason": "Provisional until point-in-time forecast history passes walk-forward validation.",
            },
            "no_hard_veto": {"value": no_hard_veto, "required": True,
                             "passes": no_hard_veto},
        },
    }
    display_verdict = ("Quant Buy" if rec["verdict"] == "Buy" and not th else rec["verdict"])
    rec["display_verdict"] = display_verdict
    # Growth-entry plan (for Quality Growth names): fair-price cases + entry zone
    # + upside now vs at the entry zone + the buy-now/wait decision.
    growth_entry = None
    if "growth" in strategies:
        base, buyz, price = fv["fair_base"], fv["buy_zone"], rec["price"]
        up_now = round(fv["upside_pct"]) if fv["upside_pct"] is not None else None
        up_entry = round((base / buyz - 1) * 100) if (base and buyz) else None
        ev = (th.get("entry_view") if th else None)
        if not ev:  # deterministic fallback from verdict + price-vs-zone
            if rec["verdict"] == "Buy":
                ev = "buy_now"
            elif base and price and buyz and price > buyz:
                ev = "wait_for_pullback"
            else:
                ev = "watch"
        growth_entry = {
            "price": price,
            "fair_conservative": fv["fair_low"], "fair_base": base, "fair_optimistic": fv["fair_high"],
            "buy_below": buyz, "mos_pct": fv["margin_of_safety_pct"],
            "upside_at_current": up_now, "upside_at_entry": up_entry,
            "entry_view": ev,
            "required_growth": (th.get("required_growth_to_justify_price") if th else None),
            "durability": (th.get("durability") if th else None),
            "kill": (th.get("kill_switch") if th else ""),
        }
    # C4 labels (req §9): holding horizon, expansion, followed-source count.
    extra = []
    tf = th.get("expected_timeframe") if th else None
    if tf:
        extra.append(["Horizon: " + tf, "info"])
    else:
        hold = {"deep_value": "Hold 1–3 yrs", "quality_growth": "Hold 2–5 yrs"}.get(strategy_key)
        if hold:
            extra.append([hold, "info"])
    kc = (th.get("key_catalyst", "") if th else "").lower()
    newscats = {c for n in thesis["news"] for c in (n.get("categories") or [])}
    if any(w in kc for w in ("expan", "new market", "launch", "capacity", "customer")) \
            or "product" in newscats or "m&a" in newscats:
        extra.append(["Expanding", "good"])
    nsrc = len(thesis["watchlist"])
    if nsrc:
        extra.append([f"{nsrc} sources to follow", "info"])
    labels = (_labels(rec) + extra)[:6]

    action = _action(rec)
    card_summary = _card_summary(rec, thesis, strategy_key)
    return {
        "ticker": rec["ticker"], "company": rec["company"], "sector": rec["sector"],
        "country": rec.get("country"), "strategy": strategies, "price": rec["price"],
        "score": rec["score"], "verdict": rec["verdict"],
        "display_verdict": display_verdict,
        "model_verdict": rec["verdict"],
        "confidence_tier": confidence_tier,
        "base_score": rec["base_score"], "thesis_modifier": rec["thesis_modifier"],
        "preliminary": rec["preliminary"], "coverage_pct": rec.get("coverage_pct"),
        "coverage_confidence": rec.get("coverage_confidence"),
        "decision_trace": dict(rec.get("decision_trace") or {},
                               thesis_evidence=qualitative if breakout else []),
        "growth_qualification": rec.get("growth_qualification"),
        "debt_direction": {
            "current_pct": (rec.get("research_metrics") or {}).get("debt_to_assets_pct"),
            "one_year_ago_pct": (rec.get("research_metrics") or {}).get("debt_to_assets_yago_pct"),
            "change_pp": (rec.get("research_metrics") or {}).get("debt_to_assets_change_yoy_pp"),
            "decision_status": "testing",
            "decision_effect": "None until historical validation passes",
        },
        "cats": [{"id": c["id"], "label": c["label"], "score": c["score"] or 0,
                  "items": [{"label": i["label"], "weight": i["weight"], "score": i["score"] or 0,
                             "actual": (rec.get("_sm_flow") if i.get("id") == "smart_money" and rec.get("_sm_flow")
                                        else ("n/a" if i["actual"] is None else str(i["actual"]))),
                             "expected": i["expected"], "rule": i["rule"],
                             "source": i["source"] or "—"} for i in c["items"] if i["score"] is not None]}
                 for c in rec["categories"] if c["score"] is not None],
        "categories": _export_categories(rec, breakout),
        "thesis": thesis,
        "valuation": {"low": fv["fair_low"], "base": fv["fair_base"], "high": fv["fair_high"],
                      "upside": round(fv["upside_pct"]), "label": fv["valuation_label"],
                      "methods": fv.get("methods", []),
                      "excluded_methods": fv.get("excluded_methods", []),
                      "dispersion_pct": fv.get("dispersion_pct"),
                      "method_agreement_pct": fv.get("method_agreement_pct"),
                      "method_agreement": fv.get("method_agreement"),
                      "confidence": fv.get("valuation_confidence"),
                      "warnings": fv.get("warnings", [])},
        "valuation_context": rec.get("_valuation_context"),
        "growth_entry": growth_entry,
        "target_plan": rec.get("_target_plan"),
        "demand_momentum": rec.get("_demand_momentum"),
        "breakout_setup": breakout,
        "entry_exit_evidence": rec.get("_entry_exit_evidence"),
        "business_durability": rec.get("_business_durability"),
        "stress_resilience": rec.get("_stress_resilience"),
        "high_conviction_research": high_conviction,
        "qualitative_context": rec["qualitative_context"],
        "vetoes": [v["reason"] for v in rec["vetoes"]],
        "context_warnings": rec.get("context_warnings", []),
        "soft": [g["reason"] for g in rec["soft_gates"]],
        "soft_gates": [g["reason"] for g in rec["soft_gates"]],
        "labels": labels, "action": action, "action_plan": action,
        "card_summary": card_summary,
        # informational only — see demand_momentum(); NOT used in the score/verdict
        "context": rec.get("_context"),
    }


def _rescore_with_thesis(cfg, secs, store, rec, th, settings, med):
    """Attach an AI review without changing the deterministic verdict."""
    r2 = dict(rec)
    r2["_thesis"] = th
    r2["thesis_modifier"] = 0
    if r2.get("decision_trace"):
        r2["decision_trace"] = dict(r2["decision_trace"], thesis_modifier=0)
    return r2, 0


def _apply_reasoning(cfg, secs, store, recs, settings, med, cap=30):
    """Run the reasoning layer (a real LLM call) on the names most worth an AI
    read: every Buy / Watch candidate, highest tested score first, capped. The
    resulting review is information only and cannot change the verdict.

    Each successful thesis is persisted to the store (thesis_results) so later
    deterministic builds can re-attach it. Circuit-breaks if the provider is
    unavailable OR only the offline stub is present (no DEEPSEEK_API_KEY /
    balance) — so we never stall the run, and never attach a placeholder 'review'
    that isn't a real one (the name stays honestly 'not reviewed yet')."""
    from ..reasoning.thesis import build_thesis
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    watch_b = cfg.verdict_bands["watch"]
    shortlist = sorted(
        [r for r in recs if r["preliminary"] >= watch_b and not r["vetoes"]],
        key=lambda r: -r["preliminary"])[:cap]
    provider_down = False
    used = 0
    for r in shortlist:
        primary = r["_primary"]
        if provider_down:
            continue
        th = build_thesis(secs[r["ticker"]], store.metrics_for(r["ticker"]),
                          {"verdict": r["verdict"], "preliminary": r["preliminary"]}, primary)
        if th.get("_provider") == "unavailable" or th.get("_stub"):
            provider_down = True   # no real provider — leave the rest 'not reviewed'
            continue
        th["_reviewed_at"] = now
        r2, mod = _rescore_with_thesis(cfg, secs, store, r, th, settings, med)
        recs[recs.index(r)] = r2
        store.set_thesis_result(r["ticker"], primary, th.get("thesis_score", 50),
                                mod, json.dumps(th), th.get("_provider", "?"), now)
        used += 1
    return {"shortlist": len(shortlist), "reasoned": used, "provider_down": provider_down}


def _review_age_days(run_at: str) -> float:
    try:
        dt = datetime.fromisoformat(run_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    except Exception:
        return 1e9


def _attach_stored_theses(cfg, secs, store, recs, settings, med, max_age_days=21):
    """Re-attach the most recent stored thesis to any rec that doesn't already
    have a fresh one, so an AI review persists across the deterministic (non-
    --reason) builds between weekly reasoning runs. Skips stale reviews."""
    stored = store.latest_theses()
    attached = 0
    for i, rec in enumerate(recs):
        if rec.get("_thesis"):
            continue                                  # got a fresh LLM thesis this build
        row = stored.get(rec["ticker"])
        if not row or not row.get("thesis_json"):
            continue
        if _review_age_days(row["run_at"]) > max_age_days:
            continue                                  # too old to trust — leave 'pending'
        try:
            th = json.loads(row["thesis_json"])
        except Exception:
            continue
        th["_reviewed_at"] = row["run_at"]
        r2, _ = _rescore_with_thesis(cfg, secs, store, rec, th, settings, med)
        recs[i] = r2
        attached += 1
    return attached


def _estimate_revisions(store, lookback_days=45):
    """Analyst-target *revision* signal, computed from metrics_history: are the
    mean analyst targets being raised or cut over the last ~45 days? Rising
    targets = positive revisions. Graceful: a ticker with <2 snapshots gets no
    score (item drops), so this activates as daily history accumulates — the
    same 'mechanism now, value later' pattern as the backtest.
    Returns {ticker: score 0-100}."""
    rows = store.con.execute(
        "SELECT ticker, substr(fetched_at,1,10) d, value_num v FROM metrics_history "
        "WHERE field_id='target_price' AND value_num IS NOT NULL "
        "GROUP BY ticker, d ORDER BY ticker, d")
    series: dict[str, list] = {}
    for r in rows:
        series.setdefault(r["ticker"], []).append((r["d"], r["v"]))
    out = {}
    for t, pts in series.items():
        pts = [p for p in pts if p[1] and p[1] > 0]
        if len(pts) < 2:
            continue
        # earliest within the lookback window vs the latest
        latest_d = pts[-1][0]
        window = [p for p in pts if _within(p[0], latest_d, lookback_days)]
        if len(window) < 2:
            window = pts[-2:]
        first, last = window[0][1], window[-1][1]
        chg = (last / first - 1) * 100 if first else 0.0
        out[t] = int(max(0, min(100, round(50 + max(-40, min(40, chg * 3))))))
    return out


def _within(d, ref, days):
    from datetime import date
    try:
        return (date.fromisoformat(ref) - date.fromisoformat(d)).days <= days
    except Exception:
        return True


def _fresh_price(metric, limit_hours=8, now=None):
    """Return whether a live-decision price is present and inside its TTL."""
    if not isinstance(metric, dict) or not isinstance(metric.get("value"), (int, float)):
        return False, "Price unavailable"
    try:
        fetched = datetime.fromisoformat(str(metric.get("fetched_at", "")).replace("Z", "+00:00"))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        age_h = ((now or datetime.now(timezone.utc)) - fetched.astimezone(timezone.utc)).total_seconds() / 3600
    except (TypeError, ValueError):
        return False, "Price timestamp unavailable"
    if age_h > limit_hours:
        return False, f"Price unavailable/stale ({age_h:.1f}h old; limit {limit_hours:g}h)"
    return True, None


def build_board(cfg, store, settings=None, reason=False) -> dict:
    settings = settings or {"margin_of_safety_pct": 15, "target_upside_pct": 30}
    med = sector_medians(cfg, store)
    secs = {x["ticker"]: x for x in store.active_securities()}
    price_limit_h = float(cfg.field("price").get("freshness_limit_h", 8))
    price_issues = []
    for ticker in list(secs):
        metric = store.metrics_for(ticker).get("price")
        fresh, issue = _fresh_price(metric, price_limit_h)
        if not fresh:
            price_issues.append({"ticker": ticker, "status": "Price unavailable/stale",
                                 "reason": issue})
            # A stale/missing execution price can never enter screeners, scoring,
            # a Buy verdict, or the high-confidence overlay.
            del secs[ticker]
    revisions = _estimate_revisions(store)
    for t, sc in revisions.items():
        store.set_metric(t, "estimate_revision_score", sc, "computed")
    store.commit()

    quals: dict[str, list[str]] = {}
    for sid, mod in SCREENERS.items():
        for t in secs:
            ok, _ = mod.passes(store.metrics_for(t))
            if ok:
                quals.setdefault(t, []).append(mod.STRATEGY)
                store.set_screen_result(t, sid, True, {})
    store.commit()

    issuer_candidates = [
        {"sec": secs[t], "metrics": store.metrics_for(t)} for t in quals
    ]
    issuer_representatives, excluded_share_classes = deduplicate_issuers(
        issuer_candidates
    )
    kept_tickers = {item["sec"]["ticker"] for item in issuer_representatives}
    quals = {ticker: strategies for ticker, strategies in quals.items()
             if ticker in kept_tickers}

    # Calculate one breakout evidence trace before scoring. Its individual
    # quantitative metrics feed existing categories; the trace itself powers the
    # existing breakout label and progressive-disclosure panel (no second score).
    breakout_inputs = []
    primary_by_ticker = {}
    for t, strategies in quals.items():
        primary = "deep_value" if "deepvalue" in strategies else "quality_growth"
        primary_by_ticker[t] = primary
        breakout_inputs.append(({
            "ticker": t,
            "sector": secs[t]["sector"],
            "_primary": primary,
        }, store.metrics_for(t)))
    breakout_context = build_breakout_setup(store, breakout_inputs)
    for ticker, context in breakout_context.items():
        for metric_id, value in (context.get("scoring_metrics") or {}).items():
            if value is not None:
                store.set_metric(ticker, metric_id, value, "FairEntry breakout_v2")
    store.commit()

    recs = []
    metrics_by_ticker = {}
    for t, strategies in quals.items():
        primary = primary_by_ticker[t]
        s = dict(settings)
        pw = _preset_weights(cfg, primary)
        if pw:
            s["weights"] = pw
        mt = store.metrics_for(t)
        metrics_by_ticker[t] = mt
        rec = score_ticker(cfg, secs[t], mt, med, s)
        rec["_primary"] = primary; rec["_strategies"] = strategies
        smf = mt.get("thirteenf_flow", {})
        rec["_sm_flow"] = smf.get("value") if isinstance(smf, dict) else None
        rec["_context"] = demand_momentum(mt)   # informational only — not scored
        rec["_valuation_context"] = build_valuation_context(secs[t], mt)
        rec["_breakout_setup"] = breakout_context.get(t)
        rec["_entry_exit_evidence"] = (
            (breakout_context.get(t) or {}).get("entry_exit_evidence")
        )
        rec["_business_durability"] = (
            (breakout_context.get(t) or {}).get("business_durability")
        )
        rec["_stress_resilience"] = (
            (breakout_context.get(t) or {}).get("stress_resilience")
        )
        recs.append(rec)

    reasoning_summary = {}
    if reason:
        reasoning_summary = _apply_reasoning(cfg, secs, store, recs, settings, med)
    # Always re-attach the most recent stored thesis to names not freshly
    # reasoned, so AI reads survive the deterministic builds between weekly runs.
    reattached = _attach_stored_theses(cfg, secs, store, recs, settings, med)

    stocks = []
    context_records = [(r, metrics_by_ticker.get(r["ticker"], {})) for r in recs]
    demand_context = build_demand_momentum(context_records)
    for rec in recs:
        rec["_target_plan"] = build_target_plan(
            rec, metrics_by_ticker.get(rec["ticker"], {}),
            minimum_upside_pct=settings.get("target_upside_pct", 30), maximum_upside_pct=100,
            expiry_days=365, historical=False)
        store.set_score_result(rec["ticker"], rec["_primary"], rec["base_score"],
                               rec["preliminary"], rec["verdict"], rec)
        rec["_demand_momentum"] = demand_context.get(rec["ticker"])
        rec["_breakout_setup"] = breakout_context.get(rec["ticker"])
        rec["_entry_exit_evidence"] = (
            (breakout_context.get(rec["ticker"]) or {}).get("entry_exit_evidence")
        )
        rec["_business_durability"] = (
            (breakout_context.get(rec["ticker"]) or {}).get("business_durability")
        )
        rec["_stress_resilience"] = (
            (breakout_context.get(rec["ticker"]) or {}).get("stress_resilience")
        )
        stocks.append(_map(rec, rec["_strategies"], rec["_primary"]))
    store.commit()

    # ---- AI-review status for the UI ----------------------------------------
    reviewed = [r for r in recs if r.get("_thesis")]
    review_dates = [r["_thesis"].get("_reviewed_at") for r in reviewed
                    if r["_thesis"].get("_reviewed_at")]
    ai_review = {
        "ran_llm": bool(reason),                         # did this build call the LLM
        "reasoned_now": reasoning_summary.get("reasoned", 0),
        "shortlist": reasoning_summary.get("shortlist", 0),
        "provider_down": reasoning_summary.get("provider_down", False),
        "reattached": reattached,                        # from stored theses
        "with_ai_read": len(reviewed),                   # names showing an AI read
        "candidates": len(recs),
        "last_review_at": max(review_dates) if review_dates else None,
        "last_review_age_days": (round(min(_review_age_days(d) for d in review_dates), 1)
                                 if review_dates else None),
    }

    stocks.sort(key=lambda r: -(r["cats"] and sum(c["score"] for c in r["cats"]) or 0))
    threshold = float(cfg.defaults.get("wma_alert_threshold_pct", 3.0))
    proximity_alerts = wma_alerts(stocks, metrics_by_ticker, threshold)
    strong_wma_candidates = strong_business_wma_candidates(
        stocks, metrics_by_ticker, threshold
    )
    return {"meta": {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "sectors": [s["id"] for s in cfg.enabled_sectors],
                     "config_version": cfg.scoring.get("version"), "count": len(stocks),
                     "issuer_deduplication": {
                         "enabled": True,
                         "policy": "primary_class_then_liquidity",
                         "excluded_share_classes": excluded_share_classes,
                     },
                     "reasoning": reasoning_summary,
                     "ai_review": ai_review,
                     "wma_alerts": proximity_alerts,
                     "strong_business_wma_candidates": strong_wma_candidates,
                     "strong_business_wma_rule": (
                         "Business Quality >=70, Financial Strength >=70, "
                         "Growth >=70, no tested veto; research-only"
                     ),
                     "wma_alert_threshold_pct": threshold,
                     "price_freshness": {
                         "limit_hours": price_limit_h,
                         "excluded_count": len(price_issues),
                         "issues": price_issues,
                     },
                     "preset_profiles": cfg.scoring.get("preset_profiles", {}),
                     "presets": cfg.scoring.get("presets", {}),
                     "default_weights": {cid: c["weight"] for cid, c in cfg.categories.items()},
                     # everything the UI needs to reproduce the tested verdict;
                     # AI/news has no score modifier.
                     "strategy_presets": cfg.defaults.get("strategy_presets", {}),
                     "verdict_bands": cfg.verdict_bands,
                     "confidence_policy": {
                         "version": "confidence_tier_v2_revalidated",
                         "high_confidence_rule": None,
                         "tested_rule": "Buy AND Financial Strength >= 70 AND no hard veto",
                         "validation_result": "not validated (44.2% vs 43.0% for all Buy episodes)",
                         "weight_changes": False,
                         "verdict_changes": False,
                         "eps_confirmation": "Expected next-year EPS growth >= 15% is provisional and information-only",
                     },
                     "thesis_modifier": [],
                     "factor_contract": [
                         {"category": cid, "category_label": category["label"],
                          "id": item["id"], "label": item["label"],
                          "decision_status": item.get("decision_status", "tested")}
                         for cid, category in cfg.categories.items()
                         for item in category["items"]
                     ]},
            "stocks": stocks}


def write_board(board: dict, path: Path = OUT):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path == OUT:
        chart_paths = write_chart_files(board.get("stocks") or [], path.parent / "charts")
        for stock in board.get("stocks") or []:
            ticker = str(stock.get("ticker", "")).upper()
            if ticker in chart_paths:
                stock["chart_path"] = chart_paths[ticker]
    path.write_text(json.dumps(board, indent=1, ensure_ascii=False), encoding="utf-8")
    return path
