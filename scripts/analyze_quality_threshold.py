"""Compare Quality 70 vs 50 on the full private point-in-time replay.

Reuses frozen scores and cost-adjusted outcomes, not historical verdicts.
Reapplies current ROIC direction using filings known at each decision date.
"""
import argparse
from collections import defaultdict
from datetime import date, datetime, timezone
import hashlib
from html import escape
import json
from pathlib import Path
import random
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ijson
import pandas as pd
from fairentry.analytics.roic_direction import assess, historical
from fairentry.backtest.sfa_tune import _episode_roots, _is_complete
from fairentry.config import load_config
from fairentry.scoring.engine import buy_entry_alignment
from fairentry.sharadar import SharadarWarehouse


def qualifies(row, quality_minimum):
    policy = {'buy_entry_alignment': {
        'category_minimum': 70, 'category_minimums': {'quality': quality_minimum},
        'monthly_ema_required': False, 'weekly_obv_required': False}}
    return (not row['vetoes'] and row['roic_direction']['passes'] and
            buy_entry_alignment(policy, row['scores'], {'price': row['price']},
                                row['valuation'])['passes'])


def summarize(rows):
    mature = [r for r in rows if _is_complete(r['_tuning_outcome'], 365)]
    def hit(r, target):
        days = r['_tuning_outcome'].get('first_hit_days_by_target', {}).get(str(target))
        return days is not None and days <= 365
    result = {'episodes': len(rows), 'mature_episodes': len(mature),
              'immature_excluded': len(rows)-len(mature),
              'unique_issuers': len({r['issuer_key'] for r in mature})}
    for target in [20,25,30,35]:
        result[f'hit_{target}_pct'] = round(100*sum(hit(r,target) for r in mature)/len(mature),2) if mature else None
    for key, values in {
        'one_year_return_pct': [r['_tuning_outcome'].get('return_pct') for r in mature],
        'one_year_alpha_pct': [r['_tuning_outcome'].get('alpha_pct') for r in mature],
        'worst_loss_from_entry_pct': [r['_tuning_outcome'].get('max_drawdown_pct') for r in mature],
    }.items():
        values = [v for v in values if isinstance(v,(int,float))]
        result[key] = {'n':len(values), 'mean':round(statistics.mean(values),2) if values else None,
                       'median':round(statistics.median(values),2) if values else None,
                       'worst':round(min(values),2) if values else None}
        if values:
            result[key]['loss_20_or_worse_pct'] = round(100*sum(v<=-20 for v in values)/len(values),2)
    result['horizons'] = {}
    for horizon in [30,90,180,365,730,1095,1825]:
        values = [r.get('horizons',{}).get(str(horizon),{}) for r in rows
                  if _is_complete(r['_tuning_outcome'], horizon)]
        returns = [r['return_pct'] for r in values if isinstance(r.get('return_pct'),(int,float))]
        alphas = [r['alpha_pct'] for r in values if isinstance(r.get('alpha_pct'),(int,float))]
        result['horizons'][str(horizon)] = {
            'n':len(returns), 'mean_return_pct':round(statistics.mean(returns),2) if returns else None,
            'median_return_pct':round(statistics.median(returns),2) if returns else None,
            'mean_alpha_pct':round(statistics.mean(alphas),2) if alphas else None,
            'positive_return_pct':round(100*sum(v>0 for v in returns)/len(returns),2) if returns else None}
    return result


