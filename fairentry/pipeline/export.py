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
    return {
        "ticker": rec["ticker"], "company": rec["company"], "sector": rec["sector"],
        "strategy": strategies, "price": rec["price"],
        "cats": [{"id": c["id"], "label": c["label"], "score": c["score"] or 0,
                  "items": [{"label": i["label"], "weight": i["weight"], "score": i["score"] or 0,
                             "actual": "n/a" if i["actual"] is None else str(i["actual"]),
                             "expected": i["expected"], "rule": i["rule"],
                             "source": i["source"] or "—"} for i in c["items"] if i["score"] is not None]}
                 for c in rec["categories"] if c["score"] is not None],
        "thesis": {"type": "recovery" if strategy_key == "deep_value" else "growth",
                   "score": 50, "label": "Reasoning pending",
                   "summary": "Deterministic score shown. The thesis/recovery reasoning layer "
                              "(why it's down, peers, DeepSeek) runs in the next phase.",
                   "situation": [], "kill": ""},
        "valuation": {"low": fv["fair_low"], "base": fv["fair_base"], "high": fv["fair_high"],
                      "upside": round(fv["upside_pct"]), "label": fv["valuation_label"]},
        "vetoes": [v["reason"] for v in rec["vetoes"]],
        "soft": [g["reason"] for g in rec["soft_gates"]],
        "labels": _labels(rec), "action": _action(rec),
    }


def build_board(cfg, store, settings=None) -> dict:
    settings = settings or {"margin_of_safety_pct": 15, "target_upside_pct": 30}
    med = sector_medians(cfg, store)
    secs = {x["ticker"]: x for x in store.securities()}

    # which strategies each ticker qualifies for
    quals: dict[str, list[str]] = {}
    for sid, mod in SCREENERS.items():
        for t, sec in secs.items():
            ok, _ = mod.passes(store.metrics_for(t))
            if ok:
                quals.setdefault(t, []).append(mod.STRATEGY)
                store.set_screen_result(t, sid, True, {})
    store.commit()

    stocks = []
    for t, strategies in quals.items():
        primary = "deep_value" if "deepvalue" in strategies else "quality_growth"
        s = dict(settings)
        pw = _preset_weights(cfg, primary)
        if pw:
            s["weights"] = pw
        rec = score_ticker(cfg, secs[t], store.metrics_for(t), med, s)
        store.set_score_result(t, primary, rec["base_score"], rec["preliminary"], rec["verdict"], rec)
        stocks.append(_map(rec, strategies, primary))
    store.commit()

    stocks.sort(key=lambda r: -(r["cats"] and sum(c["score"] * 1 for c in r["cats"]) or 0))
    return {"meta": {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "sectors": [s["id"] for s in cfg.enabled_sectors],
                     "config_version": cfg.scoring.get("version"), "count": len(stocks)},
            "stocks": stocks}


def write_board(board: dict, path: Path = OUT):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(board, indent=1, ensure_ascii=False), encoding="utf-8")
    return path
