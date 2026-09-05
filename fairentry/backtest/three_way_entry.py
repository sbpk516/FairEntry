"""Research-only predicates for fundamentals, valuation, and trend alignment."""
from __future__ import annotations

from numbers import Number


def _category(row: dict, category_id: str) -> float | None:
    for category in row.get("categories") or []:
        if category.get("id") == category_id and isinstance(category.get("score"), Number):
            return float(category["score"])
    return None


def fundamentals_strong(row: dict, minimum: float = 70) -> bool:
    scores = [_category(row, category_id)
              for category_id in ("quality", "survival", "growth")]
    return all(score is not None and score >= minimum for score in scores) \
        and not bool(row.get("vetoes"))


def valuation_aligned(row: dict, mode: str) -> bool:
    valuation = row.get("valuation") or {}
    if not isinstance(valuation.get("method_count"), Number) \
            or float(valuation["method_count"]) < 1:
        return False
    price = row.get("raw_close")
    if not isinstance(price, Number):
        return False
    if mode == "inside_fair_range":
        low, high = valuation.get("fair_low"), valuation.get("fair_high")
        return (isinstance(low, Number) and isinstance(high, Number)
                and float(low) <= float(price) <= float(high))
    if mode == "at_or_below_fair_base":
        fair = valuation.get("fair_base")
    elif mode == "at_or_below_buy_zone":
        fair = valuation.get("buy_zone")
    else:
        raise ValueError(f"unknown valuation alignment mode: {mode}")
    return isinstance(fair, Number) and float(price) <= float(fair)


def near_average(row: dict, field: str, threshold_pct: float) -> bool:
    distance = (row.get("research_factors") or {}).get(field)
    return isinstance(distance, Number) and abs(float(distance)) <= threshold_pct
