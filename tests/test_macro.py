from datetime import date, datetime, timezone

import pytest

from fairentry.adapters.macro import build_macro, describe, parse_series, transform


def test_signed_values_missing_observations_and_future_dates():
    text = 'observation_date,X\n2026-09-01,-0.4\n2026-09-02,0\n2026-09-03,.\n2026-09-04,NaN\n2027-01-01,4\n'
    assert parse_series(text, 'X', date(2026, 9, 28)) == [
        {'date': '2026-09-01', 'value': -0.4}, {'date': '2026-09-02', 'value': 0}]
    with pytest.raises(ValueError):
        parse_series('<html>Error</html>', 'X', date(2026, 9, 28))


def test_inflation_uses_same_month_not_row_offset():
    history = [{'date': '2025-07-01', 'value': 100},
               {'date': '2026-06-01', 'value': 102},
               {'date': '2026-07-01', 'value': 103}]
    result = transform(history, 'yoy')
    assert len(result) == 1 and result[0]['date'] == '2026-07-01'
    assert result[0]['value'] == pytest.approx(3)


def test_readings_distinguish_levels_from_direction():
    assert describe('curve', -0.1, 0.2) == 'Inverted'
    assert describe('curve', 0, 0.2) == 'Flat curve'
    assert describe('conditions', -0.5, 0.1) == 'Looser than average'
    assert describe('growth', -1, 2) == 'Output contracting'
    assert describe('inflation', 3, -1) == 'Above 2% target'
    assert describe('change', 4, None) == 'Comparison unavailable'


def test_cache_failures_stale_and_curve_episode(tmp_path):
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    calls = []

    def fetch(series, today):
        calls.append(series)
        if series == 'UNRATE':
            return [{'date': '2020-01-01', 'value': 4}]
        if series in ('PCEPI', 'PCEPILFE'):
            return [{'date': '2025-07-01', 'value': 100}, {'date': '2026-07-01', 'value': 103}]
        return [{'date': '2026-09-24', 'value': 0.1},
                {'date': '2026-09-25', 'value': -0.1}, {'date': '2026-09-28', 'value': -0.2}]

    cache = tmp_path / 'macro.json'
    first = build_macro(cache, now=now, fetcher=fetch)
    rows = {i['id']: i for i in first['indicators']}
    assert rows['curve2']['inverted_since'] == '2026-09-25'
    assert rows['curve2']['change'] == -0.1
    assert rows['unemployment']['status'] == 'stale'
    assert first['score_effect'] == 0 and first['verdict_effect'] == 'none'
    build_macro(cache, now=now, fetcher=fetch)
    assert len(calls) == 9

    def fail(*args):
        raise TimeoutError()

    later = build_macro(cache, now=datetime(2026, 9, 29, tzinfo=timezone.utc), fetcher=fail)
    assert later['indicators'][0]['status'] == 'cached'
    assert later['indicators'][0]['retrieved_at'] == now.isoformat()
    empty = build_macro(tmp_path / 'empty.json', now=now, fetcher=fail)
    assert all(i['status'] == 'unavailable' and i['value'] is None for i in empty['indicators'])


def test_one_source_failure_does_not_hide_others(tmp_path):
    def fetch(series, today):
        if series == 'UNRATE':
            raise ValueError()
        return [{'date': '2026-09-25', 'value': 0}]
    result = build_macro(tmp_path / 'cache.json', now=datetime(2026, 9, 28, tzinfo=timezone.utc), fetcher=fetch)
    rows = {i['id']: i for i in result['indicators']}
    assert rows['unemployment']['status'] == 'unavailable'
    assert rows['curve3']['value'] == 0
    assert rows['curve3']['reading'] == 'Flat curve'


def test_long_history_is_exported_and_old_same_day_cache_is_upgraded(tmp_path):
    import json
    cache = tmp_path / 'macro.json'
    cache.write_text(json.dumps({'claims': {'retrieved_at': '2026-09-28T00:00:00Z',
                                          'history': [{'date': '2026-09-25', 'value': 200000}]}}))
    calls = []
    def fetch(series, today):
        calls.append(series)
        return [{'date': f'{year}-01-01', 'value': 200000} for year in range(1989, 2027)]
    result = build_macro(cache, now=datetime(2026, 9, 28, tzinfo=timezone.utc), fetcher=fetch)
    claims = next(i for i in result['indicators'] if i['id'] == 'claims')
    assert 'IC4WSA' in calls
    assert claims['history'][0]['date'] == '1990-01-01'
    assert len(claims['history']) == 37
    assert json.loads(cache.read_text())['claims']['history_version'] == 2
