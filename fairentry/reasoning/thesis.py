"""Thesis & recovery reasoning (Layer B). For a shortlisted candidate, ask the
provider WHY it's down (or whether a growth premium is justified), how likely it
recovers, and what would invalidate the thesis — as structured, evidence-linked
JSON. Cached by input hash. Robust: any provider failure -> a neutral thesis
(modifier 0) so the deterministic pipeline never breaks.
"""
from __future__ import annotations

from . import cache
from .provider import get_provider

PROMPT_VERSION = "v1"

_SYS = ("You are a disciplined value+growth equity analyst. Think like a careful "
        "investor, not a hype machine. Reply with a single JSON object only, no prose.")

_RECOVERY_SCHEMA = (
    "Return JSON with exactly these keys: "
    "recovery_score (integer 0-100, higher = more credible recovery), "
    "temporary_vs_structural (one of: temporary, cyclical, structural, unknown), "
    "primary_down_reason (short string), "
    "situation (array of up to 4 objects, each: {reason, status(one of active/improving/"
    "resolved/worsening), severity(low/medium/high/critical), evidence(short, cite a metric)}), "
    "key_catalyst (short string or ''), expected_timeframe (short string), "
    "kill_switch (short string: what would prove the thesis wrong), "
    "summary (<=40 words).")

_GROWTH_SCHEMA = (
    "Return JSON with exactly these keys: "
    "growth_score (integer 0-100, higher = more durable growth worth entering), "
    "required_growth_to_justify_price (short string), "
    "durability (one of: durable, moderate, fragile, unknown), "
    "entry_view (one of: buy_now, starter, wait_for_pullback, wait_for_confirmation, avoid), "
    "summary (<=40 words), kill_switch (short string).")


def _facts(sec, metrics):
    def g(k):
        v = metrics.get(k, {})
        return v.get("value") if isinstance(v, dict) else v
    keys = ["price", "target_price", "gross_margin", "oper_margin", "roic",
            "rev_growth_qoq", "eps_growth_next_y", "debt_eq", "fwd_pe", "ps_ratio",
            "perf_year", "short_float", "inst_trans"]
    return {k: g(k) for k in keys if g(k) is not None}


def build_thesis(sec, metrics, verdict_ctx, strategy_key, provider_name="deepseek"):
    """Return a thesis dict (recovery/growth) + modifier band info. Cached."""
    prov = get_provider(provider_name)
    facts = _facts(sec, metrics)
    inputs = {"facts": facts, "verdict": verdict_ctx, "strategy": strategy_key}
    ck = cache.key(sec["ticker"], PROMPT_VERSION, getattr(prov, "NAME", "?"), inputs)
    cached = cache.get(ck)
    if cached is not None:
        return cached

    is_growth = strategy_key == "quality_growth"
    schema = _GROWTH_SCHEMA if is_growth else _RECOVERY_SCHEMA
    user = (f"Ticker {sec['ticker']} ({sec.get('company','')}), sector {sec.get('sector','')}. "
            f"Current deterministic verdict: {verdict_ctx.get('verdict')} "
            f"(score {verdict_ctx.get('preliminary')}). Key facts: {facts}. "
            + ("Judge whether the growth premium is justified and the entry is safe. "
               if is_growth else
               "Diagnose why it may be down and whether recovery is credible. ")
            + schema)

    try:
        out = prov.complete_json(_SYS, user)
        out["_provider"] = getattr(prov, "NAME", "?")
        out["_stub"] = out.get("_stub", False)
    except Exception as e:  # provider down / no balance -> neutral, non-fatal
        out = {"_provider": "unavailable", "_error": str(e)[:120],
               "recovery_score": 50, "growth_score": 50, "thesis_confidence": "low",
               "summary": "Reasoning unavailable (provider) — deterministic score only.",
               "situation": [], "kill_switch": ""}

    score = out.get("growth_score" if is_growth else "recovery_score", 50)
    out["thesis_score"] = int(score) if isinstance(score, (int, float)) else 50
    cache.put(ck, out)
    return out


def modifier_for(thesis_score: int, bands: list) -> int:
    for b in sorted(bands, key=lambda x: -x["min"]):
        if thesis_score >= b["min"]:
            return b["mod"]
    return 0
