"""SEC Form 4 adapter — insider buying (recency, cluster, top-exec, materiality).
Ports v1 insider_flow.py (now fairentry/lib/insider.py) into an `insider_score`
(0-100) the Market Confirmation category consumes. Cached; expensive; caller
passes a bounded ticker list.
"""
from __future__ import annotations

from ..lib import insider
from .cache_lite import cache_get, cache_put
from .sec_edgar import _cik_map

OWNS = "form4"
IMPLEMENTED = True


def fetch(cfg, field_ids, tickers=None, market_caps=None):
    cikm = _cik_map()
    caps = market_caps or {}
    out = {}
    for t in (tickers or []):
        t = t.upper()
        cached = cache_get("insider_score", t, ttl_days=2)   # insiders move faster
        if cached is None:
            cik = cikm.get(t)
            val = {}
            if cik:
                try:
                    s = insider.insider_summary(cik, caps.get(t, 0.0) or 0.0)
                    # no buying = mildly-below-neutral 45; strong buying scales to 100
                    val = {"insider_score": round(45 + insider.score_materiality(s) / 25 * 55)}
                except Exception:
                    val = {}
            cache_put("insider_score", t, val)
            cached = val
        if cached:
            out[t] = {k: v for k, v in cached.items() if k in field_ids and v is not None}
    return out
