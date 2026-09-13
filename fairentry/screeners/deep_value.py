"""Deep Value screener — store-only. Beaten-down / cheap names where survival is
plausible; the scoring engine + thesis layer decide the actual verdict.
"""
from __future__ import annotations

ID = "deep_value"
STRATEGY = "deepvalue"
CRITERIA = {'pb_max': 2, 'ps_max': 2, 'pfcf_max': 18, 'performance_max': 0, 'debt_equity_max': 2.5}
INPUT_FIELDS = ["pb_ratio", "ps_ratio", "pfcf_ratio", "debt_eq", "current_ratio",
                "price", "perf_year"]


def _n(m, k):
    v = m.get(k, {}).get("value")
    return v if isinstance(v, (int, float)) else None


def passes(metrics: dict) -> tuple[bool, dict]:
    pb = _n(metrics, "pb_ratio")
    ps = _n(metrics, "ps_ratio")
    pfcf = _n(metrics, "pfcf_ratio")
    debt = _n(metrics, "debt_eq")
    perf = _n(metrics, "perf_year")
    cheap = ((pb is not None and pb <= CRITERIA['pb_max'])
             or (ps is not None and ps <= CRITERIA['ps_max'])
             or (pfcf is not None and 0 < pfcf <= CRITERIA['pfcf_max']))
    beaten = perf is not None and perf <= CRITERIA['performance_max']
    survivable = debt is None or debt <= CRITERIA['debt_equity_max']
    ok = bool(cheap and survivable and beaten)
    return ok, {"cheap": cheap, "beaten": beaten, "survivable": survivable,
                "decision_inputs": ["pb_ratio", "ps_ratio", "pfcf_ratio",
                                    "debt_eq", "perf_year"]}
