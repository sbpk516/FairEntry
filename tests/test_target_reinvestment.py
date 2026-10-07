import numpy as np

from scripts.research_target_reinvestment import simulate


def test_target_exit_waits_for_following_close_and_cash_is_not_reinvested_without_signal():
    dates = np.array(['2020-01-01', '2020-01-02', '2020-01-03', '2020-12-31',
                      '2021-12-31', '2022-12-31'], dtype='datetime64[D]')
    prices = {'A': np.array([100., 201., 180., 300., 300., 300.])}
    signals = {dates[0]: [{'ticker': 'A', 'issuer': 'A', 'id': 'A'}]}
    result = simulate(signals, 0, dates, prices, {}, 1.)
    # 10% in one name, 90% cash. A doubles briefly but exits at 180 next day.
    assert 7 < result['return_pct'] < 8
    assert result['trades'] == 1
    assert result['exits'] == 1
    assert not result['reached_double']


def test_bankruptcy_zero_is_not_carried_at_last_price():
    dates = np.array(['2020-01-01', '2020-01-02', '2020-12-31',
                      '2021-12-31', '2022-12-31'], dtype='datetime64[D]')
    prices = {'A': np.array([100., np.nan, np.nan, np.nan, np.nan])}
    signals = {dates[0]: [{'ticker': 'A', 'issuer': 'A', 'id': 'A'}]}
    result = simulate(signals, 0, dates, prices, {'A': (dates[1], 'zero')}, 1.)
    assert result['return_pct'] == -10
    assert result['exits'] == 1
