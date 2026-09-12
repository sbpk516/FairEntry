from fairentry.analytics.roic_direction import assess, historical, live_assessment
import duckdb


def test_production_direction_examples():
    assert assess([15,18,18],18)['passes']
    assert assess([-10,5,60],200)['passes']
    assert not assess([30,20,15],15)['passes']
    assert not assess([20,18.5,17],17)['passes']
    assert not assess([None,18,18],18)['passes']


def test_pit_excludes_future_revisions():
    c = duckdb.connect(':memory:')
    c.execute('CREATE TABLE sfa_fundamentals(ticker VARCHAR,dimension VARCHAR,reportperiod DATE,datekey DATE,roic DOUBLE)')
    for yr, v in [(2018,.15),(2019,.18),(2020,.18)]:
        c.execute("INSERT INTO sfa_fundamentals VALUES ('T','ARY',?,?,?)",[f'{yr}-12-31',f'{yr+1}-02-01',v])
    c.execute("INSERT INTO sfa_fundamentals VALUES ('T','ART','2020-12-31','2021-02-01',.18),('T','ARY','2020-12-31','2022-02-01',.01)")
    assert historical(c,'T','2021-03-01')['passes']
    assert not historical(c,'T','2022-03-01')['passes']
    c.close()


def test_live_future_snapshot_is_not_used(monkeypatch):
    monkeypatch.setattr('fairentry.analytics.roic_direction.snapshot', lambda: {'asof':'2030-01-01','tickers':{'T':{'passes':True,'evidence_date':'2025-01-01','annual_period':'2024-12-31'}}})
    assert not live_assessment('T','2025-02-01')['passes']
