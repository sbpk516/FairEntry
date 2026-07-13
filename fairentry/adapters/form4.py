"""SEC Form 4 adapter — insider transactions (recency, cluster, top-exec buys).
Live interface; parser ported from v1 insider_flow.py in a follow-up pass.
"""
from __future__ import annotations

OWNS = "form4"
IMPLEMENTED = False   # TODO: port v1 insider_flow.py


def fetch(cfg, field_ids, tickers=None):
    return {}
