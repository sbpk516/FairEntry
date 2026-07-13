"""Finnhub adapter — company news (raw). Sentiment/stance is decided later by the
reasoning layer (LLM), not here. Live interface; news fetch ported from v1 in a
follow-up pass.
"""
from __future__ import annotations

OWNS = "finnhub"
IMPLEMENTED = False   # TODO: port v1 fetch_finnhub_news (90-day window, cached)


def fetch(cfg, field_ids, tickers=None):
    return {}
