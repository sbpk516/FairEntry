"""Finnhub adapter — company news (raw headlines).

Design: this adapter fetches *raw* news only. Sentiment / stance / whether a
headline is bullish is decided later by the reasoning layer (LLM), never by
keyword polarity here (v1's keyword tagger mis-called "2 Reasons to Sell" as
bullish — the exact bug we're avoiding). The only lightweight, deterministic
thing we do is tag a headline with a *catalyst category* (earnings, guidance,
M&A, …) so the UI can show "recent catalyst" chips; polarity stays with the LLM.

News is expensive-ish (one HTTP call per ticker, rate-limited) so it is fetched
shortlist-only from the reasoning layer, and cached with a short TTL.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

import requests

from .base import get_key
from .cache_lite import cache_get, cache_put

OWNS = "finnhub"
IMPLEMENTED = True

_NEWS_URL = "https://finnhub.io/api/v1/company-news"
_RATE_LIMIT_S = 1.1          # free tier: 60 calls/min
_CACHE_TTL_DAYS = 1          # news is time-sensitive; refetch daily

# Catalyst *category* tags only (no polarity — that's the LLM's job).
_CATALYST_PATTERNS = [
    (re.compile(r"\b(earnings|q[1-4]\s|quarter|results|beats?|missed?|revenue)\b"), "earnings"),
    (re.compile(r"\b(guidance|outlook|forecast|raises?|cuts?|lowers?)\b"), "guidance"),
    (re.compile(r"\b(acqui|merger|buyout|takeover|deal|stake)\b"), "m&a"),
    (re.compile(r"\b(upgrade|downgrade|price target|initiate|analyst|rating)\b"), "analyst"),
    (re.compile(r"\b(fda|approval|trial|patent|launch|unveils?|partnership)\b"), "product"),
    (re.compile(r"\b(lawsuit|probe|investigat|sec |fraud|recall|breach|settle)\b"), "legal"),
    (re.compile(r"\b(buyback|dividend|split|repurchase)\b"), "capital"),
    (re.compile(r"\b(ceo|cfo|resign|appoints?|steps? down|layoff|restructur)\b"), "management"),
]


def _tag(headline: str, summary: str = "") -> list[str]:
    text = f"{headline} {summary}".lower()
    return [cat for pat, cat in _CATALYST_PATTERNS if pat.search(text)]


def fetch_news(ticker: str, lookback_days: int = 60, limit: int = 12) -> list[dict]:
    """Return recent company news (newest first) as
    [{date, headline, summary, url, source, categories}] — or [] if no key /
    call fails. Cached (short TTL). Never raises.
    """
    key = get_key("FINNHUB_API_KEY")
    if not key:
        return []
    ck = f"{ticker}_{lookback_days}d"
    cached = cache_get("finnhub_news", ck, _CACHE_TTL_DAYS)
    if cached is not None:
        return cached

    today = datetime.now(timezone.utc).date()
    frm = (today - timedelta(days=lookback_days)).isoformat()
    try:
        time.sleep(_RATE_LIMIT_S)
        resp = requests.get(_NEWS_URL, timeout=20, params={
            "symbol": ticker, "from": frm, "to": today.isoformat(), "token": key})
        items = resp.json() if resp.status_code == 200 else []
    except Exception:
        items = []
    if not isinstance(items, list):
        items = []

    news, seen = [], set()
    for it in items:
        head = (it.get("headline") or "").strip()
        low = head.lower()
        if not head or low in seen:
            continue
        seen.add(low)
        ts = it.get("datetime", 0)
        d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else ""
        summ = (it.get("summary") or "").strip()[:280]
        news.append({
            "date": d,
            "headline": head[:200],
            "summary": summ,
            "url": it.get("url", ""),
            "source": (it.get("source") or "").strip()[:40],
            "categories": _tag(head, summ),
        })
    news.sort(key=lambda n: n["date"], reverse=True)
    news = news[:limit]
    cache_put("finnhub_news", ck, news)
    return news


def fetch(cfg, field_ids, tickers=None):
    """Catalog interface — company_news is fed to the reasoning layer directly
    (shortlist-only via fetch_news), not stored per-field here. Returns {} so the
    catalog refresh treats finnhub as a no-op source at the field level.
    """
    return {}
