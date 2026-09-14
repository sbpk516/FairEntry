import csv
import copy

from fairentry.config import load_config
from fairentry.screening_lab import build


def test_lab_includes_pre_screen_and_low_liquidity_without_mutating_config(tmp_path):
    cfg = load_config()
    before = copy.deepcopy(cfg.sectors)
    path = tmp_path/'snapshot.csv'
    fields = ['Ticker','Company','Sector','Price','Market Cap','Average Volume','P/B','P/S','P/Free Cash Flow','Sales Q/Q','Gross Margin','Performance (Year)','Debt/Eq']
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        w.writerow(dict(Ticker='OUTSIDE',Company='Expensive Inc',Sector='Technology',Price='10',**{'Market Cap':'400','Average Volume':'1','P/B':'99','P/S':'99'}))
        w.writerow(dict(Ticker='SMALL',Sector='Technology',Price='10',**{'Market Cap':'100','Average Volume':'2000'}))
    data=build(cfg, {'stocks':[]}, path)
    assert [s['ticker'] for s in data['stocks']]==['OUTSIDE']
    assert data['stocks'][0]['official_verdict'] is None
    assert data['stocks'][0]['metrics']['avg_dollar_volume']==10000
    assert cfg.sectors==before
    assert data['meta']['snapshot_at']


def test_missing_snapshot_is_not_presented_as_zero_market_candidates(tmp_path):
    data=build(load_config(),cache_path=tmp_path/'missing.csv')
    assert data['meta']['available'] is False
