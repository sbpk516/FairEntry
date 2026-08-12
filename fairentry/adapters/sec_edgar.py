"""SEC EDGAR / XBRL adapter — Altman-Z, red-flag panel, going-concern.
Ports v1's red_flags.py (now fairentry/lib/red_flags.py) into the catalog.
Feeds survival, risk, and the hard vetoes. Cached (7-day TTL); expensive, so the
caller passes a bounded ticker list.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from ..lib import red_flags as rf
from .base import sf
from .cache_lite import cache_get, cache_put

OWNS = "sec_edgar"
IMPLEMENTED = True

ROOT = Path(__file__).resolve().parent.parent.parent
_CIK_URL = "https://www.sec.gov/files/company_tickers.json"


def _cik_map() -> dict:
    cached = cache_get("sec_cik", "map", ttl_days=30)
    if cached:
        return cached
    try:
        r = requests.get(_CIK_URL, headers=rf.SEC_HEADERS, timeout=30)
        r.raise_for_status()
        raw = r.json()
        m = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in raw.values()}
        cache_put("sec_cik", "map", m)
        return m
    except Exception:
        return {}


def _dilution(cik) -> float | None:
    """Year-over-year % change in diluted share count from XBRL (facts cached by
    the forensic panel). Positive = dilution, negative = buyback."""
    facts = rf.fetch_company_facts(str(cik))
    if not facts:
        return None
    for concept in ("WeightedAverageNumberOfDilutedSharesOutstanding",
                    "WeightedAverageNumberOfSharesOutstandingBasic",
                    "CommonStockSharesOutstanding"):
        try:
            vals = facts["facts"]["us-gaap"][concept]["units"].get("shares", [])
        except KeyError:
            continue
        annual = [v for v in vals if v.get("form") == "10-K" and v.get("fp") == "FY"]
        seen = {}
        for e in annual:
            if e["end"] not in seen or e["filed"] > seen[e["end"]]["filed"]:
                seen[e["end"]] = e
        recent = sorted(seen.values(), key=lambda x: x["end"], reverse=True)[:2]
        if len(recent) >= 2 and recent[1]["val"] > 0:
            return round((recent[0]["val"] / recent[1]["val"] - 1) * 100, 1)
    return None


def debt_direction_from_facts(facts: dict) -> dict:
    """Year-over-year long-term-debt burden from filed annual statements.

    Using debt/assets instead of raw debt avoids calling normal expansion
    "worsening" when assets grew at least as quickly. The output is a candidate
    research factor only; the scoring config marks it as ``testing``.
    """
    debts = rf.get_metric(facts, "lt_debt")
    assets = rf.get_metric(facts, "assets")
    if len(debts) < 2 or len(assets) < 2:
        return {}
    debt_by_end = {row.get("end"): row.get("val") for row in debts}
    assets_by_end = {row.get("end"): row.get("val") for row in assets}
    periods = sorted(set(debt_by_end) & set(assets_by_end), reverse=True)
    if len(periods) < 2:
        return {}
    now, prior = periods[:2]
    if not assets_by_end[now] or not assets_by_end[prior]:
        return {}
    current = debt_by_end[now] / assets_by_end[now] * 100
    previous = debt_by_end[prior] / assets_by_end[prior] * 100
    return {
        "debt_to_assets_pct": round(current, 2),
        "debt_to_assets_yago_pct": round(previous, 2),
        "debt_to_assets_change_yoy_pp": round(current - previous, 2),
    }


def _panel_to_fields(panel: dict) -> dict:
    sc = panel.get("scores", {})
    crit = panel.get("critical_count", 0)
    warn = panel.get("warning_count", 0)
    return {
        "altman_z": sc.get("altman_z"),
        "red_flags_score": max(0, 100 - crit * 35 - warn * 8),
        "red_flags_critical": crit,
        "going_concern": bool(panel.get("disqualify")),
    }


def fetch(cfg, field_ids, tickers=None, market_caps=None):
    """market_caps: {ticker: market_cap_b} (Altman-Z X4). Returns {ticker: {field: value}}."""
    cikm = _cik_map()
    caps = market_caps or {}
    out = {}
    for t in (tickers or []):
        t = t.upper()
        cache_key = f"{t}_debt_direction_v1"
        cached = cache_get("sec_panel", cache_key, ttl_days=7)
        if cached is None:
            cik = cikm.get(t)
            if not cik:
                cache_put("sec_panel", cache_key, {})   # negative-cache unknown CIK
                continue
            try:
                panel = rf.generate_red_flags(t, cik, caps.get(t, 0.0) or 0.0)
                cached = _panel_to_fields(panel)
                d = _dilution(cik)
                if d is not None:
                    cached["share_count_yoy"] = d
                facts = rf.fetch_company_facts(str(cik))
                if facts:
                    cached.update(debt_direction_from_facts(facts))
            except Exception:
                cached = {}
            cache_put("sec_panel", cache_key, cached)
        if cached:
            out[t] = {k: v for k, v in cached.items() if k in field_ids and v is not None}
    return out
