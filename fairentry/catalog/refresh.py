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


def refresh(cfg, store, run_id=None, wma_tickers=None, verbose=True):
    """Refresh the store. Returns a summary dict.
    wma_tickers: tickers to run the (network-heavy) yfinance 200wma on. If None,
    yfinance is skipped this run (scoring treats dist_200wma_pct as missing).
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
        store.commit()
        store.log_fetch(run_id, "finviz", True, len(securities), time.time() - t0)
        summary["sources"]["finviz"] = {"ok": True, "tickers": len(securities), "values": n}
        if verbose:
            print(f"  finviz: {len(securities)} tickers, {n} values "
                  f"({time.time()-t0:.1f}s)")
    except Exception as e:  # source-failure isolation
        store.log_fetch(run_id, "finviz", False, 0, time.time() - t0, str(e))
        summary["sources"]["finviz"] = {"ok": False, "error": str(e)}
        if verbose:
            print(f"  finviz FAILED: {e}")
        return summary  # without the universe there's nothing to enrich

    universe = [s["ticker"] for s in securities]

    # --- yfinance: 200-week MA on requested tickers --------------------------
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
                print(f"  yfinance: {len(m)} tickers 200wma ({time.time()-t0:.1f}s)")
        except Exception as e:
            store.log_fetch(run_id, "yfinance", False, 0, time.time() - t0, str(e))
            summary["sources"]["yfinance"] = {"ok": False, "error": str(e)}

    # --- other enrichers (interfaces live; return {} until ported) -----------
    for name in ("sec_edgar", "finnhub", "form4", "thirteenf"):
        mod = ENRICHERS[name]
        if getattr(mod, "IMPLEMENTED", False):
            t0 = time.time()
            try:
                m = mod.fetch(cfg, set(by_adapter.get(name, [])), universe)
                n = 0
                for tkr, vals in m.items():
                    for fid, val in vals.items():
                        if val is not None:
                            store.set_metric(tkr, fid, val, name)
                            n += 1
                store.commit()
                store.log_fetch(run_id, name, True, len(m), time.time() - t0)
                summary["sources"][name] = {"ok": True, "values": n}
            except Exception as e:
                store.log_fetch(run_id, name, False, 0, time.time() - t0, str(e))
                summary["sources"][name] = {"ok": False, "error": str(e)}

    return summary
