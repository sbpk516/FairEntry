"""Evaluate fixed candidate filters on matching saved Buy episodes; no production edits."""
import argparse
import json
import sys
from pathlib import Path

import duckdb
import ijson

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fairentry.backtest.valuation_roic_research import valuation_check, roic_check, variants, summarize
from fairentry.backtest.evidence import _fixed_horizon_evaluation
from fairentry.backtest.vectorbt_crosscheck import artifact_metadata


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--public', default='web/data/backtest-sfa.json')
    p.add_argument('--private', default='data/sharadar/reports/backtest-sfa-full.json')
    p.add_argument('--warehouse', default='data/sharadar/warehouse.duckdb')
    p.add_argument('--out', default='data/sharadar/reports/valuation-roic-research.json')
    args = p.parse_args()
    public = json.loads(Path(args.public).read_text(encoding='utf-8'))
    identity = artifact_metadata(args.private)
    for field in ('run_id', 'snapshot_id', 'implementation_fingerprint'):
        if not identity.get(field) or identity[field] != public.get(field):
            raise ValueError('Artifact mismatch: ' + field)
    wanted = {(e['ticker'], e['started']) for e in public['buy_return_achievement']['episode_details']}
    rows, found = [], set()
    with duckdb.connect(args.warehouse, read_only=True) as con, open(args.private, 'rb') as stream:
        for root in ijson.items(stream, 'observations.item', use_float=True):
            key = (root.get('ticker'), root.get('entry_date'))
            if key not in wanted:
                continue
            if key in found:
                raise ValueError('Duplicate episode root')
            found.add(key)
            annual = con.execute('''SELECT reportperiod,datekey,roic FROM sfa_fundamentals
                WHERE ticker=? AND dimension='ARY' AND datekey<=CAST(? AS DATE)
                AND reportperiod<=CAST(? AS DATE)
                AND reportperiod>=CAST(? AS DATE)-INTERVAL 6 YEAR
                QUALIFY row_number() OVER(PARTITION BY year(reportperiod) ORDER BY reportperiod DESC,datekey DESC)=1
                ORDER BY reportperiod''', [key[0], root['decision_date'], root['decision_date'], root['decision_date']]).fetchall()
            history = [{'period': str(d), 'available_date': str(a), 'roic_pct': v * 100 if v is not None else None} for d,a,v in annual]
            row = {'ticker': key[0], 'entry_date': key[1], 'decision_date': root['decision_date'], 'sector': root.get('sector'),
                   'valuation': valuation_check(root.get('targets') or {}),
                   'roic': roic_check(history, (root.get('research_factors') or {}).get('roic_pct')),
                   'outcome': _fixed_horizon_evaluation(root, 30, 365)['result'],
                   'return_365_pct': (root.get('horizons', {}).get('365') or {}).get('return_pct')}
            row['passes'] = variants(row)
            rows.append(row)
    if found != wanted:
        raise ValueError('Missing episode roots')
    report = {'production_effect': 'none', 'roic_rule': 'Three-part v3: latest ROIC >=13% using assumed 10% capital cost plus 3pp cushion; latest three annual values plus latest TTM; decline >2pp in any recent step or net decline >2pp is Watch; absolute step >15pp is Watch; missing recent history is Watch. Economic health is an unverified proxy.', 'identity': identity,
              'limitations': ['Retrospective diagnostics; previously inspected cases mean this is not pristine holdout validation.',
                  'Equal-entry mean returns are not portfolio returns; no replacement trades or cash return assumed.',
                  'Saved method relevance is a proxy for suitability, not an independent valuation audit.',
                  'Annual provider ROIC is not independently reconstructed; capital denominator not audited.',
                  '10% cost of capital is hypothetical; provider ROIC has not been reconciled to an after-tax WACC-compatible definition.',
                  'Latest five annual observations with a six-year freshness bound; at least three valid years required.'],
              'all': summarize(rows),
              'periods': {label: summarize([r for r in rows if lo <= r['entry_date'] < hi]) for label,lo,hi in
                          [('before_2015','1900','2015'), ('2015_2019','2015','2020'), ('2020_onward','2020','2100')]},
              'sectors': {s: summarize([r for r in rows if r['sector'] == s]) for s in sorted({r['sector'] for r in rows if r['sector']})},
              'episodes': rows}
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(report['all'], indent=2))
    print('Saved', path)


if __name__ == '__main__':
    main()
