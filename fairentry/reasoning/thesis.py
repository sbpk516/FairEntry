"""Thesis & recovery reasoning (Layer B). For a shortlisted candidate, ask the
provider WHY it's down (or whether a growth premium is justified), how likely it
recovers, and what would invalidate the thesis — as structured, evidence-linked
JSON. Cached by input hash. Robust: any provider failure -> a neutral thesis
(modifier 0) so the deterministic pipeline never breaks.
"""
from __future__ import annotations

from . import cache
from .provider import get_provider
from ..adapters.finnhub import fetch_news

PROMPT_VERSION = "v7"   # v7 adds a non-scoring impact/confidence attention overlay

_SYS = ("You are a disciplined value+growth equity analyst. Think like a careful "
        "investor, not a hype machine. A government policy, contract, or expansion is "
        "evidence only when the supplied headline names a specific action and source; "
        "speculation stays unknown. Reply with a single JSON object only, no prose.")

# §7B watchlist intelligence — real, specific sources to follow to track THIS
# name's thesis. The anti-hallucination clause is deliberate: naming a plausible-
# but-fake @handle or URL is worse than describing the source type honestly.
_WATCHLIST_SCHEMA = (
    "watchlist_sources (array of up to 5 objects, each: {name (a real, well-known "
    "source — a sell-side/independent analyst, a notable investor, a publication/"
    "newsletter, an SEC filing type, or a data source), "
    "type (one of: analyst, investor, publication, filing, data_source, community), "
    "where (short: where to find it — do NOT invent URLs, @handles, or emails; if "
    "unsure, describe the source type generically), "
    "why (short: what tracking it tells you about THIS name's thesis specifically)})")

_BREAKOUT_EVIDENCE_SCHEMA = (
    "breakout_evidence (array of up to 8 objects. Always include one object with id "
    "management_execution and group management, one with id catalyst_visibility and group "
    "catalyst, one with id policy_impact and group catalyst, and one with id "
    "investment_expansion and group management, one with id earnings_review and group "
    "earnings, one with id operational_disruption and group operations, and one with id "
    "external_events and group external; use status unknown when supplied facts/"
    "headlines do not support a decision. Policy evidence must name the specific government "
    "action and who announced it. Investment evidence must name the expansion, contract, "
    "capacity, or acquisition and explain the measurable business path. Each object: "
    "{id (short stable snake_case), "
    "label, group (one of: catalyst, management, earnings, operations, external, "
    "investor_behavior, contradiction), "
    "status (one of: satisfied, partial, failed, contradicted, unknown), "
    "direction (one of: positive, negative, mixed, uncertain), impact (one of: low, "
    "medium, high, critical), confidence (one of: low, medium, high), time_horizon "
    "(short string), event_status (one of: rumor, proposed, announced, confirmed, "
    "occurring, unknown), affected_area (one of: revenue, margins, cash, operations, "
    "valuation, financing, sector, unknown), recommended_response (short string), evidence "
    "(specific metric, event, or headline supplied in this prompt; never invent evidence), "
    "source (metric name or supplied headline source), date (supplied date or '')}), ")

_RECOVERY_SCHEMA = (
    "Return JSON with exactly these keys: "
    "recovery_score (integer 0-100, higher = more credible recovery), "
    "temporary_vs_structural (one of: temporary, cyclical, structural, unknown), "
    "primary_down_reason (short string), "
    "situation (array of up to 4 objects, each: {reason, status(one of active/improving/"
    "resolved/worsening), severity(low/medium/high/critical), evidence(short, cite a metric)}), "
    "key_catalyst (short string or ''), expected_timeframe (short string), "
    "kill_switch (short string: what would prove the thesis wrong), "
    "summary (<=40 words), " + _BREAKOUT_EVIDENCE_SCHEMA + _WATCHLIST_SCHEMA)

