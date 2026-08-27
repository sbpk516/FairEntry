"""Historical stress protection and recovery evidence (research only)."""
from __future__ import annotations

import math
import statistics


VERSION = "stress_resilience_research_v1"


def _clean(values):
    return [float(value) for value in values or []
            if isinstance(value, (int, float)) and math.isfinite(value) and value > 0]


def _return(values, start, end):
    if start < 0 or end >= len(values) or values[start] <= 0:
        return None
    return (values[end] / values[start] - 1) * 100


def _stress_windows(benchmark, *, window=63, minimum_decline_pct=-8, maximum=5):
    candidates = []
    for end in range(window, len(benchmark)):
        result = _return(benchmark, end - window, end)
        if result is not None and result <= minimum_decline_pct:
            candidates.append((result, end - window, end))
    selected = []
    for result, start, end in sorted(candidates):
        if all(abs(end - prior_end) > window for _, _, prior_end in selected):
            selected.append((result, start, end))
        if len(selected) >= maximum:
            break
    return sorted(selected, key=lambda row: row[2])


def build_stress_resilience(stock_closes: list[float], sector_closes: list[float],
                            spy_closes: list[float], *, observed_at=None) -> dict:
    """Compare prior benchmark selloffs and subsequent stock recovery.

    Arrays are tail-aligned because the current data adapter exposes value
    arrays rather than dated series.  This is transparent in ``data_note`` and
    the historical SFA research must use date-keyed alignment before promotion.
    """
    stock, sector, spy = map(_clean, (stock_closes, sector_closes, spy_closes))
    benchmark = sector if len(sector) >= 252 else spy
    benchmark_name = "sector ETF" if len(sector) >= 252 else "SPY"
    usable = min(len(stock), len(benchmark))
    if usable < 504:
        return {
            "version": VERSION, "status": "insufficient_data",
            "label": "Stress-recovery history incomplete", "events": [],
            "event_count": 0, "backtestable": True, "validated_for_score": False,
            "score_effect": 0, "verdict_effect": "none", "observed_at": observed_at,
            "policy": "Information-only; no score or verdict effect.",
            "data_note": "At least two years of aligned daily stock and benchmark closes are required.",
        }
    stock, benchmark = stock[-usable:], benchmark[-usable:]
    events = []
    for benchmark_decline, start, trough in _stress_windows(benchmark):
        stock_decline = _return(stock, start, trough)
        if stock_decline is None:
            continue
        recovery_days = None
        start_price = stock[start]
        for index in range(trough + 1, min(len(stock), trough + 366)):
            if stock[index] >= start_price:
                recovery_days = index - trough
                break
        horizons = {}
        for days in (30, 90, 180, 365):
            end = min(len(stock) - 1, trough + days)
            horizons[str(days)] = round((stock[end] / start_price - 1) * 100, 1)
        events.append({
            "benchmark": benchmark_name,
            "benchmark_decline_pct": round(benchmark_decline, 1),
            "stock_decline_pct": round(stock_decline, 1),
            "relative_protection_pp": round(stock_decline - benchmark_decline, 1),
            "recovery_days": recovery_days,
            "recovered_within_one_year": recovery_days is not None,
            "recovery_vs_pre_stress_pct": horizons,
            "window_sessions": 63,
        })
    if len(events) < 2:
        status = "insufficient_data"
        median_protection = recovery_rate = median_recovery = None
    else:
        median_protection = round(statistics.median(row["relative_protection_pp"] for row in events), 1)
        recovery_rate = round(sum(row["recovered_within_one_year"] for row in events) / len(events) * 100, 1)
        recovered_days = [row["recovery_days"] for row in events if row["recovery_days"] is not None]
        median_recovery = round(statistics.median(recovered_days)) if recovered_days else None
        if median_protection >= 0 and recovery_rate >= 60 and median_recovery is not None and median_recovery <= 180:
            status = "strong"
        elif median_protection >= -5 and recovery_rate >= 50:
            status = "acceptable"
        else:
            status = "weak"
    return {
        "version": VERSION,
        "status": status,
        "label": {"strong": "Strong prior stress recovery", "acceptable": "Acceptable prior stress recovery",
                  "weak": "Weak prior stress recovery", "insufficient_data": "Stress-recovery history incomplete"}[status],
        "event_count": len(events),
        "events": events,
        "summary": {"median_relative_protection_pp": median_protection,
                    "recovered_within_one_year_pct": recovery_rate,
                    "median_recovery_days": median_recovery},
        "candidate_rule": "Strong: median relative protection ≥0 pp, ≥60% recovered within one year and median recovery ≤180 days. Acceptable: protection ≥−5 pp and recovery ≥50%.",
        "backtestable": True,
        "validated_for_score": False,
        "score_effect": 0,
        "verdict_effect": "none",
        "observed_at": observed_at,
        "policy": "Replayable research evidence only; it does not predict the next shock or change the official score or verdict.",
        "data_note": "Live arrays are tail-aligned. Production promotion requires date-keyed point-in-time alignment in the historical replay.",
    }
