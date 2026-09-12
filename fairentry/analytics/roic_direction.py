"""Shared recent-ROIC direction gate; no valuation or level requirements."""
import json
import math
from datetime import date
from functools import lru_cache
from pathlib import Path


def assess(annual, latest):
    values = list(annual)[-3:]
    valid = lambda x: isinstance(x, (float, int)) and not isinstance(x, bool) and math.isfinite(x)
    if len(values) != 3 or not all(valid(x) for x in values) or not valid(latest):
        return {'passes': False, 'reason': 'Recent ROIC history is insufficient; review before buying.'}
    changes = [b-a for a,b in zip(values, values[1:])] + [latest-values[-1]]
    declines = any(x < -2-1e-9 for x in changes) or latest < values[0]-2-1e-9
    return {'passes': not declines, 'reason': 'Recent ROIC is deteriorating; review before buying.' if declines else 'Recent ROIC is stable or recovering.'}


def historical(con, ticker, asof):
    rows = con.execute('''SELECT reportperiod,roic FROM sfa_fundamentals
        WHERE ticker=? AND dimension='ARY' AND datekey<=CAST(? AS DATE)
        AND reportperiod<=CAST(? AS DATE) AND reportperiod>=CAST(? AS DATE)-INTERVAL 6 YEAR
        QUALIFY row_number() OVER(PARTITION BY year(reportperiod) ORDER BY reportperiod DESC,datekey DESC)=1
        ORDER BY reportperiod''', [ticker,asof,asof,asof]).fetchall()
    latest = con.execute('''SELECT roic,datekey FROM sfa_fundamentals WHERE ticker=? AND dimension='ART'
        AND datekey<=CAST(? AS DATE) AND reportperiod<=CAST(? AS DATE)
        ORDER BY reportperiod DESC,datekey DESC LIMIT 1''', [ticker,asof,asof]).fetchone()
    result = assess([v*100 if v is not None else None for _,v in rows], latest[0]*100 if latest and latest[0] is not None else None)
    result['evidence_date'] = str(latest[1]) if latest else None
    result['annual_period'] = str(rows[-1][0]) if rows else None
    return result


@lru_cache(maxsize=1)
def snapshot():
    path = Path(__file__).resolve().parents[2] / 'config' / 'roic_direction_snapshot.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def live_assessment(ticker, asof):
    data = snapshot()
    row = dict(data.get('tickers', {}).get(ticker) or assess([], None))
    today = date.fromisoformat(str(asof)[:10])
    dates = [row.get('evidence_date'), data.get('asof'), row.get('annual_period')]
    if any(not value for value in dates):
        return assess([], None)
    ages = [(today-date.fromisoformat(value)).days for value in dates]
    if any(age < 0 for age in ages) or ages[0] > 180 or ages[1] > 180 or ages[2] > 550:
        row.update(passes=False, reason='ROIC history is stale or unavailable for this date; review before buying.')
    return row
