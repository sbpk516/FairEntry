"""Information-only entry and exit market evidence.

The calculations in this module are deterministic functions of historical
price and volume arrays.  They are therefore replayable at any point in time,
but they never create a score, gate, Buy, or Sell decision.  Correlated inputs
are grouped into four families so that several moving-average checks cannot
pretend to be several independent confirmations.
"""
from __future__ import annotations

import math
import statistics


EVIDENCE_VERSION = "entry_exit_evidence_v1"


def _number(value):
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


def _pct(numerator, denominator):
    if numerator is None or denominator in (None, 0):
        return None
    return round((numerator / denominator - 1) * 100, 2)


def _mean(values):
    clean = [_number(value) for value in values]
    clean = [value for value in clean if value is not None]
    return statistics.fmean(clean) if clean else None


def _ma(values: list[float], days: int, *, offset: int = 0):
    end = len(values) - offset
    start = end - days
    if start < 0 or end <= start:
        return None
    return _mean(values[start:end])


def _return(values: list[float], days: int, *, offset: int = 0):
    end = len(values) - 1 - offset
    start = end - days
    if start < 0 or end < 0:
        return None
    return _pct(_number(values[end]), _number(values[start]))


def _relative_alpha(closes, benchmark, days, *, offset=0):
    stock = _return(closes, days, offset=offset)
    base = _return(benchmark, days, offset=offset)
    return round(stock - base, 2) if stock is not None and base is not None else None


