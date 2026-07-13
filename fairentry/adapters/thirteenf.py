"""SEC 13F adapter — institutional-ownership flow. Live interface; ported from
v1 in a follow-up pass. (Finviz already provides `inst_trans` for a fast proxy.)
"""
from __future__ import annotations

OWNS = "thirteenf"
IMPLEMENTED = False


def fetch(cfg, field_ids, tickers=None):
    return {}
