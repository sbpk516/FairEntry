"""Build the JSON the UI reads. One record per ticker (its qualifying
strategies), scored under the strategy preset, mapped to the UI's drill-down
shape (categories/items with actual/expected/rule/score, valuation, verdict).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..scoring.engine import sector_medians, score_ticker
from ..screeners import REGISTRY as SCREENERS

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "web" / "data" / "board.json"


def _preset_weights(cfg, strategy_key):
    presets = cfg.scoring.get("presets", {})
    name = cfg.defaults.get("strategy_presets", {}).get(strategy_key)
    return presets.get(name)


def _labels(rec):
    out = []
    up = rec["valuation"]["upside_pct"]
    if up is not None:
        out.append([f"Upside {'+' if up >= 0 else ''}{up:.0f}%", "good" if up >= 20 else "warn" if up >= 0 else "bad"])
    out.append([rec["valuation"]["valuation_label"], "good" if rec["valuation"]["valuation_label"] == "cheap"
                else "bad" if rec["valuation"]["valuation_label"] == "expensive" else "info"])
    for v in rec["vetoes"]:
        out.append([v["reason"], "bad"])
    return out[:5]


def _action(rec):
    v = rec["verdict"]
    if v == "Avoid":
        return {"action": "Avoid", "size": "—", "entry": (rec["vetoes"][0]["reason"] if rec["vetoes"]
                else "Weak score."), "add": "—", "stop": "—", "review": "—"}
    if v == "Buy":
        return {"action": "Buy Now", "size": "3%", "entry": "Clears the gates on the numbers.",
                "add": "On confirmation.", "stop": "Thesis kill-switch (reasoning layer, pending).",
                "review": "Next earnings"}
    return {"action": "Watch", "size": "starter", "entry": (rec["soft_gates"][0]["reason"] if rec["soft_gates"]
            else "Not yet actionable."), "add": "—", "stop": "—", "review": "—"}


def _map(rec, strategies, strategy_key):
    fv = rec["valuation"]
    th = rec.get("_thesis")
    if th:
        # UI's situationHTML reads arrays: [reason, status, severity, temp/struct, duration, evidence]
        situation = [[s.get("reason", ""), s.get("status", "active"), s.get("severity", "medium"),
                      th.get("temporary_vs_structural", "unknown"),
                      th.get("expected_timeframe", ""), s.get("evidence", "")]
                     for s in (th.get("situation") or [])]
        thesis = {"type": "recovery" if strategy_key == "deep_value" else "growth",
                  "score": th.get("thesis_score", 50), "label": th.get("temporary_vs_structural", "—"),
                  "summary": th.get("summary", ""), "situation": situation,
                  "kill": th.get("kill_switch", ""), "provider": th.get("_provider", "—")}
    else:
        thesis = {"type": "recovery" if strategy_key == "deep_value" else "growth",
                  "score": 50, "label": "not shortlisted",
                  "summary": "Deterministic score only (reasoning runs on the shortlist).",
                  "situation": [], "kill": "", "provider": "—"}
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
    return {
        "ticker": rec["ticker"], "company": rec["company"], "sector": rec["sector"],
        "strategy": strategies, "price": rec["price"],
        "cats": [{"id": c["id"], "label": c["label"], "score": c["score"] or 0,
                  "items": [{"label": i["label"], "weight": i["weight"], "score": i["score"] or 0,
                             "actual": "n/a" if i["actual"] is None else str(i["actual"]),
                             "expected": i["expected"], "rule": i["rule"],
                             "source": i["source"] or "—"} for i in c["items"] if i["score"] is not None]}
                 for c in rec["categories"] if c["score"] is not None],
        "thesis": thesis,
        "valuation": {"low": fv["fair_low"], "base": fv["fair_base"], "high": fv["fair_high"],
                      "upside": round(fv["upside_pct"]), "label": fv["valuation_label"]},
        "growth_entry": growth_entry,
        "vetoes": [v["reason"] for v in rec["vetoes"]],
        "soft": [g["reason"] for g in rec["soft_gates"]],
        "labels": _labels(rec), "action": _action(rec),
    }


def _apply_reasoning(cfg, secs, store, recs, settings, med, cap=25):
    """Run the reasoning layer on a borderline shortlist only. Circuit-breaks if
    the provider is unavailable (e.g. no balance) so we never stall the run."""
    from ..reasoning.thesis import build_thesis, modifier_for
    bands = cfg.scoring.get("thesis_modifier", [])
    buy_b, watch_b = cfg.verdict_bands["buy"], cfg.verdict_bands["watch"]
    shortlist = sorted(
        [r for r in recs if watch_b - 3 <= r["preliminary"] <= buy_b + 4 and not r["vetoes"]],
        key=lambda r: -r["preliminary"])[:cap]
    provider_down = False
    used = 0
    for r in shortlist:
        primary = r["_primary"]
        if provider_down:
            continue
        th = build_thesis(secs[r["ticker"]], store.metrics_for(r["ticker"]),
                          {"verdict": r["verdict"], "preliminary": r["preliminary"]}, primary)
        if th.get("_provider") == "unavailable":
            provider_down = True
            continue
        mod = modifier_for(th.get("thesis_score", 50), bands)
        s = dict(settings); s["thesis_modifier"] = mod
        pw = _preset_weights(cfg, primary)
        if pw:
            s["weights"] = pw
        r2 = score_ticker(cfg, secs[r["ticker"]], store.metrics_for(r["ticker"]), med, s)
        r2["_primary"] = primary; r2["_strategies"] = r["_strategies"]; r2["_thesis"] = th
        recs[recs.index(r)] = r2
        used += 1
    return {"shortlist": len(shortlist), "reasoned": used, "provider_down": provider_down}


def build_board(cfg, store, settings=None, reason=False) -> dict:
    settings = settings or {"margin_of_safety_pct": 15, "target_upside_pct": 30}
    med = sector_medians(cfg, store)
    secs = {x["ticker"]: x for x in store.securities()}

    quals: dict[str, list[str]] = {}
    for sid, mod in SCREENERS.items():
        for t in secs:
            ok, _ = mod.passes(store.metrics_for(t))
            if ok:
                quals.setdefault(t, []).append(mod.STRATEGY)
                store.set_screen_result(t, sid, True, {})
    store.commit()

    recs = []
    for t, strategies in quals.items():
        primary = "deep_value" if "deepvalue" in strategies else "quality_growth"
        s = dict(settings)
        pw = _preset_weights(cfg, primary)
        if pw:
            s["weights"] = pw
        rec = score_ticker(cfg, secs[t], store.metrics_for(t), med, s)
        rec["_primary"] = primary; rec["_strategies"] = strategies
        recs.append(rec)

    reasoning_summary = {}
    if reason:
        reasoning_summary = _apply_reasoning(cfg, secs, store, recs, settings, med)

    stocks = []
    for rec in recs:
        store.set_score_result(rec["ticker"], rec["_primary"], rec["base_score"],
                               rec["preliminary"], rec["verdict"], rec)
        stocks.append(_map(rec, rec["_strategies"], rec["_primary"]))
    store.commit()

    stocks.sort(key=lambda r: -(r["cats"] and sum(c["score"] for c in r["cats"]) or 0))
    return {"meta": {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "sectors": [s["id"] for s in cfg.enabled_sectors],
                     "config_version": cfg.scoring.get("version"), "count": len(stocks),
                     "reasoning": reasoning_summary},
            "stocks": stocks}


def write_board(board: dict, path: Path = OUT):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(board, indent=1, ensure_ascii=False), encoding="utf-8")
    return path
