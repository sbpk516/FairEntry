"""Quality Growth Entry screener — store-only. Strong/rising businesses; the
scoring + entry logic decide whether the price is a good entry.
"""
from __future__ import annotations

ID = "quality_growth"
STRATEGY = "growth"
INPUT_FIELDS = ["rev_growth_qoq", "gross_margin", "roe", "sma200"]


def _n(m, k):
    v = m.get(k, {}).get("value")
    return v if isinstance(v, (int, float)) else None


def passes(metrics: dict) -> tuple[bool, dict]:
    rev = _n(metrics, "rev_growth_qoq")
    gm = _n(metrics, "gross_margin")
    # Current analyst EPS forecasts are not available in the historical SFA
    # replay, so membership uses reported revenue only.
    growing = rev is not None and rev >= 10
    quality = gm is None or gm >= 25
    ok = bool(growing and quality)
    return ok, {"growing": growing, "quality": quality, "rev": rev,
                "decision_inputs": ["rev_growth_qoq", "gross_margin"]}
