"""Deep Value screener — store-only. Beaten-down / cheap names where survival is
plausible; the scoring engine + thesis layer decide the actual verdict.
"""
from __future__ import annotations

ID = "deep_value"
STRATEGY = "deepvalue"
INPUT_FIELDS = ["fwd_pe", "pb_ratio", "debt_eq", "current_ratio", "target_price", "price", "perf_year"]


def _n(m, k):
    v = m.get(k, {}).get("value")
    return v if isinstance(v, (int, float)) else None


def passes(metrics: dict) -> tuple[bool, dict]:
    fwd_pe = _n(metrics, "fwd_pe")
    pb = _n(metrics, "pb_ratio")
    debt = _n(metrics, "debt_eq")
    perf = _n(metrics, "perf_year")
    price = _n(metrics, "price")
    target = _n(metrics, "target_price")
    upside = ((target / price) - 1) * 100 if price and target else None

    cheap = (fwd_pe is not None and fwd_pe <= 18) or (pb is not None and pb <= 2) \
        or (upside is not None and upside >= 25)
    beaten = perf is not None and perf <= 0
    survivable = debt is None or debt <= 2.5
    ok = bool(cheap and survivable and (beaten or (upside or 0) >= 20))
    return ok, {"cheap": cheap, "beaten": beaten, "survivable": survivable, "upside": upside}
