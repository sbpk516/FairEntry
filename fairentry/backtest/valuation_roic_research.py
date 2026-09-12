"""Research-only entry filters. Never imported by production scoring."""
from __future__ import annotations

import math
import statistics


def number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def valuation_check(targets):
    """Use explicit applicability metadata; do not count blended derivatives twice."""
    methods, rejected = {}, {}
    for key in ('fcf', 'peer_ps', 'book'):
        item = targets.get(key, {})
        value = number(item.get('price'))
        if not item.get('available') or not item.get('applicable') or item.get('excluded'):
            rejected[key] = 'Missing or not applicable'
        elif item.get('relevance') == 'low':
            rejected[key] = 'Saved relevance is low'
        elif value is None or value <= 0:
            rejected[key] = 'Non-positive or invalid estimate'
        else:
            methods[key] = value
    return {'methods': methods, 'rejected': rejected, 'method_count': len(methods),
            'ratio': max(methods.values()) / min(methods.values()) if len(methods) >= 2 else None}


def roic_check(annual, latest, capital_cost=10.0):
    # Caller supplies distinct fiscal years, latest revision available at the signal.
    annual = annual[-5:]
    values = [number(row['roic_pct']) for row in annual]
    valid = [v for v in values if v is not None]
    median = statistics.median(valid) if valid else None
    latest = number(latest)
    recent = values[-3:]
    enough = len(recent) == 3 and all(v is not None for v in recent) and latest is not None
    changes = [b - a for a, b in zip(recent, recent[1:]) if a is not None and b is not None]
    if recent and recent[-1] is not None and latest is not None:
        changes.append(latest - recent[-1])
    declining = enough and (any(change < -2.0 - 1e-9 for change in changes)
                            or latest < recent[0] - 2.0 - 1e-9)
    status = 'insufficient_evidence' if not enough else 'deteriorating' if declining else 'stable_or_recovering'
    large_swings = enough and any(abs(change) > 15.0 + 1e-9 for change in changes)
    spread = latest - capital_cost if latest is not None else None
    level = ('unknown' if latest is None else 'negative' if latest < 0 else
             'below_assumed_cost' if spread < 0 else 'small_cushion' if spread < 3 else 'healthy_proxy')
    reasons = []
    if not enough:
        reasons.append('Insufficient recent annual ROIC history')
    if declining:
        reasons.append('Recent ROIC is deteriorating')
    if large_swings:
        reasons.append('Recent ROIC changes exceed 15 percentage points')
    if level != 'healthy_proxy':
        reasons.append('ROIC lacks a 3-point cushion above assumed capital cost')
    positive_fraction = sum(v > 0 for v in valid) / len(valid) if valid else None
    return {'annual_history': annual, 'valid_years': len(valid), 'median_pct': median,
            'positive_fraction': positive_fraction, 'latest_pct': latest,
            'median_decline_pp': median - latest if median is not None and latest is not None else None,
            'changes_pp': changes, 'trend_status': status,
            'trend_pass': enough and not declining,
            'capital_cost_assumption_pct': capital_cost, 'spread_proxy_pp': spread,
            'level': level, 'level_pass': level == 'healthy_proxy',
            'consistency': 'unknown' if not enough else 'large_swings' if large_swings else 'moderate_changes',
            'consistency_pass': enough and not large_swings,
            'assessment': 'Watch' if reasons else 'Pass', 'watch_reasons': reasons,
            'economic_health_verified': False}


def variants(row):
    v, r = row['valuation'], row['roic']
    result = {'baseline': True, 'two_methods': v['method_count'] >= 2,
              'roic_direction': r['trend_pass'], 'roic_level_proxy': r['level_pass'],
              'roic_consistency': r['consistency_pass'], 'roic_three_part': r['assessment'] == 'Pass'}
    for threshold in (2, 3, 5):
        result[f'valuation_{threshold}x'] = v['ratio'] is not None and v['ratio'] <= threshold
        result[f'combined_{threshold}x'] = result[f'valuation_{threshold}x'] and r['assessment'] == 'Pass'
    return result


def summarize(rows):
    output = {}
    for name in variants({'valuation': {'method_count': 0, 'ratio': None},
                          'roic': {'trend_pass': False, 'level_pass': False, 'consistency_pass': False, 'assessment': 'Watch'}}):
        kept = [r for r in rows if r['passes'][name]]
        rejected = [r for r in rows if not r['passes'][name]]
        eligible = [r for r in kept if r['outcome'] in ('success', 'failure')]
        returns = [r['return_365_pct'] for r in kept if r['return_365_pct'] is not None]
        output[name] = {'kept': len(kept), 'rejected': len(rejected), 'evaluated': len(eligible),
                        'success_rate_pct': 100 * sum(r['outcome'] == 'success' for r in eligible) / len(eligible) if eligible else None,
                        'rejected_target_failures': sum(r['outcome'] == 'failure' for r in rejected),
                        'rejected_target_successes': sum(r['outcome'] == 'success' for r in rejected),
                        'rejected_negative_one_year_returns': sum(r['return_365_pct'] is not None and r['return_365_pct'] < 0 for r in rejected),
                        'mean_entry_return_365_pct': statistics.mean(returns) if returns else None,
                        'return_sample_count': len(returns)}
    return output
