from fairentry.config import load_config
from fairentry.filter_catalog import catalog, values


def test_catalog_covers_all_factors_and_uses_active_defaults():
    cfg = load_config()
    cfg.scoring['buy_entry_alignment']['category_minimum'] = 79
    data = catalog(cfg)
    fields = {f['id']: f for f in data['fields']}
    assert fields['category_quality']['default'] == 79
    assert not any(k.startswith('distance_ema_') for k in fields)
    for c in cfg.categories.values():
        for item in c['items']:
            assert 'factor_'+item['id'] in fields
    buy = next(p for p in data['presets'] if p['id']=='buy')['values']
    assert buy['verdict'] == 'Buy'
    assert not any(k.startswith('distance_') for k in buy)
    assert buy['category_growth']['value'] == 79


def test_export_does_not_turn_missing_metrics_into_zero_or_pass():
    cfg = load_config()
    rec = {'price':100, 'verdict':'Watch','vetoes':[], 'categories':[]}
    out = values(cfg, rec, {})
    assert out['market_cap'] is None
    for signal, expected in [(True, 'yes'), (1.0, 'yes'), (False, 'no'), (0.0, 'no'), (None, 'missing')]:
        signal_rec = dict(rec, buy_entry_alignment={'weekly_obv': {'value': signal}})
        assert values(cfg, signal_rec, {})['obv'] == expected
    boundary = values(cfg, dict(rec, price=105), {'sma_50week': {'value':100}})
    assert boundary['distance_sma_50week'] == 5
    scaled = values(cfg, rec, {'market_cap': {'value': 300_000_000}, 'avg_dollar_volume': {'value': 10_000_000}})
    assert scaled['market_cap'] == 300
    assert scaled['avg_dollar_volume'] == 10
    assert out['obv'] == out['roic'] == 'missing'
    assert out['price_to_fair'] is None
    assert out['distance_sma_50week'] is None
    out = values(cfg, rec, {'sma_50week':{'value':80}, 'market_cap':{'value':float('nan')}})
    assert out['distance_sma_50week'] == 25
    assert out['market_cap'] is None
