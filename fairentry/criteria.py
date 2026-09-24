"""Publish criteria from the same config and constants used by scoring."""
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import os
from .config import load_config
from .screeners.quality_growth import CRITERIA as Q
from .screeners.deep_value import CRITERIA as D
from .alerts import _MOVING_AVERAGE_ZONES
from .analytics.roic_direction import DECLINE_TOLERANCE_PP, MAX_EVIDENCE_AGE_DAYS, MAX_ANNUAL_AGE_DAYS


def generate(cfg=None):
    cfg = cfg or load_config()
    a = cfg.scoring['buy_entry_alignment']
    u = cfg.sectors['universe_filter']
    proximity = cfg.defaults.get('moving_average_zone_threshold_pct', 5)
    items = [f"Quality, financial strength and growth scores each at least {a['category_minimum']} out of 100.",
             f"Price at or below central fair value, with at least {a['fair_value_method_minimum']} usable valuation method(s).",
             "Weekly OBV above its 20-week exponential moving average."]
    if a.get('monthly_ema_required', True):
        items.append(f"Price within {a['ema_proximity_pct']}% of either monthly EMA.")
    if cfg.scoring.get('roic_direction_gate'):
        items.append(f'Recent ROIC direction must pass: last three annual observations plus latest trailing-year value; a drop greater than {DECLINE_TOLERANCE_PP} percentage points in any step or across the whole window means Watch. Missing/stale evidence also means Watch. No ROIC-level or volatility gate.')
    vetoes = [f"{v['reason']} ({v['when']})" for v in cfg.scoring.get('vetoes',[]) if v.get('decision_status','tested') == 'tested']
    li = lambda xs: '<ul>'+''.join('<li>'+escape(x)+'</li>' for x in xs)+'</ul>'
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FairEntry — Screening and Buy criteria</title><style>body{{font:17px/1.65 system-ui,sans-serif;background:#f5f7fa;color:#172537;margin:0}}main{{max-width:850px;margin:auto;padding:32px 24px}}section{{background:white;border:1px solid #d9e0e8;border-radius:12px;padding:24px;margin:20px 0}}h1,h2{{line-height:1.25}}li{{margin:10px 0}}a{{color:#145da0}}.note{{color:#526174;font-size:14px}}</style></head><body><main>
<a href="index.html">← Dashboard</a> · <a href="backtest.html?source=sfa">Backtest</a>
<h1>Screening &amp; Buy criteria</h1><p>Screening finds candidates. Passing a screen is not a Buy recommendation.</p>
<p><a href="index.html#criteria-explorer">Explore these criteria on the dashboard →</a> Choose a preset, adjust grouped filters, or search for an individual factor. Filters only change the displayed list; official recommendations remain unchanged. The explorer covers published candidates, not stocks excluded before publication.</p>
<p class="note">Generated with each board build from active configuration and screener constants. Updated {datetime.now(timezone.utc).isoformat(timespec='seconds')}. Deployment {escape(os.environ.get('GITHUB_SHA','local build')[:12])}.</p>
<section><h2>1. Official universe</h2>{li([f"Sectors: {', '.join(s['label'] for s in cfg.enabled_sectors)}", f"Market capitalization ≥ ${u['market_cap_min_usd']:,.0f}; price ≥ ${u['price_min_usd']}; average daily dollar volume ≥ ${u['avg_dollar_volume_min']:,.0f}."])}<p>Fresh price and required entry data are checked before recommendations are published. Emerging candidates are a separate research list.</p></section>
<section><h2>2. Candidate screens</h2><p><b>Quality Growth:</b> reported quarterly revenue growth versus a year earlier ≥ {Q['revenue_growth_min']}%; gross margin ≥ {Q['gross_margin_min']}% when available. Missing gross margin does not block this initial screen.</p><p><b>Deep Value:</b> P/B ≤ {D['pb_max']}, or P/S ≤ {D['ps_max']}, or positive P/FCF ≤ {D['pfcf_max']}; one-year price performance ≤ {D['performance_max']}%; debt/equity ≤ {D['debt_equity_max']} when available. Missing debt/equity does not block this initial screen.</p></section>
<section><h2>3. Buy requirements</h2>{li(items)}<p><b>Hard Avoid conditions:</b></p>{li(vetoes)}<p>Otherwise eligible stocks that miss a required entry condition remain Watch. Low-scoring stocks may remain Avoid. The displayed overall Buy score band is not a mandatory Buy threshold, and the +30% performance goal is not a guaranteed return or required valuation upside.</p></section>
<section><h2>4. Optional SMA filter</h2><p>Proximity is {'a required gate' if a.get('monthly_ema_required',True) else 'not a Buy gate'}. The dashboard filter shows fundamentally strong Buy/Watch stocks within ±{proximity}% of a selected average. It changes the displayed list, not the verdict.</p>{li([label for _,label in _MOVING_AVERAGE_ZONES])}<p>SMA gives each sampled closing price equal weight. Weekly averages use weekly closes, not daily closes. In-progress periods use only prices known so far.</p></section>
<section><h2>5. What does weekly volume confirm?</h2><p>OBV means On-Balance Volume. Each week, add that week's trading volume if the closing price rose versus the previous week; subtract it if the price fell; add zero if unchanged. Keep a running total.</p><p>The current gate requires that weekly running total to be above its 20-week EMA. This is a price-and-volume momentum signal, not proof of institutional buying, more buyers than sellers, or a future price increase. It does not require volume to be above average. Missing confirmation blocks Buy. The current partial week can affect the reading.</p></section>
<section><h2>Evidence limitations</h2><p>Live ROIC uses a dated assessment built from the local Sharadar history. It is not refreshed automatically by the live quote feed. Evidence older than {MAX_EVIDENCE_AGE_DAYS} days, an annual period older than {MAX_ANNUAL_AGE_DAYS} days, or missing evidence means Watch. Historical backtest reports retain their original rule versions; they are not automatically evidence for today's modified rules.</p></section>
</main></body></html>'''


def write(cfg=None):
    path = Path(__file__).resolve().parents[1] / 'web' / 'criteria.html'
    path.write_text(generate(cfg), encoding='utf-8')
    return path


if __name__ == '__main__':
    print(write())