_GROWTH_SCHEMA = (
    "Return JSON with exactly these keys: "
    "growth_score (integer 0-100, higher = more durable growth worth entering), "
    "required_growth_to_justify_price (short string), "
    "durability (one of: durable, moderate, fragile, unknown), "
    "entry_view (one of: buy_now, starter, wait_for_pullback, wait_for_confirmation, avoid), "
    "summary (<=40 words), kill_switch (short string), "
    + _BREAKOUT_EVIDENCE_SCHEMA + _WATCHLIST_SCHEMA)


def _facts(sec, metrics):
    def g(k):
        v = metrics.get(k, {})
        return v.get("value") if isinstance(v, dict) else v
    keys = ["price", "target_price", "gross_margin", "oper_margin", "roic",
            "rev_growth_qoq", "eps_growth_next_y", "debt_eq", "fwd_pe", "ps_ratio",
            "perf_year", "short_float", "inst_trans"]
    return {k: g(k) for k in keys if g(k) is not None}


def _news_block(ticker: str) -> tuple[str, list]:
    """Recent headlines as a compact prompt block + the raw list (for the cache
    key & export). Empty string when no news / no key — the LLM then reasons from
    metrics only, exactly as before."""
    news = fetch_news(ticker)
    if not news:
        return "", []
    lines = []
    for n in news[:10]:
        cats = f" [{','.join(n['categories'])}]" if n.get("categories") else ""
        lines.append(f"- {n['date']} | {n.get('source') or 'source unknown'}{cats} | {n['headline']}")
    block = ("Recent news headlines (you decide if each is bullish/bearish — do "
             "NOT assume; read them):\n" + "\n".join(lines) + " ")
    return block, news


def build_thesis(sec, metrics, verdict_ctx, strategy_key, provider_name="deepseek"):
    """Return a thesis dict (recovery/growth) + modifier band info. Cached."""
    prov = get_provider(provider_name)
    facts = _facts(sec, metrics)
    news_block, news = _news_block(sec["ticker"])
    # Coarse news signal in the cache key: material new headlines -> re-reason.
    news_sig = [n["date"] + "|" + n["headline"][:60] for n in news[:6]]
    inputs = {"facts": facts, "verdict": verdict_ctx, "strategy": strategy_key,
              "news": news_sig}
    ck = cache.key(sec["ticker"], PROMPT_VERSION, getattr(prov, "NAME", "?"), inputs)
    cached = cache.get(ck)
    if cached is not None:
        return cached

    is_growth = strategy_key == "quality_growth"
    schema = _GROWTH_SCHEMA if is_growth else _RECOVERY_SCHEMA
    user = (f"Ticker {sec['ticker']} ({sec.get('company','')}), sector {sec.get('sector','')}. "
            f"Current deterministic verdict: {verdict_ctx.get('verdict')} "
            f"(score {verdict_ctx.get('preliminary')}). Key facts: {facts}. "
            + news_block
            + ("Judge whether the growth premium is justified and the entry is safe. "
               if is_growth else
               "Diagnose why it may be down and whether recovery is credible. ")
            + schema)

    try:
        out = prov.complete_json(_SYS, user)
        out["_provider"] = getattr(prov, "NAME", "?")
        out["_stub"] = out.get("_stub", False)
        out["_news"] = news[:8]   # carry the headlines used, for the UI evidence panel
    except Exception as e:  # provider down / no balance -> neutral, non-fatal; NOT cached
        return {"_provider": "unavailable", "_error": str(e)[:120],
                "recovery_score": 50, "growth_score": 50, "thesis_score": 50,
                "thesis_confidence": "low", "temporary_vs_structural": "unknown",
                "summary": "Reasoning unavailable (provider) — deterministic score only.",
                "situation": [], "kill_switch": ""}

    score = out.get("growth_score" if is_growth else "recovery_score", 50)
    out["thesis_score"] = int(score) if isinstance(score, (int, float)) else 50
    cache.put(ck, out)   # only successful reasoning is cached
    return out


def modifier_for(thesis_score: int, bands: list) -> int:
    for b in sorted(bands, key=lambda x: -x["min"]):
        if thesis_score >= b["min"]:
            return b["mod"]
    return 0
