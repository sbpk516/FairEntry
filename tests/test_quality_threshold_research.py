from scripts.analyze_quality_threshold import qualifies, summarize


def observation(quality=50, **outcome):
    return {
        'issuer_key': 'test', 'scores': {'quality': quality, 'survival': 70, 'growth': 70},
        'price': 100, 'valuation': {'fair_base': 120, 'method_count': 1},
        'vetoes': [], 'roic_direction': {'passes': True},
        '_tuning_outcome': {'last_observed_days': 365,
                            'first_hit_days_by_target': {'30': 100}, **outcome},
    }


def test_research_changes_only_quality_and_keeps_other_gates():
    row = observation()
    assert qualifies(row, 50)
    assert not qualifies(row, 70)
    row['roic_direction']['passes'] = False
    assert not qualifies(row, 50)
    row['roic_direction']['passes'] = True
    row['vetoes'] = [{'id': 'distress'}]
    assert not qualifies(row, 50)


def test_primary_rate_excludes_immature_winners_and_counts_terminal_failures():
    winner = observation()
    early_winner = observation(last_observed_days=150)
    terminal_failure = observation(last_observed_days=50, terminal_days=50,
                                   first_hit_days_by_target={'30': None})
    result = summarize([winner, early_winner, terminal_failure])
    assert result['mature_episodes'] == 2
    assert result['immature_excluded'] == 1
    assert result['hit_30_pct'] == 50
