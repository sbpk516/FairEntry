from fairentry.config import load_config
from fairentry.criteria import generate
from fairentry.analytics.entry_alignment import compute_entry_alignment_from_history
from fairentry.alerts import moving_average_zone_candidates


def test_criteria_reflects_active_configuration():
    cfg = load_config()
    page = generate(cfg)
    assert 'not a Buy gate' in page
    assert '50-week SMA' in page
    assert 'Recent ROIC direction must pass' in page
    cfg.scoring['buy_entry_alignment']['category_minimum'] = 81
    cfg.defaults['moving_average_zone_threshold_pct'] = 7
    page = generate(cfg)
    assert 'each at least 81 out of 100' in page
    assert '±7%' in page


def test_weekly_50_average_requires_history_and_ignores_future():
    import pandas as pd
    frame = pd.DataFrame({'Close': range(1, 61), 'Volume': 100},
                         index=pd.date_range('2020-01-03', periods=60, freq='W-FRI'))
    assert 'sma_50week' not in compute_entry_alignment_from_history(frame.iloc[:49])
    result = compute_entry_alignment_from_history(frame, asof=frame.index[49])
    assert result['sma_50week'] == 25.5


def test_optional_filter_supports_ema_and_50week_without_changing_verdict():
    stock = {'ticker': 'TEST', 'verdict': 'Watch', 'price': 104,
             'categories': [{'id': k, 'score': 80} for k in ('quality', 'survival', 'growth')]}
    for key in ('ema_9month', 'ema_20month', 'sma_50week'):
        rows = moving_average_zone_candidates([stock], {'TEST': {key: {'value': 100}}})
        assert rows[0]['nearest_zone']['id'] == key
        assert stock['verdict'] == rows[0]['verdict'] == 'Watch'
        assert moving_average_zone_candidates([stock], {'TEST': {key: {'value': 90}}}) == []
