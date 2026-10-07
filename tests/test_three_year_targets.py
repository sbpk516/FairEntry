import numpy as np

from scripts.research_three_year_targets import path_outcome, roots, passes


def row():
    return {'entry_date': '2020-01-01', 'adjusted_entry': 100,
            'terminal': None, 'issuer': 'A', 'ticker': 'A', 'vetoes': [],
            'scores': {'quality': 90, 'survival': 90, 'growth': 90, 'confirmation': 80},
            'factors': {}, 'value_pass': True, 'roic_pass': True,
            'horizons': {'365': {'return_pct': 30}, '730': {'return_pct': 69},
                         '1095': {'return_pct': 119.7, 'alpha_pct': 80}}}


def test_touching_double_does_not_guarantee_next_close_capture():
    r = row()
    dates = np.array(['2020-01-01', '2020-02-01', '2020-02-02', '2022-12-31'], dtype='datetime64[D]')
    result = path_outcome(r, dates, np.array([100., 201., 180., 220.]))
    assert result['hit100_3']
    assert result['exit100'] < 100


def test_uneven_doubling_is_not_annual_30_percent():
    r = row()
    r['horizons']['365']['return_pct'] = 10
    dates = np.array(['2020-01-01', '2022-12-31'], dtype='datetime64[D]')
    result = path_outcome(r, dates, np.array([100., 220.]))
    assert result['hit100_3']
    assert not result['annual30']


def test_exact_30_percent_compounding_passes_all_three_years():
    dates = np.array(['2020-01-01', '2022-12-31'], dtype='datetime64[D]')
    result = path_outcome(row(), dates, np.array([100., 220.]))
    assert result['annual30']


def test_stop_executes_after_breach_and_can_lose_more_than_30_percent():
    dates = np.array(['2020-01-01', '2020-02-01', '2020-02-02', '2022-12-31'], dtype='datetime64[D]')
    result = path_outcome(row(), dates, np.array([100., 69., 50., 220.]))
    assert result['stop30_exit100'] < -50
    assert result['hit100_3']


def test_no_overlapping_same_issuer_and_veto_survives_strategy_change():
    r = row()
    later = {**r, 'entry_date': '2020-02-01'}
    assert len(roots([r, later], 'quality_growth')) == 1
    r['vetoes'] = [{'id': 'distress'}]
    assert not passes(r, 'quality_growth')
