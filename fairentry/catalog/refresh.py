"""Catalog refresh — pull due fields into the store via adapters, with provenance
and point-in-time history. Source failures are isolated (logged, non-fatal).

finviz defines the universe (one export call). Enrichment adapters (yfinance,
sec_edgar, finnhub, form4, thirteenf) enrich specific tickers.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from ..adapters import finviz
from ..adapters import yfinance_adapter, sec_edgar, finnhub, form4, thirteenf

ENRICHERS = {"yfinance": yfinance_adapter, "sec_edgar": sec_edgar,
             "finnhub": finnhub, "form4": form4, "thirteenf": thirteenf}


def _fields_by_adapter(cfg):
    by = {}
    for f in cfg.fields:
        by.setdefault(f["adapter"], []).append(f["id"])
    return by


def refresh(cfg, store, run_id=None, wma_tickers=None, sec_tickers=None, verbose=True):
    """Refresh the store. Returns a summary dict.
    wma_tickers: tickers to run the (network-heavy) yfinance 200wma on.
    sec_tickers: tickers to run the (expensive) SEC forensic panel on. Both are
    bounded by the caller; missing values are handled gracefully by scoring.
    """
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    by_adapter = _fields_by_adapter(cfg)
    summary = {"run_id": run_id, "sources": {}}

    # --- finviz: the universe ------------------------------------------------
    t0 = time.time()
    try:
        securities, metrics = finviz.fetch(cfg, by_adapter.get("finviz", []))
        for s in securities:
            store.upsert_security(**s)
        n = 0
        for tkr, vals in metrics.items():
            for fid, val in vals.items():
                if val is not None:
                    store.set_metric(tkr, fid, val, "finviz")
                    n += 1
        # Only replace membership after the complete Finviz fetch and writes
        # succeed. A source failure therefore cannot empty the active board.
        store.replace_universe("finviz", (s["ticker"] for s in securities))

        # Monitor the liquid fringe separately. The adapter reuses the same
        # cached Finviz export, so this normally adds no second network call.
        # Discovery membership contains only names outside today's official
        # universe; it can never leak into active scoring or recommendations.
        emerging_cfg = cfg.sectors.get("emerging_candidates", {})
        discovery_count = 0
        discovery_values = 0
        if emerging_cfg.get("enabled", True):
            discovery_min = emerging_cfg.get(
                "discovery_avg_dollar_volume_min", 5_000_000)
            discovery_securities, discovery_metrics = finviz.fetch(
                cfg, by_adapter.get("finviz", []),
                avg_dollar_volume_min=discovery_min)
            active_tickers = {s["ticker"] for s in securities}
            outside = [s for s in discovery_securities
                       if s["ticker"] not in active_tickers]
            outside_tickers = {s["ticker"] for s in outside}
            for security in outside:
                store.upsert_security(**security)
            for ticker in outside_tickers:
                for field_id, value in discovery_metrics.get(ticker, {}).items():
                    if value is not None:
                        store.set_metric(ticker, field_id, value, "finviz_discovery")
                        discovery_values += 1
            # Membership includes every $5M+ name so the research UI can show
            # the $5M-$10M, $10M-$20M and $20M+ bands. Metrics are only written
            # again for outside-universe names; official members already have
            # the identical snapshot above.
            store.replace_universe(
                "finviz_discovery", (s["ticker"] for s in discovery_securities))
            discovery_count = len(discovery_securities)
        store.commit()
        store.log_fetch(run_id, "finviz", True, len(securities), time.time() - t0)
        summary["sources"]["finviz"] = {"ok": True, "tickers": len(securities), "values": n}
        summary["sources"]["finviz_discovery"] = {
            "ok": True, "tickers": discovery_count,
            "values": discovery_values,
            "information_only": True,
        }
        if verbose:
            print(f"  finviz: {len(securities)} tickers, {n} values "
                  f"({time.time()-t0:.1f}s)")
            print(f"  finviz discovery: {discovery_count} total $5M+ tickers "
                  f"({len(outside_tickers)} outside official), "
                  f"{discovery_values} outside-only values (research only)")
    except Exception as e:  # source-failure isolation
        store.log_fetch(run_id, "finviz", False, 0, time.time() - t0, str(e))
        summary["sources"]["finviz"] = {"ok": False, "error": str(e)}
        if verbose:
            print(f"  finviz FAILED: {e}")
        return summary  # without the universe there's nothing to enrich

    universe = [s["ticker"] for s in securities]

    # --- yfinance: monthly EMA and weekly OBV entry indicators ---------------
    if wma_tickers:
        targets = [t for t in wma_tickers if t in set(universe)]
        t0 = time.time()
        try:
            m = yfinance_adapter.fetch(cfg, set(by_adapter.get("yfinance", [])), targets)
            n = 0
            for tkr, vals in m.items():
                for fid, val in vals.items():
                    if val is not None:
                        store.set_metric(tkr, fid, val, "yfinance")
                        n += 1
            store.commit()
            store.log_fetch(run_id, "yfinance", True, len(m), time.time() - t0)
            summary["sources"]["yfinance"] = {"ok": True, "tickers": len(m), "values": n}
            if verbose:
                print(f"  yfinance: {len(m)} tickers entry indicators ({time.time()-t0:.1f}s)")
        except Exception as e:
            store.log_fetch(run_id, "yfinance", False, 0, time.time() - t0, str(e))
            summary["sources"]["yfinance"] = {"ok": False, "error": str(e)}

    # --- SEC forensic panel (Altman-Z, red flags, going-concern) -------------
    if sec_tickers:
        caps = {t: (metrics.get(t, {}).get("market_cap") or 0) / 1e9 for t in sec_tickers}
        targets = [t for t in sec_tickers if t in set(universe)]
        t0 = time.time()
        try:
            m = sec_edgar.fetch(cfg, set(by_adapter.get("sec_edgar", [])), targets, market_caps=caps)
            n = 0
            for tkr, vals in m.items():
                for fid, val in vals.items():
                    if val is not None:
                        store.set_metric(tkr, fid, val, "sec_edgar")
                        n += 1
            store.commit()
            store.log_fetch(run_id, "sec_edgar", True, len(m), time.time() - t0)
            summary["sources"]["sec_edgar"] = {"ok": True, "tickers": len(m), "values": n}
            if verbose:
                print(f"  sec_edgar: {len(m)} tickers forensic ({time.time()-t0:.0f}s)")
        except Exception as e:
            store.log_fetch(run_id, "sec_edgar", False, 0, time.time() - t0, str(e))
            summary["sources"]["sec_edgar"] = {"ok": False, "error": str(e)}

        # --- Form 4 insiders (same candidate set) ----------------------------
        t0 = time.time()
        try:
            m = form4.fetch(cfg, set(by_adapter.get("form4", [])), targets, market_caps=caps)
            n = 0
            for tkr, vals in m.items():
                for fid, val in vals.items():
                    if val is not None:
                        store.set_metric(tkr, fid, val, "form4")
                        n += 1
            store.commit()
            store.log_fetch(run_id, "form4", True, len(m), time.time() - t0)
            summary["sources"]["form4"] = {"ok": True, "tickers": len(m), "values": n}
            if verbose:
                print(f"  form4: {len(m)} tickers insiders ({time.time()-t0:.0f}s)")
        except Exception as e:
            store.log_fetch(run_id, "form4", False, 0, time.time() - t0, str(e))
            summary["sources"]["form4"] = {"ok": False, "error": str(e)}

    # --- SEC 13F: smart-money institutional flow (cost-flat, matched by name) --
    if getattr(thirteenf, "IMPLEMENTED", False):
        t0 = time.time()
        try:
            names = {s["ticker"]: s.get("company", "") for s in securities}
            m = thirteenf.fetch(cfg, set(by_adapter.get("thirteenf", [])), universe, names=names)
            n = 0
            for tkr, vals in m.items():
                for fid, val in vals.items():
                    if val is not None:
                        store.set_metric(tkr, fid, val, "thirteenf")
                        n += 1
            store.commit()
            store.log_fetch(run_id, "thirteenf", True, len(m), time.time() - t0)
            summary["sources"]["thirteenf"] = {"ok": True, "tickers": len(m), "values": n}
            if verbose:
                print(f"  thirteenf: {len(m)} tickers held by tracked funds ({time.time()-t0:.0f}s)")
        except Exception as e:
            store.log_fetch(run_id, "thirteenf", False, 0, time.time() - t0, str(e))
            summary["sources"]["thirteenf"] = {"ok": False, "error": str(e)}

    # finnhub news is fetched shortlist-only from the reasoning layer, not here.
    return summary
