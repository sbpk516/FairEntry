"""Shared six-month sector-relative momentum evidence.

The calculation is deliberately small and deterministic so the current board
and the historical replay can use the identical definition.  It is research
evidence only and never changes a FairEntry score or verdict.
"""
from __future__ import annotations

import math


LOOKBACK_SESSIONS = 126
CHANGE_LOOKBACK_SESSIONS = 21
MINIMUM_SESSIONS = LOOKBACK_SESSIONS + CHANGE_LOOKBACK_SESSIONS + 1


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and value > 0 else None
    except (TypeError, ValueError):
        return None


def _return_pct(closes: list[float], end_offset: int = 0):
    end_index = len(closes) - 1 - end_offset
    start_index = end_index - LOOKBACK_SESSIONS
    if start_index < 0 or end_index < 0:
        return None
    start, end = closes[start_index], closes[end_index]
    if start <= 0:
        return None
    return (end / start - 1) * 100


def calculate_relative_momentum(
    stock_closes: list[float],
    sector_closes: list[float],
) -> dict:
    """Classify a frozen six-month stock return relative to its sector.

    Series are expected in ascending trading-session order.  Each leg requires
    148 sessions: 126 for the six-month return and another 21 to calculate the
    same six-month relative return one month earlier.
    """
    stock = [value for item in stock_closes if (value := _number(item)) is not None]
    sector = [value for item in sector_closes if (value := _number(item)) is not None]
    base = {
        "available": False,
        "required_sessions": MINIMUM_SESSIONS,
        "stock_sessions": len(stock),
        "sector_sessions": len(sector),
        "lookback_sessions": LOOKBACK_SESSIONS,
        "change_lookback_sessions": CHANGE_LOOKBACK_SESSIONS,
        "score_effect": 0,
        "verdict_effect": "none",
        "future_data_used": False,
    }
    if len(stock) < MINIMUM_SESSIONS or len(sector) < MINIMUM_SESSIONS:
        return {
            **base,
            "classification": "unavailable",
            "display_direction": "Unavailable",
            "reason": "Fewer than 148 stock or sector trading sessions are available.",
        }

    stock_current = _return_pct(stock)
    sector_current = _return_pct(sector)
    stock_prior = _return_pct(stock, CHANGE_LOOKBACK_SESSIONS)
    sector_prior = _return_pct(sector, CHANGE_LOOKBACK_SESSIONS)
    if None in (stock_current, sector_current, stock_prior, sector_prior):
        return {
            **base,
            "classification": "unavailable",
            "display_direction": "Unavailable",
            "reason": "The six-month relative-return calculation is incomplete.",
        }

    relative = stock_current - sector_current
    prior_relative = stock_prior - sector_prior
    change = relative - prior_relative
    if relative > 0 and change > 0:
        classification, display = "improving", "Supportive"
    elif relative < 0 and change < 0:
        classification, display = "deteriorating", "Contradictory"
    else:
        classification, display = "neutral", "Neutral"
    return {
        **base,
        "available": True,
        "classification": classification,
        "display_direction": display,
        "reason": None,
        "stock_return_6m_pct": round(stock_current, 2),
        "sector_return_6m_pct": round(sector_current, 2),
        "relative_momentum_6m_pct": round(relative, 2),
        "prior_stock_return_6m_pct": round(stock_prior, 2),
        "prior_sector_return_6m_pct": round(sector_prior, 2),
        "prior_relative_momentum_6m_pct": round(prior_relative, 2),
        "relative_momentum_change_1m_pp": round(change, 2),
    }


def build_high_confidence_shadow(official_verdict: str | None, evidence: dict | None) -> dict:
    """Translate the frozen evidence into a visible, zero-effect shadow label.

    The label is intentionally conditional on an existing official Buy. It
    does not manufacture a Buy from momentum and it cannot alter scoring.
    """
    evidence = evidence or {}
    classification = evidence.get("classification", "unavailable")
    display = evidence.get("display_direction", "Unavailable")
    is_official_buy = official_verdict == "Buy"
    eligible = bool(is_official_buy and classification == "improving")
    if eligible:
        status = "high_confidence_buy"
        label = "High-Confidence Buy · shadow"
        reason = (
            "The official Buy also has positive six-month sector-relative "
            "momentum that improved versus 21 trading sessions earlier."
        )
    elif is_official_buy:
        status = "momentum_not_confirmed"
        label = f"Official Buy · momentum {str(display).lower()}"
        reason = (
            "The official Buy remains unchanged, but the frozen sector-relative "
            "momentum rule is not supportive."
        )
    else:
        status = "not_applicable"
        label = "Not an official Buy"
        reason = "Momentum cannot create a Buy when the official FairEntry verdict is not Buy."
    return {
        "status": status,
        "label": label,
        "eligible": eligible,
        "official_verdict": official_verdict,
        "momentum_classification": classification,
        "momentum_label": display,
        "research_only": True,
        "score_effect": 0,
        "verdict_effect": "none",
        "reason": reason,
        "promotion_policy": (
            "Keep in shadow mode until the frozen rule has adequate new outcomes "
            "and passes the documented sample, improvement, and drawdown checks."
        ),
    }
