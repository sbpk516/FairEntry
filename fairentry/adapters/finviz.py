"""Finviz Elite adapter — fetches the whole liquid universe in one export call
and maps each catalog field (by its `raw_path` = Finviz column name) to a value.
Defines the ticker universe; other adapters enrich it.
"""
from __future__ import annotations

import csv
import io
import os
import time
from pathlib import Path

import requests

from .base import get_key, sf

FINVIZ_URL = "https://elite.finviz.com/export.ashx"
ROOT = Path(__file__).resolve().parent.parent.parent
CACHE = ROOT / "data" / "cache" / "finviz_universe.csv"
CACHE_TTL_SEC = 3600

OWNS = "finviz"   # adapter id


def _fetch_rows(force=False) -> list[dict]:
    if not force and CACHE.exists() and (time.time() - CACHE.stat().st_mtime) < CACHE_TTL_SEC:
        with open(CACHE, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    key = get_key("FINVIZ_API_KEY", required=True)
    params = {"v": "152", "f": "cap_smallover,sh_price_o1",
              "c": ",".join(str(i) for i in range(71)), "auth": key}
    resp = requests.get(FINVIZ_URL, params=params,
                        headers={"User-Agent": "FairEntry/0.1"}, timeout=60)
    resp.raise_for_status()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(resp.text, encoding="utf-8")
    os.replace(tmp, CACHE)
    return list(csv.DictReader(io.StringIO(resp.text)))


def fetch(cfg, field_ids, tickers=None, force=False):
    """Return (securities, metrics).
    securities: [{ticker,company,sector,industry,country}]
    metrics:    {ticker: {field_id: value}} for the finviz fields requested.
    """
    rows = _fetch_rows(force=force)
    finviz_fields = [cfg.field(fid) for fid in field_ids
                     if cfg.field(fid).get("adapter") == "finviz"]

    enabled = {s["finviz"] for s in cfg.enabled_sectors}
    uf = cfg.sectors.get("universe_filter", {})
    cap_min = uf.get("market_cap_min_usd", 0)
    price_min = uf.get("price_min_usd", 0)
    advol_min = uf.get("avg_dollar_volume_min", 0)

    securities, metrics = [], {}
    for r in rows:
        tkr = (r.get("Ticker") or "").strip()
        if not tkr:
            continue
        sector = (r.get("Sector") or "").strip()
        if enabled and sector not in enabled:
            continue
        price = sf(r.get("Price")) or 0
        cap = (sf(r.get("Market Cap")) or 0) * 1_000_000    # Finviz gives $M
        advol = price * (sf(r.get("Average Volume")) or 0) * 1_000  # Finviz vol is in thousands
        if price < price_min or cap < cap_min or advol < advol_min:
            continue
        if tickers and tkr not in tickers:
            continue
        securities.append({"ticker": tkr, "company": (r.get("Company") or "").strip(),
                           "sector": sector, "industry": (r.get("Industry") or "").strip(),
                           "country": (r.get("Country") or "").strip()})
        vals = {}
        for f in finviz_fields:
            raw = r.get(f.get("raw_path", ""))
            if f["id"] == "market_cap":
                vals[f["id"]] = cap
            elif f.get("unit") in ("text", "enum"):
                vals[f["id"]] = (raw or "").strip() or None
            else:
                vals[f["id"]] = sf(raw)
        metrics[tkr] = vals
    return securities, metrics
