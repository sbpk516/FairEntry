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
    return {
        "ticker": rec["ticker"], "company": rec["company"], "sector": rec["sector"],
        "country": rec.get("country"), "strategy": strategies, "price": rec["price"],
        "score": rec["score"], "verdict": rec["verdict"],
        "base_score": rec["base_score"], "thesis_modifier": rec["thesis_modifier"],
        "preliminary": rec["preliminary"], "coverage_pct": rec.get("coverage_pct"),
        "cats": [{"id": c["id"], "label": c["label"], "score": c["score"] or 0,
                  "items": [{"label": i["label"], "weight": i["weight"], "score": i["score"] or 0,
                             "actual": (rec.get("_sm_flow") if i.get("id") == "smart_money" and rec.get("_sm_flow")
                                        else ("n/a" if i["actual"] is None else str(i["actual"]))),
                             "expected": i["expected"], "rule": i["rule"],
                             "source": i["source"] or "—"} for i in c["items"] if i["score"] is not None]}
                 for c in rec["categories"] if c["score"] is not None],
        "categories": [{"id": c["id"], "label": c["label"], "score": c["score"] or 0,
                        "weight": c["weight"], "coverage": c.get("coverage"),
                        "items": [{"label": i["label"], "weight": i["weight"],
                                   "score": i["score"] or 0,
                                   "actual": "n/a" if i["actual"] is None else str(i["actual"]),
                                   "expected": i["expected"], "rule": i["rule"],
                                   "source": i["source"] or "-",
                                   "fetched_at": i.get("fetched_at") or "-"}
                                  for i in c["items"] if i["score"] is not None]}
                       for c in rec["categories"] if c["score"] is not None],
        "thesis": thesis,
        "valuation": {"low": fv["fair_low"], "base": fv["fair_base"], "high": fv["fair_high"],
                      "upside": round(fv["upside_pct"]), "label": fv["valuation_label"],
                      "methods": fv.get("methods", [])},
        "growth_entry": growth_entry,
        "vetoes": [v["reason"] for v in rec["vetoes"]],
        "soft": [g["reason"] for g in rec["soft_gates"]],
        "soft_gates": [g["reason"] for g in rec["soft_gates"]],
        "labels": labels, "action": action, "action_plan": action,
        # informational only — see demand_momentum(); NOT used in the score/verdict
        "context": rec.get("_context"),
    }


def _rescore_with_thesis(cfg, secs, store, rec, th, settings, med):
    """Re-score one rec with its thesis modifier + preset weights, carrying the
    thesis (and rec-attached extras) onto the new record. Shared by the live-LLM
    path and the stored-thesis re-attach path so they behave identically."""
    from ..reasoning.thesis import modifier_for
    primary = rec["_primary"]
    mod = modifier_for(th.get("thesis_score", 50), cfg.scoring.get("thesis_modifier", []))
    s = dict(settings); s["thesis_modifier"] = mod
    pw = _preset_weights(cfg, primary)
    if pw:
        s["weights"] = pw
    r2 = score_ticker(cfg, secs[rec["ticker"]], store.metrics_for(rec["ticker"]), med, s)
    r2["_primary"] = primary; r2["_strategies"] = rec["_strategies"]; r2["_thesis"] = th
    r2["_sm_flow"] = rec.get("_sm_flow")   # preserve rec-attached extras across re-score
    r2["_context"] = rec.get("_context")   # informational only
    return r2, mod


def _apply_reasoning(cfg, secs, store, recs, settings, med, cap=30):
    """Run the reasoning layer (a real LLM call) on the names most worth an AI
    read: every Buy / near-Buy candidate — preliminary at or above the Watch line
    (minus a small margin so borderline names the modifier could tip are included
    too), highest score first, capped. Covers clear Buys, which the old
    borderline-only window (<= buy_b + 4) excluded.

    Each successful thesis is persisted to the store (thesis_results) so later
    deterministic builds can re-attach it. Circuit-breaks if the provider is
    unavailable OR only the offline stub is present (no DEEPSEEK_API_KEY /
    balance) — so we never stall the run, and never attach a placeholder 'review'
    that isn't a real one (the name stays honestly 'not reviewed yet')."""
    from ..reasoning.thesis import build_thesis
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    watch_b = cfg.verdict_bands["watch"]
    shortlist = sorted(
        [r for r in recs if r["preliminary"] >= watch_b - 3 and not r["vetoes"]],
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


def build_board(cfg, store, settings=None, reason=False) -> dict:
    settings = settings or {"margin_of_safety_pct": 15, "target_upside_pct": 30}
    med = sector_medians(cfg, store)
    secs = {x["ticker"]: x for x in store.securities()}
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

    recs = []
    for t, strategies in quals.items():
        primary = "deep_value" if "deepvalue" in strategies else "quality_growth"
        s = dict(settings)
        pw = _preset_weights(cfg, primary)
        if pw:
            s["weights"] = pw
        mt = store.metrics_for(t)
        rec = score_ticker(cfg, secs[t], mt, med, s)
        rec["_primary"] = primary; rec["_strategies"] = strategies
        smf = mt.get("thirteenf_flow", {})
        rec["_sm_flow"] = smf.get("value") if isinstance(smf, dict) else None
        rec["_context"] = demand_momentum(mt)   # informational only — not scored
        recs.append(rec)

    reasoning_summary = {}
    if reason:
        reasoning_summary = _apply_reasoning(cfg, secs, store, recs, settings, med)
    # Always re-attach the most recent stored thesis to names not freshly
    # reasoned, so AI reads survive the deterministic builds between weekly runs.
    reattached = _attach_stored_theses(cfg, secs, store, recs, settings, med)

    stocks = []
    for rec in recs:
        store.set_score_result(rec["ticker"], rec["_primary"], rec["base_score"],
                               rec["preliminary"], rec["verdict"], rec)
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
    return {"meta": {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "sectors": [s["id"] for s in cfg.enabled_sectors],
                     "config_version": cfg.scoring.get("version"), "count": len(stocks),
                     "reasoning": reasoning_summary,
                     "ai_review": ai_review,
                     "presets": cfg.scoring.get("presets", {}),
                     "default_weights": {cid: c["weight"] for cid, c in cfg.categories.items()},
                     # everything the UI needs to reproduce the backend verdict
                     # exactly (per-strategy preset weights, bands, thesis modifier)
                     "strategy_presets": cfg.defaults.get("strategy_presets", {}),
                     "verdict_bands": cfg.verdict_bands,
                     "thesis_modifier": cfg.scoring.get("thesis_modifier", [])},
            "stocks": stocks}


def write_board(board: dict, path: Path = OUT):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(board, indent=1, ensure_ascii=False), encoding="utf-8")
    return path
