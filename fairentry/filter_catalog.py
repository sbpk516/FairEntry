"""Configuration-backed exploration controls; never changes official decisions."""
import math

from .alerts import _MOVING_AVERAGE_ZONES
from .screeners.quality_growth import CRITERIA as Q
from .screeners.deep_value import CRITERIA as D


def catalog(cfg):
    fields = []
    def number(key, label, group, default=None, unit='', help=''):
        fields.append(dict(id=key, label=label, group=group, type='number',
                           default=default, unit=unit, help=help))
    def choice(key, label, group, options, help=''):
        fields.append(dict(id=key, label=label, group=group, type='choice',
                           options=options, help=help))
    u = cfg.sectors['universe_filter']
    for key, label, default in [('market_cap', 'Market cap', u['market_cap_min_usd']),
                                ('price', 'Share price', u['price_min_usd']),
                                ('avg_dollar_volume', 'Average daily dollar volume', u['avg_dollar_volume_min'])]:
        number(key, label, 'Universe', default if key == 'price' else default/1_000_000,
               'USD' if key == 'price' else 'USD millions',
               '' if key == 'price' else 'Enter millions: 300 means $300 million; 1000 means $1 billion.')
    choice('screen', 'Screening route', 'Candidate screens',
           [['either', 'Either screen'], ['quality_growth', 'Quality Growth'], ['deep_value', 'Deep Value']],
           'Quality Growth requires revenue AND margin. Deep Value requires any cheap ratio AND price performance AND debt/equity. Missing margin or debt/equity is allowed, as in production.')
    thresholds = [('rev_growth_qoq', 'Revenue growth minimum', Q['revenue_growth_min'], '%'),
                  ('gross_margin', 'Gross margin minimum', Q['gross_margin_min'], '%'),
                  ('pb_ratio', 'P/B maximum', D['pb_max'], '×'),
                  ('ps_ratio', 'P/S maximum', D['ps_max'], '×'),
                  ('pfcf_ratio', 'Positive P/FCF maximum', D['pfcf_max'], '×'),
                  ('perf_year', 'One-year price change maximum', D['performance_max'], '%'),
                  ('debt_eq', 'Debt/equity maximum', D['debt_equity_max'], '×')]
    for key, label, value, unit in thresholds:
        fields.append(dict(id='screen_'+key, metric=key, label=label, group='Candidate screens',
                           type='threshold', default=value, unit=unit,
                           help='Used only when a screening route is selected. Blank restores the production default.'))
    minimum = cfg.scoring['buy_entry_alignment']['category_minimum']
    for key, c in cfg.categories.items():
        number('category_'+key, c['label']+' score', 'Business scores',
               minimum if key in ('quality', 'survival', 'growth') else None, '/100',
               'Official category score. Filtering does not rescore it.')
    number('price_to_fair', 'Price / central fair value', 'Valuation', 1, '×', 'At most 1 means price is at or below central fair value.')
    number('method_count', 'Usable valuation methods', 'Valuation', cfg.scoring['buy_entry_alignment']['fair_value_method_minimum'])
    choice('roic', 'Recent ROIC direction', 'ROIC & technical',
           [['pass','Stable / recovering'], ['declining','Deteriorating'], ['missing','Missing / insufficient'], ['stale','Stale evidence']])
    choice('obv', 'Weekly OBV confirmation', 'ROIC & technical', [['yes','Confirmed'], ['no','Not confirmed'], ['missing','Missing']],
           'Weekly OBV above its 20-week EMA. Not proof of institutional buying.')
    for key, label in _MOVING_AVERAGE_ZONES:
        number('distance_'+key, 'Distance from '+label, 'ROIC & technical',
               cfg.defaults.get('moving_average_zone_threshold_pct', 5), '%',
               'Absolute distance, above or below the average. Optional, never a Buy gate. Multiple enabled averages must all match.')
    choice('veto', 'Any hard veto', 'Risk & evidence', [['no','No hard veto'], ['yes','Hard veto active']])
    for v in cfg.scoring.get('vetoes', []):
        if v.get('decision_status', 'tested') == 'tested':
            choice('veto_'+v['id'], v['reason'], 'Risk & evidence', [['no','Not active'], ['yes','Active']], v['when'])
    number('coverage', 'Data coverage', 'Risk & evidence', None, '%')
    choice('stale_inputs', 'Stale technical inputs removed', 'Risk & evidence', [['no','None removed'], ['yes','Some removed']],
           'Stale indicators are removed before scoring; missing values do not count as confirmed.')
    choice('verdict', 'Official recommendation', 'Results', [[s,s] for s in ('Buy','Watch','Avoid')],
           'The Current Buy rules preset includes Buy only. Remove this filter to explore Watch/Avoid stocks under relaxed criteria. Uses the published rating, not locally tuned ratings.')
    choice('strategy', 'Published screening membership', 'Results', [['growth','Quality Growth'],['deepvalue','Deep Value']])
    for key, c in cfg.categories.items():
        for item in c['items']:
            group = 'Individual factors' if item.get('decision_status','tested') == 'tested' else 'Information only'
            number('factor_'+item['id'], item['label']+' score', group, None, '/100',
                   (item.get('definition') or item.get('expected','')) + (' Information only: does not affect recommendations.' if group == 'Information only' else ''))
    universe = {k: {'op':'min','value':u[v] if k == 'price' else u[v]/1_000_000} for k,v in [('market_cap','market_cap_min_usd'), ('price','price_min_usd'), ('avg_dollar_volume','avg_dollar_volume_min')]}
    strong = {'category_'+k: {'op':'min','value':minimum} for k in ('quality','survival','growth')}
    buy = dict(universe, **strong, screen='either', price_to_fair={'op':'max','value':1},
               method_count={'op':'min','value':cfg.scoring['buy_entry_alignment']['fair_value_method_minimum']},
               obv='yes', veto='no', verdict='Buy')
    if cfg.scoring.get('roic_direction_gate'):
        buy['roic'] = 'pass'
    return {'fields':fields, 'presets':[
        {'id':'all','label':'All published candidates','values':{}},
        {'id':'buy','label':'Current Buy rules','values':buy},
        {'id':'growth','label':'Quality Growth screen','values':dict(universe, screen='quality_growth')},
        {'id':'value','label':'Deep Value screen','values':dict(universe, screen='deep_value')},
        {'id':'strong','label':'Strong fundamentals','values':strong}],
        'scope':'Filters search published, screened candidates only. Lowering a universe floor or relaxing a screen cannot recover excluded stocks. Sector, strategy, search and other toolbar filters also apply. Official recommendations are unchanged.'}