def _confidence(required: int, available: int):
    if available >= required:
        return "high"
    if available >= max(20, required // 2):
        return "medium"
    return "low"


def _metric(metric_id, label, value, unit, evidence, *, reading="context",
            lookback="", formula="", supportive_when="", cautionary_when="",
            source="adjusted daily OHLCV history"):
    if value is None:
        reading = "unavailable"
    return {
        "id": metric_id,
        "label": label,
        "value": None if value is None else round(value, 2),
        "unit": unit,
        "reading": reading,
        "lookback": lookback,
        "formula": formula,
        "supportive_when": supportive_when,
        "cautionary_when": cautionary_when,
        "evidence": evidence,
        "source": source,
        "backtestable": True,
        "score_effect": 0,
        "verdict_effect": "none",
    }


def _family(family_id, label, state, confidence, observations, explanation,
            decision_rule=""):
    known = [row for row in observations if row["value"] is not None]
    impact = "high" if state in {"supportive", "cautionary"} and len(known) >= 3 else (
        "medium" if known else "unknown"
    )
    return {
        "id": family_id,
        "label": label,
        "state": state,
        "impact": impact,
        "confidence": confidence,
        "observations": observations,
        "explanation": explanation,
        "decision_rule": decision_rule,
        "available_observations": len(known),
        "supportive_observations": sum(row.get("reading") == "supportive" for row in known),
        "cautionary_observations": sum(row.get("reading") == "cautionary" for row in known),
        "agreement_role": "This family contributes at most one agreement vote.",
        "vote_count": 1 if state in {"supportive", "cautionary"} else 0,
    }


def _trend_family(closes):
    if len(closes) < 50:
        return _family("trend", "Trend", "unavailable", "low", [],
                       "At least 50 daily closes are required.")
    price = _number(closes[-1])
    ma50, ma200 = _ma(closes, 50), _ma(closes, 200)
    prior50, prior200 = _ma(closes, 50, offset=20), _ma(closes, 200, offset=20)
    slope50, slope200 = _pct(ma50, prior50), _pct(ma200, prior200)
    price50, price200 = _pct(price, ma50), _pct(price, ma200)
    current_low = min(closes[-20:]) if len(closes) >= 40 else None
    prior_low = min(closes[-40:-20]) if len(closes) >= 40 else None
    higher_low = _pct(current_low, prior_low)
    checks = [
        price50 is not None and price50 > 0,
        price200 is not None and price200 > 0,
        slope50 is not None and slope50 > 0,
        slope200 is not None and slope200 >= 0,
        higher_low is not None and higher_low >= 0,
    ]
    known = sum(value is not None for value in (price50, price200, slope50, slope200, higher_low))
    positives = sum(checks)
    if known < 3:
        state = "unavailable"
    elif (price200 is not None and price200 < 0 and slope200 is not None and slope200 < 0) or positives <= 1:
        state = "cautionary"
    elif positives >= 4:
        state = "supportive"
    else:
        state = "mixed"
    observations = [
        _metric("price_vs_50dma", "Price versus 50-day average", price50, "%", "Positive means price is above its intermediate trend.",
                reading="supportive" if price50 is not None and price50 > 0 else "cautionary",
                lookback="50 trading sessions", formula="(latest adjusted close / 50-day average − 1) × 100",
                supportive_when="> 0%", cautionary_when="< 0%"),
        _metric("price_vs_200dma", "Price versus 200-day average", price200, "%", "Positive means price is above its long-term daily trend.",
                reading="supportive" if price200 is not None and price200 > 0 else "cautionary",
                lookback="200 trading sessions", formula="(latest adjusted close / 200-day average − 1) × 100",
                supportive_when="> 0%", cautionary_when="< 0%"),
        _metric("slope_50dma_20d", "50-day average change over 20 sessions", slope50, "%", "A rising average supports trend persistence.",
                reading="supportive" if slope50 is not None and slope50 > 0 else "cautionary",
                lookback="50-day averages measured 20 sessions apart", formula="(current 50DMA / 50DMA 20 sessions ago − 1) × 100",
                supportive_when="> 0%", cautionary_when="< 0%"),
        _metric("slope_200dma_20d", "200-day average change over 20 sessions", slope200, "%", "A stable or rising long-term average distinguishes healing from a falling-average crossover.",
                reading="supportive" if slope200 is not None and slope200 >= 0 else "cautionary",
                lookback="200-day averages measured 20 sessions apart", formula="(current 200DMA / 200DMA 20 sessions ago − 1) × 100",
                supportive_when="≥ 0%", cautionary_when="< 0%"),
        _metric("higher_low_20d", "Recent 20-day low versus prior 20-day low", higher_low, "%", "Positive means the latest swing low is higher.",
                reading="supportive" if higher_low is not None and higher_low >= 0 else "cautionary",
                lookback="two consecutive 20-session windows", formula="(latest 20-day low / preceding 20-day low − 1) × 100",
                supportive_when="≥ 0%", cautionary_when="< 0%"),
    ]
    return _family("trend", "Trend", state, _confidence(220, len(closes)), observations,
                   f"{positives} of {known} available trend observations are supportive.",
                   "Supportive when at least 4 of 5 observations support the trend. Cautionary when at most 1 supports it, or price and the 200DMA are both deteriorating; otherwise Mixed.")


def _momentum_family(closes, sector_closes, spy_closes):
    rows = []
    current_alphas = []
    current_1m = None
    for days, label in ((21, "1 month"), (63, "3 months"), (126, "6 months")):
        market = _relative_alpha(closes, spy_closes, days)
        sector = _relative_alpha(closes, sector_closes, days)
        values = [value for value in (market, sector) if value is not None]
        combined = round(statistics.fmean(values), 2) if values else None
        if days == 21:
            current_1m = combined
        if combined is not None:
            current_alphas.append(combined)
        rows.append(_metric(f"relative_strength_{days}d", f"Relative strength — {label}", combined,
                            "percentage points", "Average excess return versus SPY and the sector ETF.",
                            reading="supportive" if combined is not None and combined > 0 else "cautionary",
                            lookback=f"{days} trading sessions",
                            formula="average(stock return − SPY return, stock return − sector ETF return)",
                            supportive_when="> 0 percentage points", cautionary_when="< 0 percentage points",
                            source="adjusted closes for the stock, SPY and sector ETF"))
    prior_values = [
        value for value in (
            _relative_alpha(closes, spy_closes, 21, offset=21),
            _relative_alpha(closes, sector_closes, 21, offset=21),
        ) if value is not None
    ]
    prior_1m = statistics.fmean(prior_values) if prior_values else None
    acceleration = round(current_1m - prior_1m, 2) if current_1m is not None and prior_1m is not None else None
    rows.append(_metric("relative_strength_acceleration", "Relative-strength acceleration",
                        acceleration, "percentage points", "Current one-month relative return minus the preceding one-month relative return.",
                        reading="supportive" if acceleration is not None and acceleration >= 0 else "cautionary",
                        lookback="two consecutive 21-session windows",
                        formula="current 21-day relative return − preceding 21-day relative return",
                        supportive_when="≥ 0 percentage points", cautionary_when="< 0 percentage points",
                        source="adjusted closes for the stock, SPY and sector ETF"))
    positives = sum(value > 0 for value in current_alphas)
    negatives = sum(value < 0 for value in current_alphas)
    if len(current_alphas) < 2:
        state = "unavailable"
    elif positives >= 2 and (acceleration is None or acceleration >= 0):
        state = "supportive"
    elif negatives >= 2 and acceleration is not None and acceleration < 0:
        state = "cautionary"
    else:
        state = "mixed"
    return _family("momentum", "Momentum", state, _confidence(148, min(len(closes), len(spy_closes))), rows,
                   f"Relative performance is positive in {positives} and negative in {negatives} measured horizons.",
                   "Supportive when at least 2 measured horizons outperform and one-month relative strength is stable or accelerating. Cautionary when at least 2 underperform and acceleration is negative; otherwise Mixed.")


def _volume_family(closes, volumes):
    usable = min(len(closes), len(volumes))
    if usable < 51:
        return _family("volume", "Volume", "unavailable", "low", [],
                       "At least 51 aligned closes and volume observations are required.")
    c, v = closes[-usable:], volumes[-usable:]
    up = down = flat = 0.0
    for index in range(usable - 20, usable):
        dollar_volume = max(0.0, _number(v[index]) or 0.0) * max(0.0, _number(c[index]) or 0.0)
        if c[index] > c[index - 1]:
            up += dollar_volume
        elif c[index] < c[index - 1]:
            down += dollar_volume
        else:
            flat += dollar_volume
    up_down = round(up / down, 2) if down > 0 else (3.0 if up > 0 else None)
    total = up + down + flat
    balance = round((up - down) / total * 100, 2) if total > 0 else None
    recent5 = _mean(v[-5:])
    prior50 = _mean(v[-55:-5])
    recent_ratio = round(recent5 / prior50, 2) if recent5 is not None and prior50 not in (None, 0) else None
    return5 = _return(c, 5)
    pullback = None
    if return5 is not None and return5 < 0 and recent_ratio is not None:
        pullback = "contracting" if recent_ratio < .8 else "expanding" if recent_ratio > 1.2 else "normal"
    positives = sum((up_down is not None and up_down >= 1.2,
                     balance is not None and balance >= 10,
                     pullback == "contracting"))
    cautions = sum((up_down is not None and up_down <= .8,
                    balance is not None and balance <= -10,
                    pullback == "expanding"))
    if up_down is None or balance is None:
        state = "unavailable"
    elif positives >= 2:
        state = "supportive"
    elif cautions >= 2:
        state = "cautionary"
    else:
        state = "mixed"
    rows = [
        _metric("up_down_dollar_volume_20d", "20-day up/down dollar-volume ratio", up_down, "x", "Values above one indicate more dollar participation on advancing days.",
                reading="supportive" if up_down is not None and up_down >= 1.2 else "cautionary" if up_down is not None and up_down <= .8 else "mixed",
                lookback="20 trading sessions", formula="advancing-day dollar volume / declining-day dollar volume",
                supportive_when="≥ 1.20×", cautionary_when="≤ 0.80×"),
        _metric("signed_dollar_volume_balance_20d", "20-day signed dollar-volume balance", balance, "%", "Positive means advancing-day dollar volume exceeded declining-day dollar volume.",
                reading="supportive" if balance is not None and balance >= 10 else "cautionary" if balance is not None and balance <= -10 else "mixed",
                lookback="20 trading sessions", formula="(up-day dollar volume − down-day dollar volume) / total dollar volume × 100",
                supportive_when="≥ +10%", cautionary_when="≤ −10%"),
        _metric("recent_volume_vs_prior_50d", "Recent five-day volume versus prior 50 days", recent_ratio, "x", "Falling volume during a pullback is less cautionary than expanding selling volume.",
                reading="supportive" if pullback == "contracting" else "cautionary" if pullback == "expanding" else "context",
                lookback="latest 5 sessions versus preceding 50", formula="average volume latest 5 sessions / average volume preceding 50 sessions",
                supportive_when="< 0.80× during a pullback", cautionary_when="> 1.20× during a pullback"),
        _metric("price_return_5d", "Five-day price change", return5, "%", f"Pullback-volume condition: {pullback or 'not applicable'}.",
                reading="context", lookback="5 trading sessions", formula="(latest adjusted close / close 5 sessions earlier − 1) × 100",
                supportive_when="Context for volume contraction", cautionary_when="Context for expanding sell volume"),
    ]
    return _family("volume", "Volume", state, _confidence(55, usable), rows,
                   f"Volume evidence has {positives} supportive and {cautions} cautionary observations.",
                   "Supportive when at least 2 volume observations show accumulation or contracting pullback volume. Cautionary when at least 2 show distribution or expanding sell volume; otherwise Mixed.")


def _daily_returns(closes):
    returns = []
    for previous, current in zip(closes, closes[1:]):
        if previous and previous > 0:
            returns.append(current / previous - 1)
    return returns


def _realized_volatility(returns, days):
    if len(returns) < days:
        return None
    sample = returns[-days:]
    return statistics.pstdev(sample) * math.sqrt(252) * 100


def _atr_series(highs, lows, closes, days=14):
    usable = min(len(highs), len(lows), len(closes))
    if usable < days + 1:
        return []
    h, l, c = highs[-usable:], lows[-usable:], closes[-usable:]
    true_ranges = []
    for index in range(1, usable):
        true_ranges.append(max(h[index] - l[index], abs(h[index] - c[index - 1]), abs(l[index] - c[index - 1])))
    return [statistics.fmean(true_ranges[index - days:index]) for index in range(days, len(true_ranges) + 1)]


def _volatility_family(closes, highs, lows):
    returns = _daily_returns(closes)
    vol20, vol126 = _realized_volatility(returns, 20), _realized_volatility(returns, 126)
    vol_ratio = round(vol20 / vol126, 2) if vol20 is not None and vol126 not in (None, 0) else None
    recent63 = returns[-63:] if len(returns) >= 63 else []
    upside = [value for value in recent63 if value > 0]
    downside = [-value for value in recent63 if value < 0]
    up_vol = statistics.pstdev(upside) if len(upside) >= 5 else None
    down_vol = statistics.pstdev(downside) if len(downside) >= 5 else None
    downside_ratio = round(down_vol / up_vol, 2) if down_vol is not None and up_vol not in (None, 0) else None
    atr_values = _atr_series(highs, lows, closes)
    atr_pct = round(atr_values[-1] / closes[-1] * 100, 2) if atr_values and closes[-1] else None
    atr_window = atr_values[-252:]
    atr_percentile = None
    if atr_values and len(atr_window) >= 20:
        atr_percentile = round(sum(value <= atr_values[-1] for value in atr_window) / len(atr_window) * 100, 1)
    if vol_ratio is None and downside_ratio is None:
        state = "unavailable"
    elif ((vol_ratio is not None and vol_ratio >= 1.25 and downside_ratio is not None and downside_ratio >= 1.2)
          or (atr_percentile is not None and atr_percentile >= 80 and downside_ratio is not None and downside_ratio > 1)):
        state = "cautionary"
    elif vol_ratio is not None and vol_ratio <= .85 and (downside_ratio is None or downside_ratio <= 1.1):
        state = "supportive"
    else:
        state = "mixed"
    rows = [
        _metric("realized_volatility_20d", "20-day annualized volatility", vol20, "%", "Recent close-to-close realized volatility.",
                reading="context", lookback="20 trading sessions", formula="standard deviation of daily returns × √252 × 100",
                supportive_when="Used through the 20d/126d ratio", cautionary_when="Used through the 20d/126d ratio"),
        _metric("volatility_20d_vs_126d", "20-day versus 126-day volatility", vol_ratio, "x", "Values above one indicate recent volatility expansion.",
                reading="supportive" if vol_ratio is not None and vol_ratio <= .85 else "cautionary" if vol_ratio is not None and vol_ratio >= 1.25 else "mixed",
                lookback="20 and 126 trading sessions", formula="20-day annualized volatility / 126-day annualized volatility",
                supportive_when="≤ 0.85×", cautionary_when="≥ 1.25×"),
        _metric("downside_upside_volatility_63d", "Downside/upside volatility ratio", downside_ratio, "x", "Values above one indicate larger downside moves than upside moves.",
                reading="supportive" if downside_ratio is not None and downside_ratio <= 1.1 else "cautionary" if downside_ratio is not None and downside_ratio >= 1.2 else "mixed",
                lookback="63 trading sessions", formula="standard deviation of negative-return magnitudes / standard deviation of positive returns",
                supportive_when="≤ 1.10×", cautionary_when="≥ 1.20×"),
        _metric("atr_14_pct", "14-day ATR as a percentage of price", atr_pct, "%", "True-range volatility normalized by price.",
                reading="context", lookback="14 trading sessions", formula="14-day average true range / latest adjusted close × 100",
                supportive_when="Context; evaluated with ATR percentile and downside asymmetry", cautionary_when="Context; evaluated with ATR percentile and downside asymmetry"),
        _metric("atr_14_percentile", "14-day ATR historical percentile", atr_percentile, "percentile", "Current ATR compared with its available trailing history.",
                reading="cautionary" if atr_percentile is not None and atr_percentile >= 80 and downside_ratio is not None and downside_ratio > 1 else "context",
                lookback="up to 252 rolling ATR observations", formula="percentage of trailing ATR values less than or equal to current ATR",
                supportive_when="Context when downside volatility is controlled", cautionary_when="≥ 80th percentile with downside/upside ratio > 1"),
    ]
    return _family("volatility", "Volatility", state, _confidence(140, len(closes)), rows,
                   "Volatility is interpreted by expansion/contraction and downside asymmetry, not by direction prediction.",
                   "Supportive when short-term volatility is at most 0.85× longer-term volatility and downside asymmetry is controlled. Cautionary when volatility expands to at least 1.25× with downside asymmetry, or high-percentile ATR is downside-heavy; otherwise Mixed.")


def build_entry_exit_evidence(closes: list[float], volumes: list[float],
                              sector_closes: list[float], spy_closes: list[float],
                              *, highs: list[float] | None = None,
                              lows: list[float] | None = None,
                              observed_at=None) -> dict:
    """Build four information-only evidence families from an as-of data slice."""
    highs, lows = highs or [], lows or []
    families = [
        _trend_family(closes),
        _momentum_family(closes, sector_closes, spy_closes),
        _volume_family(closes, volumes),
        _volatility_family(closes, highs, lows),
    ]
    supportive = [family["id"] for family in families if family["state"] == "supportive"]
    cautionary = [family["id"] for family in families if family["state"] == "cautionary"]
    available = [family["id"] for family in families if family["state"] != "unavailable"]
    if not available:
        entry_alignment, exit_pressure = "unavailable", "unavailable"
    else:
        entry_alignment = (
            "supportive" if len(supportive) >= 3 and len(cautionary) <= 1 else
            "constructive" if len(supportive) == 2 and len(cautionary) <= 1 else
            "cautionary" if len(cautionary) >= 3 else "mixed"
        )
        exit_pressure = "elevated" if len(cautionary) >= 3 else "moderate" if len(cautionary) == 2 else "low"
    return {
        "version": EVIDENCE_VERSION,
        "context_only": True,
        "not_scored": True,
        "score_effect": 0,
        "verdict_effect": "none",
        "automatic_trade_effect": "none",
        "observed_at": observed_at,
        "entry_alignment": entry_alignment,
        "exit_pressure": exit_pressure,
        "agreement": {
            "supportive_families": supportive,
            "cautionary_families": cautionary,
            "available_families": available,
            "supportive_count": len(supportive),
            "cautionary_count": len(cautionary),
            "available_count": len(available),
        },
        "decision_rules": {
            "entry_alignment": "Supportive: at least 3 supportive families and no more than 1 cautionary. Constructive: exactly 2 supportive and no more than 1 cautionary. Cautionary: at least 3 cautionary. Otherwise Mixed.",
            "exit_pressure": "Elevated: at least 3 cautionary families. Moderate: exactly 2 cautionary families. Low: zero or one cautionary family.",
            "correlation_control": "Trend, Momentum, Volume and Volatility each contribute at most one family vote regardless of how many underlying indicators they contain.",
        },
        "data_inputs": [
            "point-in-time adjusted stock closes, highs, lows and volume",
            "point-in-time adjusted SPY closes",
            "point-in-time adjusted sector ETF closes",
        ],
        "families": families,
        "policy": (
            "Evidence describes current trend, momentum, volume and volatility. "
            "It does not predict returns, change a score or verdict, gate an entry, or trigger an automatic exit."
        ),
        "backtestability": (
            "Replayable from point-in-time adjusted OHLCV plus SPY and sector ETF history; "
            "each historical evaluation must truncate every series at the observation date."
        ),
    }
