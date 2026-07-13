"""Rule interpreter — maps a metric value to a 0-100 item score, transparently.
Each rule also returns a short 'how' string so the UI can show why.
Returns (score, how) or (None, reason) when the metric is missing.
"""
from __future__ import annotations


def _lerp(v, lo, hi):
    """Map v in [lo,hi] (either order) to 0..100, clamped."""
    if hi == lo:
        return 100.0 if v >= hi else 0.0
    t = (v - lo) / (hi - lo)
    return max(0.0, min(100.0, t * 100.0))


def apply_rule(rule: dict, value, sector_median=None):
    t = rule.get("type")
    if value is None and t != "passthrough":
        return None, "no data"

    if t == "passthrough":
        if value is None:
            # Sparse signals (e.g. 13F, estimate revisions) opt to DROP when we
            # have no data — the item vanishes and the category renormalizes,
            # rather than pretending a neutral 50. Others keep the neutral default.
            if rule.get("drop_if_missing"):
                return None, "no data"
            return 50.0, "no data — neutral"
        return max(0.0, min(100.0, float(value))), "direct score"

    if t == "higher_better":
        s = _lerp(value, rule["floor_at"], rule["full_at"])
        return s, f"{value} vs full≥{rule['full_at']} / floor≤{rule['floor_at']}"

    if t == "lower_better":
        s = _lerp(value, rule["floor_at"], rule["full_at"])   # full_at<floor_at
        return s, f"{value} vs full≤{rule['full_at']} / floor≥{rule['floor_at']}"

    if t == "sector_rel":
        if sector_median is None:
            # fall back to an absolute-ish read so we still produce a score
            return 50.0, "no sector median — neutral"
        delta = value - sector_median
        if rule.get("lower_better"):
            delta = -delta
        s = _lerp(delta, rule["floor_delta"], rule["full_delta"])
        sign = "+" if delta >= 0 else ""
        return s, f"{sign}{delta:.1f} vs sector median ({sector_median:.1f})"

    if t == "band":
        for b in rule["bands"]:
            lo = b.get("min", float("-inf"))
            hi = b.get("max", float("inf"))
            if lo <= value <= hi:
                return float(b["score"]), f"{value} in [{lo},{hi}]"
        return 0.0, f"{value} outside all bands"

    if t == "bool_good":
        good = bool(value)
        return (float(rule.get("good_score", 100)) if good
                else float(rule.get("bad_score", 0))), ("true" if good else "false")

    return None, f"unknown rule '{t}'"
