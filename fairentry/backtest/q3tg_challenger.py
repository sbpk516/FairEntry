"""Research-only predicates inspired by the Q3-TG strategy report."""
from __future__ import annotations

from numbers import Number


def report_stock_trend_passes(row: dict) -> bool:
    """Frozen partial Step-4 rule supported by the local price warehouse."""
    factors = row.get("research_factors") or {}
    extension = factors.get("price_to_sma50_pct")
    momentum = factors.get("momentum_12_1_pct")
    return (
        factors.get("price_above_sma200") is True
        and factors.get("sma200_rising_20_sessions") is True
        and isinstance(momentum, Number) and float(momentum) > 0
        and isinstance(extension, Number) and 0 <= float(extension) <= 8
    )


def market_regime_passes(row: dict) -> bool:
    return (row.get("research_factors") or {}).get(
        "spy_above_10month_sma"
    ) is True