def bootstrap_difference(baseline, challenger, field, samples=2000):
    # Paired decision-cohort block bootstrap preserves within-month dependence.
    groups = defaultdict(lambda: [[],[]])
    for i,rows in enumerate([baseline,challenger]):
        for row in rows:
            if not _is_complete(row['_tuning_outcome'],365):continue
            out=row['_tuning_outcome']
            if field=='hit':
                d=out.get('first_hit_days_by_target',{}).get('30')
                val=100.0 if d is not None and d<=365 else 0.0
            else:val=out.get(field)
            if isinstance(val,(int,float)):groups[row['decision_date']][i].append(val)
    blocks=list(groups.values());rng=random.Random(50);diffs=[]
    totals=[[(sum(v),len(v)) for v in block] for block in blocks]
    for _ in range(samples):
        sums=[0.,0.];counts=[0,0]
        for block in rng.choices(totals,k=len(totals)):
            for i,(s,n) in enumerate(block):sums[i]+=s;counts[i]+=n
        if all(counts):diffs.append(sums[1]/counts[1]-sums[0]/counts[0])
    diffs.sort()
    return [round(diffs[int(len(diffs)*p)],2) for p in [.05,.95]] if diffs else None


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--artifact',default='data/sharadar/reports/backtest-sfa-full.json')
    ap.add_argument('--out',default='data/sharadar/reports/quality-threshold-50-vs-70.json')
    args=ap.parse_args();path=Path(args.artifact)
    rows=[];digest=hashlib.sha256()
    with path.open('rb') as stream:
        while chunk:=stream.read(8*1024*1024):digest.update(chunk)
    with path.open('rb') as stream:
        for row in ijson.items(stream,'observations.item',use_float=True):
            scores={c['id']:c.get('score') for c in row['categories']}
            # Keep all observations for episode boundaries; retain compact inputs.
            keep={k:row.get(k) for k in ['observation_id','ticker','issuer_key','sector','regime','decision_date','entry_date','score','vetoes','valuation','horizons','_tuning_outcome']}
            keep['scores']=scores
            keep['price']=row['buy_entry_alignment']['valuation']['price']
            dilution=next((i.get('actual') for c in row['categories'] for i in c['items'] if i['id']=='dilution'),None)
            if isinstance(dilution,(int,float)) and dilution>10 and not keep['vetoes']:
                keep['vetoes']=[{'id':'substantial_dilution'}]
            keep['roic_direction']={'passes':False}
            rows.append(keep)
    print(json.dumps({'stage':'loaded','observations':len(rows)}),flush=True)
    candidates=[r for r in rows if not r['vetoes'] and all(
        isinstance(r['scores'].get(k),(int,float)) and r['scores'][k]>=minimum
        for k,minimum in [('quality',50),('survival',70),('growth',70)])
        and r['valuation'].get('method_count',0)>=1
        and r['price']<=r['valuation']['fair_base']]
    with SharadarWarehouse(read_only=True) as wh:
        wh.con.execute('SET threads=4')
        wh.con.register('quality_candidates',pd.DataFrame([{'id':r['observation_id'],'ticker':r['ticker'],'asof':r['decision_date']} for r in candidates]))
        annual=wh.con.execute('''SELECT c.id,f.reportperiod,f.roic FROM quality_candidates c JOIN sfa_fundamentals f ON c.ticker=f.ticker
          WHERE f.dimension='ARY' AND f.datekey<=CAST(c.asof AS DATE) AND f.reportperiod<=CAST(c.asof AS DATE)
          AND f.reportperiod>=CAST(c.asof AS DATE)-INTERVAL 6 YEAR
          QUALIFY row_number() OVER(PARTITION BY c.id,year(f.reportperiod) ORDER BY f.reportperiod DESC,f.datekey DESC)=1
          ORDER BY c.id,f.reportperiod''').fetchall()
        latest=wh.con.execute('''SELECT c.id,f.roic FROM quality_candidates c JOIN sfa_fundamentals f ON c.ticker=f.ticker
          WHERE f.dimension='ART' AND f.datekey<=CAST(c.asof AS DATE) AND f.reportperiod<=CAST(c.asof AS DATE)
          QUALIFY row_number() OVER(PARTITION BY c.id ORDER BY f.reportperiod DESC,f.datekey DESC)=1''').fetchall()
        histories=defaultdict(list)
        for ident,period,value in annual:histories[ident].append(value*100 if value is not None else None)
        latest=dict(latest)
        for row in candidates:
            v=latest.get(row['observation_id'])
            row['roic_direction']=assess(histories[row['observation_id']],v*100 if v is not None else None)
        # Check batch query parity against the production historical implementation.
        sample=random.Random(50).sample(candidates,min(20,len(candidates)))
        for row in sample:
            assert row['roic_direction']['passes']==historical(wh.con,row['ticker'],row['decision_date'])['passes']
    print(json.dumps({'stage':'roic_attached','candidates':len(candidates),'parity_checks':len(sample)}),flush=True)
    report={'generated_at':datetime.now(timezone.utc).isoformat(),'source':str(path),'sha256':digest.hexdigest(),
      'observations':len(rows),'cohorts':len({r['decision_date'] for r in rows}),
      'window':[min(r['decision_date'] for r in rows),max(r['decision_date'] for r in rows)],
      'method':'Frozen full point-in-time replay inputs/outcomes; reapply category thresholds, valuation, hard vetoes and historical ROIC. EMA/OBV optional in both policies.',
      'sectors':['Technology','Consumer Cyclical','Communication Services'],
      'execution':'Next close; existing outcomes include 15 bps entry and 15 bps exit costs; SPY benchmark.',
      'episode_rule':'First Buy of each contiguous issuer run; a non-Buy or gap over 45 days starts a new episode.',
      'censoring':'Primary target rates use only full 365-day follow-up or terminal events, including early winners only once mature.',
      'limitations':['Cached financial scores and outcomes were reused, not recomputed from raw filings.','Chronological slices are stability checks, not a newly untouched holdout.','Episode holding windows can overlap; rates are not independent portfolio trades.','Worst loss from entry is not peak-to-trough drawdown.','No claim that existing historical reports validate the changed production rule.','Bootstrap clusters by decision month; repeated issuers and adjacent months may remain correlated.'],
      'policies':{},'comparisons':{}}
    episodes={}
    for name,threshold in [('quality_70',70),('quality_50',50),('incremental_50_to_69',50)]:
        selected=[]
        for row in rows:
            passed=qualifies(row,threshold)
            if name.startswith('incremental'):passed=passed and row['scores']['quality']<70
            selected.append({**row,'verdict':'Buy' if passed else 'Watch'})
        roots=_episode_roots(selected,{}, {},45,use_recorded_verdict=True);episodes[name]=roots
        groups={}
        for dimension,fn in [('by_year',lambda r:r['entry_date'][:4]),('by_sector',lambda r:r['sector']),('by_regime',lambda r:r['regime']),
            ('by_period',lambda r:'1998-2014' if r['entry_date']<'2015' else '2015-2019' if r['entry_date']<'2020' else '2020-2026')]:
            buckets=defaultdict(list)
            for r in roots:buckets[fn(r)].append(r)
            groups[dimension]={k:summarize(v) for k,v in sorted(buckets.items())}
        report['policies'][name]={'buy_observations':sum(r['verdict']=='Buy' for r in selected),'summary':summarize(roots),**groups}
    report['comparisons']['hit_30_change_pp_ci90']=bootstrap_difference(episodes['quality_70'],episodes['quality_50'],'hit')
    report['comparisons']['one_year_alpha_change_pp_ci90']=bootstrap_difference(episodes['quality_70'],episodes['quality_50'],'alpha_pct')
    out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    write_html(report, out.with_suffix('.html'))
    print(json.dumps({'output':str(out),'summaries':{k:v['summary'] for k,v in report['policies'].items()},'comparisons':report['comparisons']}),flush=True)

