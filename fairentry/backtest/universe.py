"""Issuer-level universe normalization shared by backtest implementations."""
from __future__ import annotations

import math
import re


def issuer_key(company: str | None, ticker: str | None = None) -> str:
    """Return a stable grouping key for multiple listed classes of one issuer."""
    normalized = re.sub(r"[^A-Z0-9]+", "", (company or "").upper())
    return normalized or (ticker or "UNKNOWN").upper()


def _number(value) -> float:
    if isinstance(value, dict):
        value = value.get("value")
    try:
        number = float(value)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _share_class_rank(category: str | None) -> int:
    category = (category or "").lower()
    if "primary class" in category:
        return 0
    if "preferred" in category:
        return 3
    if "secondary class" in category:
        return 2
    return 1


def _representative_rank(item: dict) -> tuple:
    sec, metrics = item["sec"], item.get("metrics", {})
    price = _number(metrics.get("price"))
    average_volume = _number(metrics.get("avg_volume"))
    liquidity = _number(metrics.get("avg_dollar_volume")) or price * average_volume
    market_cap = _number(metrics.get("market_cap"))
    return (
        _share_class_rank(sec.get("category")),
        -liquidity,
        -market_cap,
        sec.get("ticker") or "",
    )


def deduplicate_issuers(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Keep one representative security per issuer and return removed classes."""
    order: list[str] = []
    grouped: dict[str, list[dict]] = {}
    for item in items:
        sec = item["sec"]
        key = issuer_key(sec.get("company"), sec.get("ticker"))
        item["issuer_key"] = key
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(item)

    kept, removed = [], []
    for key in order:
        group = sorted(grouped[key], key=_representative_rank)
        representative = group[0]
        representative["excluded_share_classes"] = [
            row["sec"].get("ticker") for row in group[1:]
        ]
        kept.append(representative)
        for row in group[1:]:
            removed.append(
                {
                    "issuer_key": key,
                    "company": row["sec"].get("company"),
                    "ticker": row["sec"].get("ticker"),
                    "kept_ticker": representative["sec"].get("ticker"),
                    "reason": "duplicate_issuer_share_class",
                }
            )
    return kept, removed
