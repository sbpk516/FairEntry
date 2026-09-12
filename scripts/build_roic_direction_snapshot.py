"""Build derived live gate statuses from the local licensed warehouse (no raw ratios)."""
import json
import sys
from datetime import date
from pathlib import Path
import duckdb
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fairentry.analytics.roic_direction import historical

if __name__ == '__main__':
    asof = str(date.today())
    with duckdb.connect('data/sharadar/warehouse.duckdb', read_only=True) as con:
        tickers = [r[0] for r in con.execute("SELECT DISTINCT ticker FROM sfa_fundamentals WHERE dimension='ART' AND datekey>=CAST(? AS DATE)-INTERVAL 180 DAY", [asof]).fetchall()]
        rows = {t: historical(con,t,asof) for t in tickers}
    Path('config/roic_direction_snapshot.json').write_text(json.dumps({'asof': asof, 'source': 'Sharadar derived status; refresh locally; stale after 180 days', 'tickers':rows}, separators=(',',':')), encoding='utf-8')
    print('Derived ROIC statuses:', len(rows))
