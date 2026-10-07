"""Frozen-rule research for annual +30% versus doubling within three years.
No live recommendation changes. Raw vendor inputs stay in ignored data/.

Resume guide: docs/research/three-year-targets/README.md.
The 12 hypotheses and split boundaries below belong to the October 2026
experiment. Do not silently retune them against the already-seen test period.
Use a new version/output location for follow-up experiments.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import ijson
import numpy as np
import pandas as pd
from fairentry.sharadar import SharadarWarehouse
from fairentry.analytics.roic_direction import assess

# Predefined economic hypotheses, not a grid optimized on the final period.
RULES={
 'current_quality50': 'Current rules: Q≥50, strength/growth≥70, fair value, historical ROIC direction, no veto.',
 'quality_growth': 'Q≥70, strength≥70, growth≥70; no fixed-multiple fair-value requirement.',
 'balanced_growth': 'Q≥50, strength≥70, growth≥70, market confirmation≥60.',
 'quality_momentum': 'Q≥50, strength≥60, growth≥60, confirmation≥75, price above 200-day average.',
 'growth_acceleration': 'Sales growth≥20%, reported EPS growth≥20%, growth accelerating, positive FCF margin, dilution≤5%, strength≥60.',
 'durable_compounder': 'Three-year sales and EPS CAGR≥15%, ROIC≥15%, positive FCF, strength≥70.',
 'growth_at_reasonable_pe': 'Sales growth≥15%, reported EPS growth≥15%, trailing P/E 0–40, FCF margin≥5%, strength≥70.',
 'cash_compounder': 'ROIC≥15%, FCF margin≥10%, sales growth≥15%, at least 75% of recorded quarters FCF-positive, strength≥70.',
 'profitable_hypergrowth': 'Sales growth≥30%, operating margin≥10%, P/S≤15, dilution≤5%, strength≥70.',
 'recovery_momentum': 'Q≥40, strength≥70, growth≥80, positive operating margin and price above 200-day average.',
 'discounted_cash_growth': 'Q≥50, strength≥70, growth≥50, positive FCF margin and model upside≥30%.',
 'high_selectivity': 'Q≥85, strength≥85, growth≥85, confirmation≥70.',
}
# Three-year embargoes keep development/validation outcomes out of the next
# entry window. This dataset was explored previously; test is not pristine.
SPLITS={'development':('1998-01-01','2009-01-01'),
        'validation':('2012-01-01','2017-01-01'),
        'test':('2020-01-01','2023-08-08')}


def passes(row, name):
    if row['vetoes']:return False
    c=row['scores'];f=row['factors']
    def ge(k,v):return isinstance(f.get(k),(int,float)) and f[k]>=v
    def le(k,v):return isinstance(f.get(k),(int,float)) and f[k]<=v
    def cats(q,s,g,m=0):return all(isinstance(c.get(k),(int,float)) and c[k]>=v for k,v in [('quality',q),('survival',s),('growth',g),('confirmation',m)])
    if name=='current_quality50':return cats(50,70,70) and row['value_pass'] and row.get('roic_pass',False)
    if name=='quality_growth':return cats(70,70,70)
    if name=='balanced_growth':return cats(50,70,70,60)
    if name=='quality_momentum':return cats(50,60,60,75) and ge('price_above_sma200_pct',0)
    if name=='growth_acceleration':return cats(0,60,0) and ge('revenue_growth_yoy_pct',20) and ge('eps_growth_yoy_pct',20) and ge('revenue_growth_change_pp',0) and ge('fcf_margin_pct',0.01) and le('share_count_change_yoy_pct',5)
    if name=='durable_compounder':return cats(0,70,0) and ge('revenue_cagr_3y_pct',15) and ge('eps_cagr_3y_pct',15) and ge('roic_pct',15) and ge('fcf_margin_pct',0.01)
    if name=='growth_at_reasonable_pe':return cats(0,70,0) and ge('revenue_growth_yoy_pct',15) and ge('eps_growth_yoy_pct',15) and ge('trailing_pe',0.01) and le('trailing_pe',40) and ge('fcf_margin_pct',5)
    if name=='cash_compounder':return cats(0,70,0) and ge('roic_pct',15) and ge('fcf_margin_pct',10) and ge('revenue_growth_yoy_pct',15) and ge('positive_fcf_history_pct',75)
    if name=='profitable_hypergrowth':return cats(0,70,0) and ge('revenue_growth_yoy_pct',30) and ge('operating_margin_pct',10) and ge('price_to_sales',0.01) and le('price_to_sales',15) and le('share_count_change_yoy_pct',5)
    if name=='recovery_momentum':return cats(40,70,80) and ge('operating_margin_pct',0.01) and ge('price_above_sma200_pct',0)
    if name=='discounted_cash_growth':return cats(50,70,50) and ge('fcf_margin_pct',0.01) and row['upside']>=30
    if name=='high_selectivity':return cats(85,85,85,70)
    raise ValueError(name)


def path_outcome(row, dates, prices):
    """Measure touches separately from fills; a next-close sale can miss 2x.

    Prices are dividend-adjusted. Round-trip costs are 15 bps per side.
    Terminal outcomes inherit the replay's zero/last-close policy.
    """
    start=np.datetime64(row['entry_date'],'D');end=start+np.timedelta64(1095,'D')
    terminal=row.get('terminal') or {};td=terminal.get('date')
    terminal_date=np.datetime64(str(td)[:10],'D') if td else None
    cutoff=min(end,terminal_date) if terminal_date is not None else end
    lo=int(np.searchsorted(dates,start));hi=int(np.searchsorted(dates,cutoff,side='right'))
    if hi<=lo:return None
    p=prices[lo:hi];d=dates[lo:hi]
    net=p/row['adjusted_entry']*(1-.0015)/(1+.0015)-1
    if terminal_date is not None and terminal_date<=end and terminal.get('terminal_return_policy')=='zero':
        net=np.append(net,-1.);d=np.append(d,terminal_date)
    end_value=row['horizons'].get('1095',{}).get('return_pct')
    if end_value is None:return None
    result={'hold_return':end_value,'hold_alpha':row['horizons'].get('1095',{}).get('alpha_pct'),
            'worst_loss':round(float(min(net))*100,4),
            'max_drawdown':round(float(np.min((1+net)/np.maximum.accumulate(np.maximum(1,1+net))-1))*100,4)}
    annual=[row['horizons'].get(str(h),{}).get('return_pct') for h in [365,730,1095]]
    result['annual30']=False
    if all(v is not None for v in annual) and annual[0]>-100 and annual[1]>-100 and not (terminal_date is not None and terminal_date<end):
        annual_changes=[annual[0],((1+annual[1]/100)/(1+annual[0]/100)-1)*100,((1+annual[2]/100)/(1+annual[1]/100)-1)*100]
        result['annual30']=all(v>=30-1e-8 for v in annual_changes)
    for years,h in [(1,365),(2,730),(3,1095)]:
        mask=d<=start+np.timedelta64(h,'D');series=net[mask]
        for target in [30,100,200,400]:
            result[f'hit{target}_{years}']=bool(len(series) and np.max(series)>=target/100)
    for target in [30,100]:
        hits=np.flatnonzero(net>=target/100)
        result[f'exit{target}']=end_value
        result[f'exit{target}_days']=1095
        if len(hits):
            hit=int(hits[0]);result[f'hit{target}_days']=int((d[hit]-start).astype(int))
            following=lo+hit+1
            if following<len(prices) and dates[following]<=end and (terminal_date is None or dates[following]<=terminal_date):
                result[f'exit{target}']=round((prices[following]/row['adjusted_entry']*(1-.0015)/(1+.0015)-1)*100,4)
                result[f'exit{target}_days']=int((dates[following]-start).astype(int))
    target=np.flatnonzero(net>=1);stop=np.flatnonzero(net<=-.30)
    result['stop30_exit100']=result['exit100']
    if len(stop) and (not len(target) or stop[0]<target[0]):
        following=lo+int(stop[0])+1
        if following<len(prices) and dates[following]<=end and (terminal_date is None or dates[following]<=terminal_date):
            result['stop30_exit100']=round((prices[following]/row['adjusted_entry']*(1-.0015)/(1+.0015)-1)*100,4)
        else:result['stop30_exit100']=end_value
    return result


def roots(rows, name):
    # One position per issuer, maximum 3-year lockout. No overlapping same-name bets.
    last={};out=[]
    for r in rows:
        if not passes(r,name):continue
        day=np.datetime64(r['entry_date'],'D');key=r['issuer']
        if key in last and (day-last[key]).astype(int)<1095:continue
        last[key]=day;out.append(r)
    return out


def wilson(successes,n):
    if not n:return [None,None]
    z=1.644853626951;p=successes/n;den=1+z*z/n
    center=(p+z*z/(2*n))/den;half=z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [round(float((center-half)*100),2),round(float((center+half)*100),2)]


def summary(rows):
    rows=[r for r in rows if r.get('outcome')];n=len(rows)
    if not n:return {'n':0}
    out={'n':n,'issuers':len({r['issuer'] for r in rows})}
    for target in [30,100,200,400]:
        for y in [1,2,3]:
            out[f'hit{target}_{y}_pct']=round(100*sum(r['outcome'][f'hit{target}_{y}'] for r in rows)/n,2)
    for key in ['annual30']:
        out[key+'_pct']=round(100*sum(r['outcome'][key] for r in rows)/n,2)
    for key in ['hold_return','exit30','exit100','stop30_exit100']:
        vals=[r['outcome'][key] for r in rows]
        out[key]={'mean':round(statistics.mean(vals),2),'median':round(statistics.median(vals),2),
                  'doubled_pct':round(100*sum(v>=100 for v in vals)/n,2),'loss_pct':round(100*sum(v<0 for v in vals)/n,2),
                  'loss30_pct':round(100*sum(v<=-30 for v in vals)/n,2),'worst':round(min(vals),2)}
    out['hit100_3_ci90']=wilson(sum(r['outcome']['hit100_3'] for r in rows),n)
    out['hit30_3_ci90']=wilson(sum(r['outcome']['hit30_3'] for r in rows),n)
    out['median_worst_loss']=round(statistics.median(r['outcome']['worst_loss'] for r in rows),2)
    out['median_max_drawdown']=round(statistics.median(r['outcome']['max_drawdown'] for r in rows),2)
    out['worst_max_drawdown']=round(min(r['outcome']['max_drawdown'] for r in rows),2)
    days=[r['outcome'].get('hit100_days') for r in rows if r['outcome'].get('hit100_days') is not None]
    out['median_days_to_double']=round(statistics.median(days),1) if days else None
    return out


def portfolio(rows,name):
    cohorts=defaultdict(list)
    for r in rows:
        if passes(r,name) and r.get('outcome'):cohorts[r['decision_date']].append(r)
    results=[]
    for day,rs in sorted(cohorts.items()):
        # Fixed selection before examining forward outcomes; at most 10 names.
        rs=sorted(rs,key=lambda r:(-r['rank'],r['ticker']))[:10]
        out={'date':day,'positions':len(rs)}
        for method in ['hold_return','exit30','exit100','stop30_exit100']:
            # 10% allocation per name; unfilled slots and exit proceeds stay in cash at 0%.
            out[method]=sum(r['outcome'][method] for r in rs)/10
        results.append(out)
    output={'cohorts':len(results),'minimum_positions':min((r['positions'] for r in results),default=0),'median_positions':statistics.median([r['positions'] for r in results]) if results else None}
    for method in ['hold_return','exit30','exit100','stop30_exit100']:
        vals=[r[method] for r in results]
        output[method]={'mean_return':round(statistics.mean(vals),2) if vals else None,'median_return':round(statistics.median(vals),2) if vals else None,
                        'doubled_pct':round(100*sum(v>=100 for v in vals)/len(vals),2) if vals else None,'worst_return':round(min(vals),2) if vals else None}
    output['note']='Monthly start cohorts overlap. 10 equal 10% slots, cash for missing slots; no reinvestment after exit. These are terminal portfolio returns, not daily portfolio drawdowns. Months with zero qualifying names are omitted.'
    return output


def load_rows(path, mature_only=True):
    """Load frozen entry-date inputs, never select on later realized returns.

    August 7, 2023 is the maturity cutoff for the August 7, 2026 warehouse.
    Refresh BOTH this cutoff and SPLITS deliberately when extending history.
    Portfolio reinvestment needs mature_only=False to admit later entry signals.
    """
    out=[]
    with path.open('rb') as stream:
        for r in ijson.items(stream,'observations.item',use_float=True):
            # Uniform full follow-up cutoff excludes immature winners as well as losers.
            if mature_only and r['entry_date']>'2023-08-07':continue
            h=r.get('horizons',{})
            if mature_only and h.get('1095',{}).get('return_pct') is None:continue
            scores={c['id']:c.get('score') for c in r['categories']}
            factors=r.get('research_factors') or {}
            vetoes=list(r.get('vetoes') or [])
            if (factors.get('share_count_change_yoy_pct') or 0)>10:vetoes.append({'id':'dilution'})
            a=r['buy_entry_alignment'];val=a['valuation']
            row={'id':r['observation_id'],'ticker':r['ticker'],'issuer':r.get('issuer_key') or r['ticker'],
                 'sector':r['sector'],'decision_date':r['decision_date'],'entry_date':r['entry_date'],
                 'adjusted_entry':r['_entry_closeadj'],'terminal':r.get('terminal_event'),
                 'horizons':h,'scores':scores,'factors':factors,'vetoes':vetoes,
                 'value_pass':val['passes'],'upside':r['valuation'].get('upside_pct') or 0,
                 'regime':r['regime']}
            row['rank']=sum((scores.get(k) or 0)*w for k,w in [('quality',.25),('survival',.15),('growth',.35),('confirmation',.25)])
            out.append(row)
    return sorted(out,key=lambda r:(r['entry_date'],r['ticker']))


def attach_roic(rows,con):
    """Reconstruct the current baseline ROIC gate from filings known at entry.

    Never replace this with today's live ROIC snapshot in a historical test.
    Other hypotheses intentionally do not inherit this gate unless specified.
    """
    candidates=[r for r in rows if r['value_pass'] and not r['vetoes'] and all((r['scores'].get(k) or 0)>=v for k,v in [('quality',50),('survival',70),('growth',70)])]
    con.register('candidates',pd.DataFrame([{'id':r['id'],'ticker':r['ticker'],'asof':r['decision_date']} for r in candidates]))
    ann=con.execute('''SELECT c.id,f.reportperiod,f.roic FROM candidates c JOIN sfa_fundamentals f USING(ticker)
      WHERE dimension='ARY' AND datekey<=CAST(c.asof AS DATE) AND reportperiod<=CAST(c.asof AS DATE)
      AND reportperiod>=CAST(c.asof AS DATE)-INTERVAL 6 YEAR
      QUALIFY row_number() OVER(PARTITION BY c.id,year(reportperiod) ORDER BY reportperiod DESC,datekey DESC)=1 ORDER BY c.id,reportperiod''').fetchall()
    latest=dict(con.execute('''SELECT c.id,f.roic FROM candidates c JOIN sfa_fundamentals f USING(ticker)
      WHERE dimension='ART' AND datekey<=CAST(c.asof AS DATE) AND reportperiod<=CAST(c.asof AS DATE)
      QUALIFY row_number() OVER(PARTITION BY c.id ORDER BY reportperiod DESC,datekey DESC)=1''').fetchall())
    history=defaultdict(list)
    for ident,period,value in ann:history[ident].append(value*100 if value is not None else None)
    for r in candidates:
        v=latest.get(r['id']);r['roic_pass']=assess(history[r['id']],v*100 if v is not None else None)['passes']
    con.unregister('candidates')


def run():
    ap=argparse.ArgumentParser();ap.add_argument('--out',default='data/sharadar/reports/three-year-target-research.json');args=ap.parse_args()
    rows=load_rows(ROOT/'data/sharadar/reports/backtest-sfa-full.json')
    print(json.dumps({'loaded':len(rows)}),flush=True)
    with SharadarWarehouse(read_only=True) as wh:
        wh.con.execute('SET threads=4');attach_roic(rows,wh.con)
        selected=[r for r in rows if any(passes(r,name) for name in RULES)]
        by_ticker=defaultdict(list)
        for r in selected:by_ticker[r['ticker']].append(r)
        tickers=sorted(by_ticker)
        for offset in range(0,len(tickers),100):
            batch=tickers[offset:offset+100];wh.con.register('names',pd.DataFrame({'ticker':batch}))
            frame=wh.con.execute('SELECT p.ticker,p.date,p.closeadj FROM sfa_prices p JOIN names n USING(ticker) WHERE closeadj>0 ORDER BY ticker,date').fetchdf()
            for ticker,prices in frame.groupby('ticker',sort=False):
                dates=prices['date'].to_numpy().astype('datetime64[D]');values=prices['closeadj'].to_numpy()
                for r in by_ticker[ticker]:r['outcome']=path_outcome(r,dates,values)
            wh.con.unregister('names')
            print(json.dumps({'priced_tickers':min(offset+100,len(tickers)),'total':len(tickers)}),flush=True)
    split_rows={name:[r for r in rows if start<=r['entry_date']<end] for name,(start,end) in SPLITS.items()}
    report={'generated_at':datetime.now(timezone.utc).isoformat(),'rules':RULES,'splits':SPLITS,'observations':len(rows),'policies':{},'selections':{}}
    # Freeze selection using only development and validation results.
    for name in RULES:
        report['policies'][name]={split:summary(roots(rs,name)) for split,rs in split_rows.items() if split!='test'}
    for objective in ['hit100_3_pct','hit30_3_pct']:
        eligible=[]
        for name,v in report['policies'].items():
            dev=v['development'];val=v['validation']
            if dev['n']>=80 and val['n']>=40 and val.get('issuers',0)>=25:
                eligible.append(name)
        key='hit100_3_ci90' if objective.startswith('hit100') else 'hit30_3_ci90'
        chosen=max(eligible,key=lambda name:(min(report['policies'][name]['development'][key][0],report['policies'][name]['validation'][key][0]),report['policies'][name]['validation'][objective])) if eligible else None
        report['selections'][objective]={'rule':chosen,'selection':'Highest minimum development/validation Wilson lower bound; ≥80 development episodes and ≥40 validation episodes / 25 issuers.'}
    print(json.dumps({'frozen_selections':report['selections']}),flush=True)
    chosen_names={s['rule'] for s in report['selections'].values()}|{'current_quality50'}
    for name in RULES:
        test=roots(split_rows['test'],name);report['policies'][name]['test']=summary(test)
        if name in chosen_names:
            report['policies'][name]['test_portfolio']=portfolio(split_rows['test'],name)
            groups=defaultdict(list)
            for r in test:groups[r['sector']].append(r)
            report['policies'][name]['test_by_sector']={k:summary(v) for k,v in groups.items()}
            report['policies'][name]['test_by_year']={y:summary([r for r in test if r['entry_date'].startswith(y)]) for y in ['2020','2021','2022','2023']}
    report['limitations']=[
      'Research only; no live recommendations or deployment changed.',
      'Frozen scores and fundamentals reused from prior point-in-time replay; daily forward price outcomes recalculated locally.',
      'Only previously screened Technology, Consumer Cyclical and Communication Services companies; not all global stocks.',
      'Entry at next close, 15 bps per entry/exit, dividend-adjusted closes. Targets observed at close, exits at following close; no intraday-perfect fills.',
      'All included entries have three-year follow-up or terminal outcomes. Unknown delistings follow the existing last-close policy; bankruptcy zero-return policy retained.',
      'Development entries end in 2008, validation begins 2012 and ends 2016, test begins 2020. Three-year outcome gaps reduce leakage.',
      'The dataset has been explored in earlier research; this is not a truly pristine external holdout. Rules selected before revealing test results in this run.',
      'Wilson intervals treat trades as independent and may be too narrow; issuers and market cycles remain correlated.',
      'One entry per issuer per 1095 days for stock statistics; this is not an immediately reinvested trading portfolio.',
      'Portfolio cohorts overlap, omit zero-signal months and hold unused/realized capital in cash at zero interest. No taxes modeled.',
      'Yearly +30% means each separate anniversary-to-anniversary year, not simply 30% CAGR.',
      '3x and 5x statistics are threshold touches, not guaranteed realizable exits.',
    ]
    out=ROOT/args.out;out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(out),'test_results':{k:v['test'] for k,v in report['policies'].items()}}),flush=True)

if __name__=='__main__':run()
