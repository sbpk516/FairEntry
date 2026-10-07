"""Render the aggregate three-year strategy research without private stock inputs."""
import argparse
import json
from html import escape
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REPORTS=ROOT/'data/sharadar/reports'


def render(reports=REPORTS):
    # Tables read the JSON artifacts. Narrative numbers describe the frozen
    # October 2026 experiment and MUST be reviewed after a new data/rule run.
    # --reports-dir also renders the checked-in aggregates without vendor data.
    stock=json.loads((reports/'three-year-target-research.json').read_text())
    portfolios={p:json.loads((reports/('three-year-reinvestment'+('' if p=='test' else '-'+p)+'.json')).read_text()) for p in ['development','validation','test']}
    def table(headers,rows):
        return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+escape(str(h))+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+escape('—' if v is None else str(v))+'</td>' for v in r)+'</tr>' for r in rows)+'</tbody></table></div>'
    names={k:k.replace('_',' ').title() for k in stock['rules']}
    b='<h1>Can a strategy double capital within three years?</h1><p class="subtitle">Evidence review · 12 entry methods · 5 portfolio exit/reinvestment policies · historical data through August 7, 2026</p>'
    b+='<div class="callout"><strong>No tested method supports a 75% probability of doubling.</strong> Uneven three-year growth was much more common than earning 30% in every individual year. A +30% total target was easier, but reaching it is not equivalent to doubling a portfolio.</div>'
    b+='<h2>What was compared</h2><p>30% in each of three years compounds to +119.7%. Doubling in three years is +100% total, or approximately 26.0% annualized. Returns do not need to arrive evenly. We separately measured target touches, next-close realized exits, and a portfolio’s marked-to-market and final value.</p>'
    b+='<p>Reused '+str(stock['observations'])+' mature observations from the complete saved point-in-time replay. Recalculated forward target/exit paths from adjusted daily prices. The universe includes screened Technology, Consumer Cyclical and Communication Services names, including delisted issuers; it is not the entire market.</p>'
    b+='<h2>Later-period individual-stock results</h2><p>Entries in 2020–August 2023 with outcomes through August 2026. No overlapping positions in the same issuer within a rule’s three-year stock-level test. All percentages below are observed historical frequencies, not forecasts.</p>'
    fields=['n','hit30_3_pct','hit100_3_pct','annual30_pct','hit200_3_pct','hit400_3_pct']
    b+=table(['Entry rule','Positions','+30% within 3y (%)','2× within 3y (%)','+30% every year (%)','3× touch (%)','5× touch (%)'],[[names[k]]+[v['test'].get(f) for f in fields] for k,v in stock['policies'].items()])
    b+='<h2>Which candidates were selected before test-period comparison?</h2>'
    b+='<p><strong>For doubling:</strong> Discounted Cash Growth. <strong>For +30% total:</strong> Current Quality 50. Selection maximized the weaker development/validation Wilson lower confidence bound, subject to ≥80 development positions, ≥40 validation positions and ≥25 validation issuers. The final test did not determine these selections.</p>'
    b+='<p>Durable Compounder happened to have the highest doubling frequency in the later test (41.94%), but was weaker in development and validation. Choosing it because of that later result would be retrospective selection. Treat it as an exploratory hypothesis, not a validated winner.</p>'
    b+=table(['Rule','Entry period','Positions','2× within 3y (%)','+30% within 3y (%)'],[[names[k],period,v[period]['n'],v[period].get('hit100_3_pct'),v[period].get('hit30_3_pct')] for k,v in stock['policies'].items() for period in ['development','validation','test']])
    b+='<h2>Portfolio results with executable reinvestment</h2><p>10 slots, initially at most 10% of capital per name. Rank by 25% Quality, 15% Financial Strength, 35% Growth and 25% Market Confirmation. Monthly signals determine entries; target/stop breaches are observed at the close and executed at the next available close. Empty slots and proceeds wait in cash at 0% until a qualifying monthly signal. A 30-day issuer cooldown follows exits. Entry and exit costs are 15 basis points each. No leverage or tax modeling.</p>'
    b+='<p>“Take30” sells at a +30% closing signal; “Take100” sells at +100%. Stop30 adds a −30% closing signal. Actual fills can be worse than the trigger. Portfolio target touches below are net-of-liquidation-cost mark-to-market values; there is no automatic portfolio-wide exit at 2×.</p>'
    for period,p in portfolios.items():
        b+='<h3>'+period.title()+' portfolio windows</h3>'
        b+=table(['Rule / exit policy','Starts','Mean total return (%)','Median return (%)','Reached portfolio 2× (%)','Ended at 2× (%)','+30% each year (%)','Worst drawdown (%)'],[[k,v['summary']['cohorts'],v['summary']['mean_return_pct'],v['summary']['median_return_pct'],v['summary']['reached_double_pct'],v['summary']['ended_double_pct'],v['summary']['annual30_pct'],v['summary']['worst_max_drawdown_pct']] for k,v in p['results'].items()])
        q=p['spy_benchmark'];b+='<p>SPY, same entry windows and three-year horizon: mean '+str(q['mean_return_pct'])+'%, median '+str(q['median_return_pct'])+'%, '+str(q['ended_double_pct'])+'% of windows finished doubled. Monthly start windows overlap heavily; they are not independent trials.</p>'
    b+='<h2>Practical interpretation</h2><ol><li><b>Prefer an uneven three-year objective over a promise of +30% each year.</b> The latter occurred in only 0–2.15% of later-period stock cases and none of the tested later-period portfolio windows.</li><li><b>Do not promote a 75% doubling claim.</b> Even the best later-period individual-stock rule reached 2× less than half the time. The best observed portfolio variant touched 2× in about 21% of later starts and ended doubled in about 9%.</li><li><b>Do not confuse a +30% success rate with attractive whole-portfolio results.</b> Losers, cash waiting for new signals, and exits all matter. Reinvesting +30% winners did not reliably create three consecutive 30% portfolio years.</li><li><b>Keep the aggressive strategy in research.</b> Discounted Cash Growth with a 2× take-profit and 30% stop had stronger recent mean returns, but its older-period worst drawdown was approximately 68%. It did not meet the requested success target.</li></ol>'
    b+='<h2>Fixed hypotheses</h2>'+table(['Name','Entry requirements'],[[names[k],v] for k,v in stock['rules'].items()])
    b+='<h2>Sample separation and limitations</h2><p>Development entries: 1998–2008. Validation entries: 2012–2016. Test entries: 2020–August 2023. Three-year gaps prevent the earlier group’s outcomes from extending into the following entry group. The dataset has been researched before, so this is not a pristine external holdout. Exit alternatives are reported side by side rather than declaring the best test result a newly validated strategy.</p><ul>'+''.join('<li>'+escape(x)+'</li>' for x in stock['limitations'])+'</ul>'
    b+='<p>Potential future work: industry-specific growth-aware valuation, a broader point-in-time universe, and prospective paper trading with frozen rules. These are unvalidated possibilities, not evidence of a high chance of 2×, 3× or 5×. No live scoring or deployment was changed by this research.</p>'
    b+='<p><a href="https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-47">SEC: interpreting investment performance claims</a>. Historical and hypothetical performance do not establish future returns.</p>'
    b+='<p class="subtitle">Generated from local research artifacts dated '+escape(stock['generated_at'])+'. Full code: scripts/research_three_year_targets.py and scripts/research_target_reinvestment.py.</p>'
    out=reports/'three-year-strategy-comparison.html'
    out.write_text('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Three-year investment research</title><style>body{font:16px/1.65 system-ui;color:#152d40;background:#f5f7fa;max-width:1280px;margin:auto;padding:32px}h1,h2,h3{line-height:1.25}h2{margin-top:38px}.subtitle{color:#526979}.callout{border-left:5px solid #bc6b20;padding:18px;background:#fff3df}.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px;background:white;margin:18px 0}th,td{padding:9px;border:1px solid #d9e0e6;text-align:right}th{background:#e3edf4}th:first-child,td:first-child{text-align:left}p,li{max-width:1100px}li{margin:12px 0}a{color:#145a88}</style></head><body>'+b+'</body></html>',encoding='utf-8')
    print(out)

if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--reports-dir', type=Path, default=REPORTS)
    render(parser.parse_args().reports_dir)
