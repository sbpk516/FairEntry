from fairentry.backtest.valuation_roic_research import valuation_check, roic_check, variants, summarize


def target(price, **kwargs):
    return dict(price=price, available=True, applicable=True, **kwargs)


def test_valuation_filters_and_no_double_count():
    result = valuation_check({'fcf': target(2), 'peer_ps': target(150),
                              'book': target(80, relevance='low'), 'blended': target(75)})
    assert result['ratio'] == 75
    assert result['method_count'] == 2
    assert 'book' in result['rejected']


def test_invalid_estimates_fail_closed():
    result = valuation_check({'fcf': target(float('nan')), 'peer_ps': target(0), 'book': target(5)})
    assert result['ratio'] is None
    assert result['method_count'] == 1


def test_roic_requires_history_and_detects_decline():
    assert not roic_check([{'roic_pct': 100}], 100)['trend_pass']
    result = roic_check([{'roic_pct': x} for x in (35, 40, 45)], 20)
    assert not result['trend_pass']
    assert result['trend_status'] == 'deteriorating'


def test_user_examples_and_stability():
    for values, expected in [([40, 35, 30, 20, 15], False),
                             ([15, 18, 20, 22, 25], True),
                             ([-20, -10, 5, 60, 200], True),
                             ([15, 15, 15], True),
                             ([10, 20, 19, 30, 40], True)]:
        result = roic_check([{'roic_pct': x} for x in values], values[-1])
        assert result['trend_pass'] is expected


def test_missing_history_is_not_silently_bridged():
    result = roic_check([{'roic_pct': x} for x in [1, None, 3, 4]], 4)
    assert not result['trend_pass']
    assert result['trend_status'] == 'insufficient_evidence'


def test_threshold_boundary_and_missing_outcomes():
    row = {'valuation': {'method_count': 2, 'ratio': 3},
           'roic': {'trend_pass': True, 'level_pass': True, 'consistency_pass': True, 'assessment': 'Pass'},
           'outcome': 'active', 'return_365_pct': None}
    row['passes'] = variants(row)
    assert row['passes']['valuation_3x']
    assert not row['passes']['valuation_2x']
    report = summarize([row])
    assert report['baseline']['success_rate_pct'] is None
    assert report['valuation_2x']['rejected_target_failures'] == 0


def test_three_part_user_patterns():
    for values, expected in [([36.2,39.7,40,44.4,51.3], 'Pass'),
                             ([20,18,15,15,18,18], 'Pass'),
                             ([-20,-10,5,60,200], 'Watch'),
                             ([40,35,30,20,15], 'Watch')]:
        r = roic_check([{'roic_pct': v} for v in values], values[-1])
        assert r['assessment'] == expected
        assert not r['economic_health_verified']


def test_level_examples():
    for value, level in [(18,'healthy_proxy'),(11,'small_cushion'),(7,'below_assumed_cost'),(-5,'negative')]:
        r = roic_check([{'roic_pct': value}] * 3, value)
        assert r['level'] == level


def test_gradual_net_decline_and_recent_stability():
    assert roic_check([{'roic_pct': x} for x in [20,18.5,17]],17)['assessment'] == 'Watch'
    assert roic_check([{'roic_pct': x} for x in [None,15,18,18]],18)['assessment'] == 'Pass'
