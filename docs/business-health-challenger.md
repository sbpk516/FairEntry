# Business Health Challenger v1

`business_health_v1` is a frozen, research-only alternative to the current
Business Quality and Growth calculations. It cannot alter the production Buy
list. Survival, valuation, market confirmation, hard vetoes, soft gates, and
the 72-point Buy threshold remain unchanged so the comparison isolates the new
business-health evidence.

## Factors

Business Quality keeps the proposed 40/25/20/15 split across ROIC quality,
operating-margin level, margin trend, and FCF quality. ROIC quality combines
current level (50%), five-year durability (30%), and three-year trend (20%).
FCF quality combines FCF margin (50%), operating-cash conversion (30%), and the
share of recent trailing periods with positive FCF (20%).

Growth uses the proposed revenue, diluted-EPS, FCF, and acceleration factors.
Market share is registered as information-only because no broad, consistent,
point-in-time historical source is available. Its proposed 15% is redistributed
proportionally across the four replayable factors for this challenger.

Three-year CAGR is calculated only when both endpoints are positive. A move
from negative to positive EPS or FCF is scored as a recovery rather than as an
invalid or extreme CAGR. At least 12 trailing ROIC observations are required
for the durability component, and at least eight FCF observations are required
for positive-FCF consistency. A Quality or Growth coverage level below 60%
cannot create a challenger Buy.

All frozen weights and thresholds live in
`config/business_health_challenger.json`.

## Running the comparison

The normal private SFA replay and `--publish-private` flow attach the factor
history and emit a `business_health_challenger` report automatically. The
report includes baseline and challenger Buy counts and completed one-year
+30% hit rates. Per-observation challenger results remain separately labelled
with `production_effect: none`; the original verdict is never overwritten.

The SFA enrichment uses filings whose `datekey` is no later than the decision
date. It deduplicates amended filings by fiscal period before calculating lags,
preventing later amendments or future reports from entering an earlier
decision.
