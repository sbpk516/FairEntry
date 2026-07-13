"""Quality Growth Entry screener — store-only. Strong/rising businesses; the
scoring + entry logic decide whether the price is a good entry.
"""
from __future__ import annotations

ID = "quality_growth"
STRATEGY = "growth"
INPUT_FIELDS = ["rev_growth_qoq", "eps_growth_next_y", "gross_margin", "roe", "sma200"]


def _n(m, k):
    v = m.get(k, {}).get("value")
    return v if isinstance(v, (int, float)) else None


def passes(metrics: dict) -> tuple[bool, dict]:
    rev = _n(metrics, "rev_growth_qoq")
    eps = _n(metrics, "eps_growth_next_y")
    gm = _n(metrics, "gross_margin")
    growing = (rev is not None and rev >= 10) or (eps is not None and eps >= 12)
    quality = gm is None or gm >= 25
    ok = bool(growing and quality)
    return ok, {"growing": growing, "quality": quality, "rev": rev, "eps": eps}
