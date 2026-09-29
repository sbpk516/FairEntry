from datetime import date, datetime, timezone

import pandas as pd
import pytest

from fairentry.adapters.market_valuation import (
    DownloadLinks, buffett_history, build_market_valuation, cape_history, parse_fred,
)


def test_buffett_matches_quarters_converts_units_and_excludes_old_definition():
    quarters = [date(1996, 10, 1), date(2026, 1, 1), date(2026, 4, 1)]
    equities = dict(zip(quarters, [100, 60_000_000, 90_000_000]))
    gdp = {quarters[0]: 10, quarters[1]: 30_000, date(2026, 7, 1): 40_000}
    assert buffett_history(equities, gdp, date(2026, 9, 27)) == [{
        'date': '2026-03-31', 'value': 200.0,
        'equity_millions': 60_000_000.0, 'gdp_billions_annualized': 30_000.0,
    }]
    assert buffett_history(equities, gdp, date(2026, 2, 1)) == []


def test_fred_missing_nonfinite_and_bad_schema():
    result = parse_fred('observation_date,X\n2026-01-01,.\n2026-04-01,NaN\n2026-07-01,12\n', 'X')
    assert result == {date(2026, 7, 1): 12.0}
    with pytest.raises(ValueError):
        parse_fred('<html>error</html>', 'X')


def test_cape_selects_conventional_ratio_and_handles_october_numeric_dates():
    frame = pd.DataFrame([
        ['Title', None, None], ['Date', 'CAPE', 'TR CAPE'],
        [1996.12, 20, 21], [2025.10, 30, 40], [2026.09, 35, 45],
        [2026.10, 36, 46], [2026.08, float('nan'), 40],
        ['Footnote', None, None],
    ])
    assert cape_history(frame, date(2026, 9, 27)) == [
        {'date': '2025-10-01', 'value': 30.0}, {'date': '2026-09-01', 'value': 35.0},
    ]
    with pytest.raises(ValueError):
        cape_history(pd.DataFrame([['Date', 'TR CAPE']]), date(2026, 9, 27))


def test_download_link_discovery():
    links = DownloadLinks()
    links.feed('<a href="//img1.wsimg.com/files/ie_data.xls?v=2&amp;x=1">Download</a>')
    assert links.url == 'https://img1.wsimg.com/files/ie_data.xls?v=2&x=1'


def test_cache_failure_and_observation_staleness_are_independent(tmp_path):
    path = tmp_path / 'cache.json'
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    calls = []

    def fetch(key, session, today):
        calls.append(key)
        return [{'date': '2026-06-30' if key == 'buffett' else '2025-01-01', 'value': 30.0}]

    first = build_market_valuation(path, now=now, fetcher=fetch)
    assert first['indicators']['buffett']['status'] == 'available'
    assert first['indicators']['cape']['status'] == 'stale'
    build_market_valuation(path, now=now, fetcher=fetch)
    assert calls == ['buffett', 'cape']

    def fail(*args):
        raise ValueError('provider unavailable')

    later = build_market_valuation(path, now=datetime(2026, 9, 28, tzinfo=timezone.utc), fetcher=fail)
    assert later['indicators']['buffett']['status'] == 'cached'
    assert later['indicators']['buffett']['retrieved_at'] == now.isoformat()
    assert later['indicators']['cape']['status'] == 'stale'
    assert later['score_effect'] == 0 and later['verdict_effect'] == 'none'
    empty = build_market_valuation(tmp_path / 'empty.json', now=now, fetcher=fail)
    assert all(v['value'] is None and v['status'] == 'unavailable' for v in empty['indicators'].values())


def test_sources_fail_independently(tmp_path):
    def fetch(key, session, today):
        if key == 'buffett':
            raise TimeoutError()
        return [{'date': '2026-09-01', 'value': 40}]
    result = build_market_valuation(tmp_path / 'cache.json',
                                    now=datetime(2026, 9, 27, tzinfo=timezone.utc), fetcher=fetch)
    assert result['indicators']['buffett']['status'] == 'unavailable'
    assert result['indicators']['cape']['value'] == 40
