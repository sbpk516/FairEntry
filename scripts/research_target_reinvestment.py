"""Executable monthly-signal portfolio research; fixed selection, daily marking.

Run research_three_year_targets.py first: its frozen development/validation
selection determines the entry rules here. See the research README for commands.
Exit variants are comparisons, not automatically promoted production policies.
"""
from collections import defaultdict
import json
import argparse
from pathlib import Path
import statistics
import sys
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.research_three_year_targets import load_rows,attach_roic,passes,SPLITS
from fairentry.sharadar import SharadarWarehouse


def simulate(signals, start_index, dates, prices, terminal, target, stop=None, reinvest=True):
    # Mark positions daily, execute previously queued exits first, then admit
    # only today's precomputed monthly signals. Never fill at the trigger close.
    # Ten 10% entry slots are not daily rebalancing; existing weights can drift.
    # Cash earns zero. Cohort start dates overlap and are not independent trials.
    start=dates[start_index];end_index=int(np.searchsorted(dates,start+np.timedelta64(1095,'D')))
    if end_index>=len(dates):return None
    cash=1.;positions={};cooldown={};nav=[];trades=0;total_exits=0
    for i in range(start_index,end_index+1):
        today=dates[i]
        for ticker,pos in list(positions.items()):
            quote=prices[ticker][i]
            t=pos['terminal'];forced=t is not None and today>=t[0]
            if np.isfinite(quote):pos['mark']=quote
            if forced and t[1]=='zero':pos['mark']=0.
            # A stop is not a guaranteed loss cap: gaps and later reinvestment
            # can produce much larger portfolio losses than the stop threshold.
            if forced or (pos['pending'] and np.isfinite(quote)):
                cash+=pos['shares']*pos['mark']*.9985
                del positions[ticker];cooldown[pos['issuer']]=today+np.timedelta64(30,'D');total_exits+=1
                continue
            gain=pos['mark']/pos['entry']*.9985/1.0015-1
            if (target is not None and gain>=target) or (stop is not None and gain<=stop):pos['pending']=True
        if i<end_index and (reinvest or i==start_index) and today in signals:
            held={p['issuer'] for p in positions.values()}
            choices=[r for r in signals[today] if r['ticker'] not in positions and r['issuer'] not in held
                     and cooldown.get(r['issuer'],np.datetime64('1900-01-01'))<=today]
            for r in choices:
                slots=10-len(positions)
                if slots<=0:break
                ticker=r['ticker'];quote=prices[ticker][i]
                if not np.isfinite(quote) or quote<=0:continue
                nav_now=cash+sum(p['shares']*p['mark'] for p in positions.values())
                budget=min(cash/slots,nav_now*.10)
                if budget<=1e-8:continue
                positions[ticker]={'shares':budget/(quote*1.0015),'entry':quote,'mark':quote,
                                   'pending':False,'issuer':r['issuer'],'terminal':terminal.get(r['id'])}
                cash-=budget;trades+=1
        nav.append(cash+sum(p['shares']*p['mark']*.9985 for p in positions.values()))
    arr=np.asarray(nav);relative=arr/np.maximum.accumulate(np.maximum(arr,1.))-1
    annual=[arr[int(np.searchsorted(dates[start_index:end_index+1],start+np.timedelta64(h,'D')))] for h in [365,730,1095]]
    changes=[annual[0]-1,annual[1]/annual[0]-1 if annual[0] else -1,annual[2]/annual[1]-1 if annual[1] else -1]
    return {'start':str(start),'return_pct':round((arr[-1]-1)*100,3),'reached_double':bool(max(arr)>=2),
            'ended_double':bool(arr[-1]>=2),'annual30':all(v>=.30-1e-10 for v in changes),
            'max_drawdown_pct':round(float(min(relative))*100,3),'trades':trades,'exits':total_exits}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--period',choices=list(SPLITS),default='test');args=ap.parse_args()
    lower,upper=SPLITS[args.period]
    rows=load_rows(ROOT/'data/sharadar/reports/backtest-sfa-full.json',mature_only=False)
    with SharadarWarehouse(read_only=True) as wh:
        wh.con.execute('SET threads=4');attach_roic(rows,wh.con)
        prior=json.loads((ROOT/'data/sharadar/reports/three-year-target-research.json').read_text())
        names=sorted({x['rule'] for x in prior['selections'].values()}|{'current_quality50'})
        candidates=[r for r in rows if any(passes(r,n) for n in names)]
        tickers=sorted({r['ticker'] for r in candidates})
        spy=wh.con.execute("SELECT date,closeadj FROM canonical_benchmarks WHERE ticker='SPY' ORDER BY date").fetchdf()
        dates=spy['date'].to_numpy().astype('datetime64[D]')
        px={}
        for offset in range(0,len(tickers),100):
            wh.con.register('names',pd.DataFrame({'ticker':tickers[offset:offset+100]}))
            frame=wh.con.execute('SELECT ticker,date,closeadj FROM sfa_prices JOIN names USING(ticker) ORDER BY ticker,date').fetchdf()
            for ticker,f in frame.groupby('ticker'):
                series=f.set_index('date')['closeadj'];px[ticker]=series.reindex(pd.to_datetime(dates)).to_numpy()
            wh.con.unregister('names')
        terminal={}
        for r in candidates:
            t=r.get('terminal')
            if t:terminal[r['id']]=(np.datetime64(str(t['date'])[:10],'D'),t.get('terminal_return_policy'))
        starts=sorted({r['entry_date'] for r in rows if lower<=r['entry_date']<upper})
        result={'period':args.period,'entry_window':[lower,upper],
          'scope':'Three-year portfolio starts within the stated entry window, monthly signals, ten 10% slots, daily mark-to-market; following-close target/stop execution; 30-day issuer cooldown after exits; cash earns zero; 15 bps each side.',
          'selection':'Rules frozen by the development/validation selection from the preceding stock-level experiment. No test-period retuning.',
          'limitations':['Monthly start cohorts strongly overlap and are not independent trials.','No taxes; delisting handling inherited from replay; unknown delistings valued at last available close.','The available universe is limited to three sectors and previously screened stocks.','These are historical results, not forward probabilities.'],'results':{}}
        for name in names:
            signals=defaultdict(list)
            for r in candidates:
                if passes(r,name):signals[np.datetime64(r['entry_date'],'D')].append(r)
            for day in signals:signals[day].sort(key=lambda r:(-r['rank'],r['ticker']))
            for label,target,stop,reinvest in [('hold_no_reinvestment',None,None,False),('hold_add_to_empty_slots',None,None,True),('take30_reinvest',.30,None,True),('take100_reinvest',1.,None,True),('take100_stop30_reinvest',1.,-.30,True)]:
                outcomes=[simulate(signals,int(np.searchsorted(dates,np.datetime64(s,'D'))),dates,px,terminal,target,stop,reinvest) for s in starts]
                outcomes=[o for o in outcomes if o is not None];n=len(outcomes)
                returns=[o['return_pct'] for o in outcomes]
                summary={'cohorts':n,'mean_return_pct':round(statistics.mean(returns),2),'median_return_pct':round(statistics.median(returns),2),
                  'reached_double_pct':round(100*sum(o['reached_double'] for o in outcomes)/n,2),
                  'ended_double_pct':round(100*sum(o['ended_double'] for o in outcomes)/n,2),
                  'annual30_pct':round(100*sum(o['annual30'] for o in outcomes)/n,2),
                  'loss_pct':round(100*sum(v<0 for v in returns)/n,2),'worst_return_pct':min(returns),
                  'median_max_drawdown_pct':round(statistics.median(o['max_drawdown_pct'] for o in outcomes),2),
                  'worst_max_drawdown_pct':min(o['max_drawdown_pct'] for o in outcomes),
                  'mean_trades':round(statistics.mean(o['trades'] for o in outcomes),1)}
                result['results'][name+'/'+label]={'summary':summary,'cohorts':outcomes}
                print(json.dumps({'rule':name,'exit':label,**summary}),flush=True)
        bench=[];spyvals=spy['closeadj'].to_numpy()
        for s in starts:
            i=int(np.searchsorted(dates,np.datetime64(s,'D')));j=int(np.searchsorted(dates,dates[i]+np.timedelta64(1095,'D')))
            if j<len(dates):bench.append((spyvals[j]/spyvals[i]*.9985/1.0015-1)*100)
        result['spy_benchmark']={'cohorts':len(bench),'mean_return_pct':round(statistics.mean(bench),2),'median_return_pct':round(statistics.median(bench),2),'ended_double_pct':round(100*sum(x>=100 for x in bench)/len(bench),2),'worst_return_pct':round(min(bench),2)}
    (ROOT/('data/sharadar/reports/three-year-reinvestment'+('' if args.period=='test' else '-'+args.period)+'.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')

if __name__=='__main__':main()