def write_html(report, path):
    labels={'quality_70':'Quality ≥70', 'quality_50':'Quality ≥50', 'incremental_50_to_69':'Quality 50–69 only'}
    def fmt(value):
        return '—' if value is None else str(value)
    def table(headers, rows):
        return '<table><thead><tr>'+''.join('<th>'+escape(str(v))+'</th>' for v in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+escape(fmt(v))+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table>'
    def summary_rows(groups):
        return [[label, s['mature_episodes'], s['hit_30_pct'], s['one_year_return_pct']['mean'], s['one_year_alpha_pct']['mean'], s['one_year_return_pct'].get('loss_20_or_worse_pct'), s['worst_loss_from_entry_pct']['median']] for label,s in groups]
    headers=['Policy / group','Mature episodes','Hit +30% in 1y (%)','Mean 1y return (%)','Mean excess vs SPY (pp)','1y losses ≥20% (%)','Median worst loss from entry (%)']
    body='<h1>Business Quality minimum: 50 versus 70</h1><p>Lowering the threshold adds opportunities, with broadly similar one-year target success and slightly weaker average returns. The test does not establish improved performance.</p>'
    body+='<p><b>Scope:</b> '+str(report['observations'])+' point-in-time observations across '+str(report['cohorts'])+' monthly decision dates, '+escape(' to '.join(report['window']))+'. Technology, Consumer Cyclical and Communication Services only. Active and delisted issuers; share classes deduplicated.</p>'
    body+='<p><b>Both policies:</b> Financial Strength ≥70, Growth ≥70, price at or below calculated fair value, current hard vetoes and historical ROIC-direction gate. Monthly EMA and weekly OBV optional. Quality weights remain 30% gross margin, 35% ROIC and 35% operating margin.</p>'
    body+='<h2>One-year comparison</h2>'+table(headers,summary_rows([(labels[k],v['summary']) for k,v in report['policies'].items()]))
    body+='<p>The 90% decision-month block-bootstrap interval for the change in +30% success rate is '+escape(str(report['comparisons']['hit_30_change_pp_ci90']))+' percentage points. The interval includes zero. For mean one-year excess return, the interval is '+escape(str(report['comparisons']['one_year_alpha_change_pp_ci90']))+' points. These intervals do not fully account for repeated issuers or correlated adjacent months.</p>'
    body+='<p>Quality 50–69 is analyzed as its own sequence of episodes. Episode counts are not additive: changing eligibility can move entry dates and merge consecutive Buy runs.</p>'
    for group,title in [('by_period','Chronological stability'),('by_sector','Sector results'),('by_regime','Market regimes')]:
        body+='<h2>'+title+'</h2>'+table(headers,summary_rows([(labels[k]+' / '+name,s) for k,v in report['policies'].items() for name,s in v[group].items()]))
    body+='<h2>Holding horizons</h2>'+table(['Policy','Calendar days','Completed outcomes','Mean return (%)','Median return (%)','Mean excess vs SPY (pp)','Positive returns (%)'],[[labels[k],h,s['n'],s['mean_return_pct'],s['median_return_pct'],s['mean_alpha_pct'],s['positive_return_pct']] for k,v in report['policies'].items() for h,s in v['summary']['horizons'].items()])
    body+='<h2>Target sensitivity</h2>'+table(['Policy','Mature episodes','+20% hit (%)','+25% hit (%)','+30% hit (%)','+35% hit (%)'],[[labels[k],v['summary']['mature_episodes']]+[v['summary']['hit_'+str(t)+'_pct'] for t in [20,25,30,35]] for k,v in report['policies'].items()])
    body+='<details><summary>Annual results</summary>'+table(headers,summary_rows([(labels[k]+' / '+name,s) for k,v in report['policies'].items() for name,s in v['by_year'].items()]))+'</details>'
    body+='<h2>Method and limitations</h2>'+''.join('<p>'+escape(report[key])+'</p>' for key in ['method','execution','episode_rule','censoring'])+'<ul>'+''.join('<li>'+escape(x)+'</li>' for x in report['limitations'])+'</ul>'
    body+='<p>Target attainment is a closing-price touch during the year, not the return from holding for a full year. The primary denominator excludes immature observations, including early winners, to avoid favoring recent winners over unresolved losers. One-year loss frequency measures the final return; worst loss from entry measures the lowest interim cost-adjusted return.</p>'
    body+='<p>No new portfolio simulation or daily entry replay was performed. This is a controlled threshold comparison on the full saved monthly replay, not a rerun of every raw fundamental calculation.</p>'
    body+='<p>Generated '+escape(report['generated_at'])+'<br>Source: '+escape(report['source'])+'<br>Source SHA-256: '+report['sha256']+'</p>'
    path.write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Quality threshold backtest</title><style>body{font:16px/1.6 system-ui;color:#192d40;background:#f5f7fa;max-width:1150px;margin:auto;padding:32px}h1,h2{line-height:1.2}table{border-collapse:collapse;width:100%;background:white;font-size:14px;margin:20px 0}th,td{padding:9px;border:1px solid #dce2e8;text-align:right}th:first-child,td:first-child{text-align:left}th{background:#e5edf4}details{margin:24px 0}p{max-width:1000px}</style>'+body+'</html>',encoding='utf-8')


if __name__=='__main__':main()