def values(cfg, rec, metrics, stale=()):
    def raw(key):
        value = metrics.get(key)
        return value.get('value') if isinstance(value,dict) else value
    def numeric(value):
        return value if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) else None
    result = {key:numeric(raw(key)) for key in ('market_cap','avg_dollar_volume','rev_growth_qoq','gross_margin','pb_ratio','ps_ratio','pfcf_ratio','perf_year','debt_eq')}
    result['price'] = numeric(rec.get('price'))
    for key in ('market_cap', 'avg_dollar_volume'):
        if result[key] is not None:
            result[key] /= 1_000_000
    alignment = rec.get('buy_entry_alignment') or {}
    val = alignment.get('valuation') or {}
    base, price = numeric(val.get('fair_base')), result['price']
    result['price_to_fair'] = price/base if price is not None and base and base>0 else None
    result['method_count'] = numeric(val.get('method_count'))
    obv = (alignment.get('weekly_obv') or {}).get('value')
    result['obv'] = 'yes' if obv is True else 'no' if obv is False else 'missing'
    roic = (rec.get('decision_trace') or {}).get('roic_direction') or {}
    reason = roic.get('reason','').lower()
    result['roic'] = 'pass' if roic.get('passes') is True else 'stale' if 'stale' in reason else 'declining' if 'deteriorat' in reason else 'missing'
    result.update(verdict=rec.get('verdict'), strategy=rec.get('_strategies', []),
                  veto='yes' if rec.get('vetoes') else 'no', coverage=numeric(rec.get('coverage_pct')),
                  stale_inputs='yes' if stale else 'no')
    active = {v['id'] for v in rec.get('vetoes',[])}
    for v in cfg.scoring.get('vetoes',[]):
        result['veto_'+v['id']] = 'yes' if v['id'] in active else 'no'
    for key, _ in _MOVING_AVERAGE_ZONES:
        average = numeric(raw(key))
        result['distance_'+key] = round(abs(price/average-1)*100, 8) if price is not None and average and average>0 else None
    for c in rec.get('categories',[]):
        result['category_'+c['id']] = numeric(c.get('score'))
        for item in c['items']:
            result['factor_'+item['id']] = numeric(item.get('score'))
    return result
