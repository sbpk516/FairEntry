"""SEC EDGAR / XBRL adapter — Altman-Z, red-flag panel, going-concern, dilution.
Interface is live; the heavy XBRL parsing is ported from v1 (red_flags.py,
risk_model.py) in a follow-up pass. Until then it returns no values, and the
scoring engine treats those items as missing (neutral) rather than failing.
"""
from __future__ import annotations

OWNS = "sec_edgar"
IMPLEMENTED = False   # TODO: port v1 red_flags.py / risk_model.py


def fetch(cfg, field_ids, tickers=None):
    # Port target: fetch companyfacts XBRL -> altman_z, red_flags_panel,
    # going_concern, dilution_yoy (7-day cache, SEC 10 req/s fair-use).
    return {}
