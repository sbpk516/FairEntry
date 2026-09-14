"""Export pre-strategy-screen research listings from the existing provider snapshot.

No fresh requests, store writes, scoring, alerts or changes to official membership.
"""
import copy
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from .adapters.finviz import CACHE, parse_rows
from .config import load_config
from .screeners.quality_growth import CRITERIA as Q
from .screeners.deep_value import CRITERIA as D

FIELDS = ['price', 'market_cap', 'avg_dollar_volume', 'rev_growth_qoq', 'gross_margin',
          'pb_ratio', 'ps_ratio', 'pfcf_ratio', 'perf_year', 'debt_eq']


def build(cfg, board=None, cache_path=CACHE):
    cfg = copy.deepcopy(cfg)
    defaults = dict(cfg.sectors['universe_filter'])
    # The provider export already excludes cap < $300M and price <= $1.
    # Retain that boundary; remove local liquidity and strategy restrictions.
    cfg.sectors['universe_filter'] = dict(market_cap_min_usd=300_000_000,
                                        price_min_usd=1, avg_dollar_volume_min=0)
    meta = {'source': 'Finviz cached pre-screen export',
            'sectors': [s['finviz'] for s in cfg.enabled_sectors],
            'defaults': defaults, 'quality_growth': Q, 'deep_value': D,
            'count_unit': 'stock listings (tickers), not deduplicated companies',
            'scope': 'Configured sectors only. The provider export starts at $300M market cap and prices above $1. It cannot answer questions about smaller companies or sub-$1 stocks. Counts are listings; multiple share classes can represent one company. Screening matches are not Buy recommendations.',
            'snapshot_at': None, 'available': False}
    if not cache_path.exists():
        return {'meta': meta, 'stocks': []}
    with cache_path.open(newline='', encoding='utf-8-sig') as f:
        securities, metrics = parse_rows(cfg, FIELDS, list(csv.DictReader(f)))
    official = {s['ticker']:s.get('verdict') for s in (board or {}).get('stocks', [])}
    stocks = []
    seen = set()
    for s in securities:
        if s['ticker'] in seen:
            continue
        seen.add(s['ticker'])
        m = {k: v if isinstance(v, (float,int)) and math.isfinite(v) else None
             for k,v in metrics[s['ticker']].items()}
        stocks.append(dict(s, metrics=m, official_verdict=official.get(s['ticker'])))
    meta.update(snapshot_at=datetime.fromtimestamp(cache_path.stat().st_mtime, timezone.utc).isoformat(),
                available=True, count=len(stocks))
    return {'meta':meta, 'stocks':stocks}


def write(cfg=None, board=None):
    target = Path(__file__).resolve().parents[1]/'web/data/screening-lab.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build(cfg or load_config(), board), ensure_ascii=False), encoding='utf-8')
    return target


if __name__ == '__main__':
    print(write())
