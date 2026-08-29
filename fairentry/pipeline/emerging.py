"""Live adapter for the shared, research-only Emerging Research policy."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from ..emerging import evaluate_emerging_candidate
from .export import build_board


def _category_score(stock: dict, category_id: str):
    for category in stock.get("categories") or stock.get("cats") or []:
        if category.get("id") == category_id:
            value = category.get("score")
            return value if isinstance(value, (int, float)) else None
    return None


def classify_emerging_stock(stock: dict, metrics: dict, policy: dict) -> dict | None:
    """Adapt a mapped live stock into the shared research-rule contract."""
    valuation = stock.get("valuation") or {}
    agreement = valuation.get("method_agreement") or {}
    advol_metric = metrics.get("avg_dollar_volume", {})
    advol = advol_metric.get("value") if isinstance(advol_metric, dict) else advol_metric
    shadow_verdict = stock.get("model_verdict") or stock.get("verdict")
    alignment = (stock.get("entry_exit_evidence") or {}).get("entry_alignment")
    evaluated = evaluate_emerging_candidate({
        "avg_dollar_volume": advol,
        "business_quality": _category_score(stock, "quality"),
        "financial_strength": _category_score(stock, "survival"),
        "growth": _category_score(stock, "growth"),
        "valuation_upside_pct": valuation.get("upside"),
        "valuation_method_count": agreement.get("method_count") or len(
            valuation.get("methods") or []),
        "valuation_agreement": agreement.get("status"),
        "entry_alignment": alignment,
        "shadow_verdict": shadow_verdict,
        "no_hard_veto": not bool(stock.get("vetoes")),
    }, policy)
    if not evaluated["qualifies"]:
        return None

    labels = {"broad": "Emerging · Basic Match",
              "balanced": "Emerging · Strong Match",
              "selective": "Emerging · Strict Match"}
    highest = evaluated["highest_variant"]
    inputs = evaluated["inputs"]
    return {
        **evaluated,
        "status": highest,
        "label": labels[highest],
        "validation_label": "Research only · backtest advantage not proven",
        "official_buy": False,
        "source": "finviz_discovery",
        "source_description": (
            "Same point-in-time Finviz universe, monitored from $5M average daily "
            "dollar volume upward and separated into $5M-$10M, $10M-$20M, and $20M+ bands"
        ),
        "shadow_model_verdict": shadow_verdict,
        "shadow_score": stock.get("preliminary"),
        "checks": evaluated["variants"][highest]["checks"],
        "confirmation_checks": evaluated["variants"]["selective"]["checks"],
        "blockers": evaluated["variants"]["selective"]["failed_checks"],
        "graduation_rule": (
            "$5M-$10M names graduate into the official-liquidity universe at $10M; "
            "$10M-$20M names reach the positive liquidity-quality band at $20M. "
            "Neither transition automatically creates a Buy."
        ),
        "disclaimer": (
            "Discovery hypothesis only. It changes no category score, official "
            "Buy/Watch/Avoid verdict, portfolio position, or trading alert."
        ),
        "summary_values": {
            "business_quality": inputs["business_quality"],
            "financial_strength": inputs["financial_strength"],
            "growth": inputs["growth"],
            "median_valuation_upside_pct": inputs["valuation_upside_pct"],
            "average_daily_dollar_volume": inputs["avg_dollar_volume"],
            "valuation_agreement": inputs["valuation_agreement"],
            "valuation_method_count": inputs["valuation_method_count"],
            "valuation_spread_pct": agreement.get("dispersion_pct"),
            "entry_alignment": inputs["entry_alignment"],
        },
    }


def build_emerging_candidates(cfg, store, official_board=None) -> dict:
    """Compute and persist today's three Emerging Research variants."""
    policy = dict(cfg.sectors.get("emerging_candidates", {}) or {})
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    refreshed_at = store.universe_refreshed_at("finviz_discovery")
    if not policy.get("enabled", True):
        return {"stocks": [], "meta": {"enabled": False, "available": False}}
    if not refreshed_at:
        return {"stocks": [], "meta": {
            "enabled": True, "available": False,
            "reason": "Run a successful Finviz refresh to create the discovery snapshot.",
            "policy_version": policy.get("policy_version", "emerging_research_v2"),
        }}

    shadow_board = build_board(
        cfg, store, reason=False, source="finviz_discovery", persist_results=False)
    official_by = {stock["ticker"]: stock for stock in
                   ((official_board or {}).get("stocks") or [])}
    official_members = {row["ticker"] for row in store.active_securities("finviz")}
    selected, db_rows = [], []
    for original in shadow_board.get("stocks", []):
        stock = deepcopy(original)
        annotation = classify_emerging_stock(
            stock, store.metrics_for(stock["ticker"]), policy)
        if not annotation:
            continue
        official_stock = official_by.get(stock["ticker"])
        annotation["in_official_universe"] = stock["ticker"] in official_members
        annotation["official_verdict"] = (
            (official_stock or {}).get("model_verdict") if official_stock else None)
        stock["emerging_candidate"] = annotation
        stock["official_verdict"] = annotation["official_verdict"] or "None"
        stock["official_decision"] = bool(official_stock)
        selected.append(stock)
        values = annotation["summary_values"]
        db_rows.append({
            "ticker": stock["ticker"], "company": stock.get("company"),
            "sector": stock.get("sector"),
            "strategy": ",".join(stock.get("strategy") or []),
            "status": annotation["status"], "price": stock.get("price"),
            "shadow_score": stock.get("preliminary"),
            "shadow_verdict": annotation["shadow_model_verdict"],
            "financial_strength": values["financial_strength"],
            "avg_dollar_volume": values["average_daily_dollar_volume"],
            "source": annotation["source"],
            "policy_version": annotation["policy_version"],
            "evidence": annotation,
        })

    persistence = store.replace_emerging_candidates(
        db_rows, official_tickers=official_members, observed_at=generated_at)
    current = {row["ticker"]: row for row in store.emerging_candidates()}
    for stock in selected:
        row = current.get(stock["ticker"], {})
        stock["emerging_candidate"]["first_seen"] = row.get("first_seen")
        stock["emerging_candidate"]["last_seen"] = row.get("last_seen")
    rank = {"selective": 0, "balanced": 1, "broad": 2}
    selected.sort(key=lambda stock: (
        rank.get(stock["emerging_candidate"]["highest_variant"], 9),
        -(stock.get("preliminary") or 0), stock["ticker"]))
    counts = {variant: sum(variant in stock["emerging_candidate"]["matched_variants"]
                           for stock in selected)
              for variant in ("broad", "balanced", "selective")}
    bands = {band: sum(stock["emerging_candidate"]["liquidity_band"] == band
                       for stock in selected)
             for band in ("5m_to_10m", "10m_to_20m", "20m_plus")}
    return {"stocks": selected, "meta": {
        "enabled": True, "available": True, "generated_at": generated_at,
        "discovery_universe_refreshed_at": refreshed_at,
        "discovery_universe_count": len(store.active_securities("finviz_discovery")),
        "candidate_count": len(selected), "variant_counts": counts,
        "liquidity_band_counts": bands,
        "default_variant": policy.get("default_variant", "balanced"),
        "default_liquidity_band": policy.get("default_liquidity_band", "5m_to_20m"),
        "policy_version": policy.get("policy_version", "emerging_research_v2"),
        "policy": policy, "persistence": persistence,
        "official_score_effect": 0, "official_verdict_effect": "none",
        "not_validated": True,
    }}
