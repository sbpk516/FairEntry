# SFA point-in-time backtest

## Decision-factor rule

Only a factor reproduced from information available on each historical decision
date may affect Buy / Watch / Avoid. The UI labels every factor as:

- **Tested** - used in the verdict.
- **Being tested** - replayed and displayed, but has no verdict effect yet.
- **Information only** - displayed as context and has no verdict effect.

AI/news review, current analyst forecasts, and current-only warning panels are
information only. Long-term debt burden change is currently being tested.

FairEntry keeps the licensed Nasdaq Data Link / Sharadar SFA snapshot private
and publishes only non-reconstructable derived evidence. Raw files, the DuckDB
warehouse, private evidence and API credentials are gitignored.

## Storage layout

```text
data/sharadar/
  LATEST
  snapshots/<UTC snapshot id>/
    manifest.json
    metadata/*.json
    raw/*.zip
    extracted/<TABLE>/*.csv
  warehouse.duckdb
  reports/backtest-sfa-full.json
```

Every table manifest records the vendor snapshot timestamp, metadata refresh
time, byte size, SHA-256, columns and extraction paths. Preserve the applicable
licence/retention correspondence separately with the subscription records.
Every replay also records an implementation fingerprint covering the replay,
scoring, screening, target code and live model configuration, so a code or
weight change cannot silently reuse the same run identifier.

## Commands

```powershell
python scripts/sharadar_snapshot.py --build-warehouse
python scripts/sfa_backtest.py --start 2022-01-01 --top-n 300 --no-evidence
python scripts/sfa_backtest.py --step 30 --hold 30
python scripts/sfa_backtest.py --step 30 --hold 30 --tune
./scripts/run_sfa_validation.ps1
```

The private report retains full evidence. `web/data/backtest-sfa.json` removes
raw vendor observations, price paths and reconstructable factor values. The UI
loads SFA by default and offers a link to the legacy seeded replay.

## Point-in-time rules

- Historical eligibility uses each security's first and last price dates and
  includes delisted securities.
- The historical universe applies the same enabled sectors, $300M market-cap,
  $1 price and $20M average-dollar-volume floors as the live board before the
  top-N limit. Sector medians use that full eligible pre-screen population.
- Multiple listed share classes are grouped at issuer level before screening.
  The primary common-stock class is preferred, then liquidity and market cap;
  GOOG and GOOGL therefore count as one Alphabet idea rather than two trades.
- Financial statements use as-reported `ART` and `ARQ` dimensions and only rows
  whose `datekey` was available by the decision date.
- Unit contracts are normalized explicitly; for example, DAILY market
  capitalization (USD millions) is converted before it is divided by SF1 free
  cash flow (USD).
- The default entry is the next trading close. Every stock horizon and SPY
  benchmark period is anchored to that same executable entry date.
- Targets use split-adjusted closes; investment returns use fully adjusted
  closes so dividends and spinoffs are reflected.
- Alpha is measured against SPY total return. Cohort-mean alpha remains an
  executable alternative.
- Strict mode removes approximate/current-proxy inputs before screening and
  scoring. Coverage counts observed inputs only; a neutral missing-value score
  does not masquerade as evidence.
- Candidates below the factor-coverage threshold are rejected rather than
  silently reweighted on too little evidence.
- Insider transactions and broad institutional change use historical SF2/SF3A
  observations; beta and relative strength are calculated point-in-time.
- Bankruptcy produces a zero terminal value. Other delistings/acquisitions close
  at the final total-return quote, and no target may remain active after a known
  terminal event within its expiry.
- Entry and exit trading costs are included in returns and portfolio evidence.

## Primary success scorecards

Every full replay publishes two episode-level scorecards at the top of the
backtest page:

- a fixed net +30% result within one year, with later hits separated into year
  2, year 3, and after year 3; and
- the frozen Practical Target checked against a deadline based on the expected
  gain: 1 year through +30%, 2 years through +70%, 3 years through +120%, and
  5 years through +271%.

The first Buy starts both clocks. Consecutive Buy signals for the same issuer
remain one episode. A late Practical Target hit is shown but is not an on-time
success; a recent episode whose deadline has not passed remains active rather
than becoming a failure. The public artifact contains derived episode results,
not the licensed daily price rows.

## Known residual limitations

- SFA does not supply historical forward analyst EPS growth, analyst targets,
  news sentiment, short interest or FairEntry's full forensic review.
- `DAILY.pe` is trailing P/E and is explicitly labelled as a proxy for the
  legacy `fwd_pe` field required by the existing screener.
- A daily rolling average over roughly 1,000 sessions is used as the historical
  200-week mean proxy.
- Close-based execution does not model liquidity-dependent bid/ask spreads,
  market impact, taxes or partial fills.
- The SFA tuner selects Deep Value and Quality Growth challengers separately on
  a development partition, checks a later validation partition, then reports an
  untouched final test. Weight promotion remains manual.
- The direct Sharadar 13F breadth proxy is disclosed as approximate and is
  excluded by strict mode; it is not presented as FairEntry's curated-manager
  13F signal.

## Direct Sharadar migration

`fairentry.sharadar.direct.DirectSharadarClient` maps direct API tables to the
same logical SFA table names used by the warehouse. A future direct subscription
therefore changes ingestion and licence provenance, not the backtester or UI.
Direct-plan records must remain separable because their post-cancellation
deletion obligations may differ from the Nasdaq snapshot.

## Review cadence

- Update prospective outcomes daily or weekly.
- Run the full historical replay quarterly and after material strategy/code changes.
- Run `scripts/run_sfa_validation.ps1` for the complete local feature-build,
  replay, chronological tuner and test sequence.
- Do not tune weights on every run. Require improvement across blocked time
  folds and an untouched final holdout before manual promotion.

## Fixed-return achievement after a Buy

The backtest separately reports whether Buy recommendations reached fixed net
returns of 10%, 15%, 20%, 25%, 28%, 30%, 35%, 40%, 50%, 75%, 100%, 150% and 200% within
90, 180, 270, 365, 730, 1,095 and 1,825 calendar days. Attainment uses the first
dividend-adjusted daily close that clears the threshold after configured entry
and exit costs.

The primary view counts Buy episodes, not every repeated recommendation. A
consecutive run of Buy observations for one issuer starts at its first Buy and
remains one episode until a non-Buy observation or a gap longer than 1.5 cohort
intervals. The raw-signal view remains available for auditability.

Recent observations are not failures merely because their horizon has not
elapsed. A row is evaluable when it reaches the threshold early, the complete
horizon is observed, or the security reaches a terminal event. The UI displays
reached, evaluable and active counts, Wilson uncertainty, median time to hit and
the middle 50% of successful hit times.

## Earliest-entry and conservative-upside research

Every SFA run also tests whether FairEntry could have recognized an opportunity
before later repeated Buy signals. The first point-in-time official Buy in a
continuous episode starts the clock; later Buys never reset the entry price or
deadline. The primary outcome remains +30% within 365 days. Separate secondary
outcomes measure +50% within 730 days and +100% within 1,095 days.

The predeclared conservative-upside thresholds are 30%, 45%, 50% and 60%.
Conservative fair value is the lowest of at least two relevant replayable
methods after sector-inappropriate P/B is removed, and the highest retained
method may be no more than 75% above the lowest. The report shows both
upside-only results and a stricter stable-thesis subset.

The stable-thesis subset requires a comparable earlier snapshot within 120
days, no more than a 10% fall in conservative fair value, no more than a
10-point fall in the official score or Business Quality, Financial Strength or
Growth category, a passing point-in-time growth qualification, and no tested
hard veto. Missing history is reported as incomplete and excluded; it is never
assumed positive. Full-history and newest unseen-period results are displayed
separately. This research changes no score, weight or verdict.

## Movement-capacity and horizon research

The SFA report separately measures whether an earliest Buy had enough recent
price movement to make the fixed +30% one-year target plausible. Movement
capacity is the trailing range available on the Buy date:

`(highest adjusted daily high / lowest adjusted daily low - 1) * 100`

The calculation uses at most 252 prior trading sessions and requires at least
200. Shorter histories are unavailable rather than treated as passing. The
predeclared exclusive bands are below 20%, 20% to under 30%, 30% to under 45%,
45% to under 60%, and 60% or more. Direct below/above comparisons are also
reported for 20% and 30% cutoffs.

The same frozen episodes are measured against +30% within 365 days, +30%
within 730 days, and +50% within 730 days. This prevents a longer deadline from
being confused with an improvement in the original one-year objective. Each
group reports completed cases, success, time to target, median drawdown and
worst drawdown, with the newest chronological period displayed separately.

Annualized volatility is not compared directly with a cumulative 30% return.
The report shows 126-session realized volatility and target distance in
volatility units as context. It also combines a four-check price trend measure
with 63-session downside/upside volatility. Constructive trend requires a
trend score of at least 75; controlled downside requires a ratio of 1.10 or
less. These derived inputs use only prices on or before the Buy date. The
research remains explicitly not validated and changes no score, weight,
verdict or production exclusion.

## Frozen six-month relative-momentum research

The SFA report also evaluates one pre-registered market-confirmation
hypothesis. It calculates the stock's 126-session return minus its mapped
sector ETF's return, then compares that value with the same six-month relative
return 21 sessions earlier. The evidence is Supportive only when relative
momentum is positive and improving, Contradictory only when it is negative and
deteriorating, and Neutral otherwise.

The calculation uses the latest aligned stock/sector trading session strictly
before the earliest official Buy. The primary outcome is +30% within one year;
+25% within one year and +30% within two years are secondary outcomes. Missing
sector mappings or fewer than 148 aligned sessions are unavailable rather than
treated as neutral or supportive.

Every tested definition is recorded in
`config/relative_momentum_experiments.json`. The initial experiment tests
exactly one frozen definition and does not search alternative thresholds after
seeing outcomes. Results are shown for chronological development, validation,
and a one-time newest historical test, including candidates, successes,
failures, uncertainty, time to target and drawdown. Genuinely new shadow
outcomes are still required before promotion. The current board and historical
replay share the calculation in `fairentry/analytics/relative_momentum.py`; it
adds zero official points and never changes scoring, verdicts, positions or
alerts.

## Rare Capability and Execution Moat research

The SFA report ranks existing Buy episodes with a separate research-only
Capability Moat score. Its fixed components are proven execution (30%),
commercial-product proof (20%), a replication-barrier proxy (20%), competitive
scarcity (15%), and productive reinvestment (15%). R&D and capital intensity
are paired with ROIC, gross profitability, revenue consistency, margins and
cash flow, so spending alone cannot qualify a company. Proven execution and
commercial proof must each score at least 60, and at least 70% of component
weight must be available.

The primary concentration challenger selects at most the highest-ranked half
of each historical Buy-date cohort. Top-three and top-one variants are reported
separately. Every selector is compared with all official Buys using the same
+30%-within-365-days outcome, alpha, large-loss and drawdown evidence across
chronological development, validation and untouched final-test dates.

Full run `sfa-03438e60bf19` did not support promotion. All 618 Buy episodes
reached +30% within one year at 47.6%; the eligible top-half selector reached
44.1%. In the untouched final period, all Buys reached 54.5% versus 45.8% for
the top half, a -8.7 percentage-point change. Dated standardized competition,
switching-cost and technical-milestone history is also unavailable in the
current warehouse. The report therefore keeps `score_effect: 0`,
`verdict_effect: none`, and `promotion_allowed: false`.

## Exit-policy capacity replay and v2 research

FairEntry has one versioned exit state machine shared by paper tracking and the
SFA research replay. It evaluates information after the close and records an
instruction separately from its fill. Historical fills use the next available
close plus configured exit costs; the trigger price is never treated as a
guaranteed execution price.

The fixed precedence is terminal event, hard veto, a -25% catastrophic-loss
close, two distinct Avoid evaluations, first +30% profit, a 15% trailing close
on the remainder, frozen Practical Target, one-year stagnation, and a 730-day
maximum holding period. The +30% instruction realizes half the original
position. A loss exit starts a 30-session paper re-entry cooldown. Position
state retains entry, frozen target, peak, remaining fraction, Avoid count,
pending instruction, fills and the policy version.

Full run `sfa-03438e60bf19` evaluated 618 Buy episodes from 302 issuers. The
policy reduced the all-history fifth-percentile completed-trade result from
-67.38% to -29.70%, but mean episode return fell from 25.86% to 4.97%. On the
newest untouched 20%, the loss boundary improved from -56.11% to -29.51%,
while mean return fell from 38.37% to 2.81% and completed-trade win rate was
47.5%. The dominant exits were 200 catastrophic-loss exits and 149 trailing
profit exits. The mean-return promotion gate therefore failed.

The capacity-aware replay is now complete. It uses 20 equal target slots,
score-ranked entries, issuer de-duplication, actual cash availability and cash
redeployment. Portfolio equity and drawdown are marked daily; entry and exit
costs are included. Exit v1 returned 2.84% in the final unseen portfolio versus
32.02% for the 730-day hold, while maximum drawdown improved from -35.44% to
-30.87%. It failed the return gates and was not promoted.

Four frozen v2 variants were then compared using development data only. The
predeclared selector required at least five percentage points of development
loss-tail and portfolio-drawdown improvement, then maximized capacity-aware
return preservation. It selected `exit_v2_balanced_research`: a -35% loss
trigger, three Avoid confirmations, 25% profit realization at +50%, a 25%
trailing close, no Practical Target exit, 548-day stagnation and a 730-day
maximum hold.

The selected v2 policy improved final-unseen portfolio return to 58.80% versus
32.02% for hold and slightly improved drawdown, but failed independent
validation portfolio return by 0.16 percentage points and reduced final-unseen
episode mean return by 22.93 points. It is therefore closed as a rejected
challenger. Choosing another variant after seeing validation would contaminate
the experiment. The official score, verdict, paper policy and real-money effect
remain unchanged. A future exit experiment requires genuinely new shadow data
and a newly frozen hypothesis.

## Strict historical ticker identity

Strategy v3 rejects a historical row when the displayed ticker did not yet
exist on that decision date. Sharadar ACTIONS `tickerchangefrom` records define
when the current ticker became valid. For example, CLGX history before the 2010
change from FAF is excluded instead of being presented as if CLGX traded in
1999.

The exclusion happens before universe ranking, scoring, Buy creation and target
measurement. Each artifact publishes an identity-control audit with the number
of removed cohort rows, affected tickers and remaining violations. The runner
also refuses to publish an older or malformed artifact under the strict policy
unless remaining invalid observations equal zero. Corporate-action data is
identity metadata only and contributes no score.

## Latest completed replay

Run `sfa-03438e60bf19` used snapshot `20260810T132048Z`. It evaluated 333
monthly decision dates from October 1998 through June 2026, comprising 78,791
issuer-deduplicated observations and 1,766 different stocks.

- The strict ticker-identity control removed 39,691 otherwise eligible cohort
  rows across 1,088 displayed tickers. An independent scan found zero retained
  observations before their recorded ticker-effective date. CLGX's earliest
  remaining Buy is April 30, 2012, after its May 28, 2010 change from FAF.
- Among completed current-weight Buy episodes, 319 of 597 (53.4%) touched a net
  +25% within one year, 296 of 595 (49.7%) reached +28%, 283 of 595 (47.6%)
  reached the primary +30% target, and 254 of 595 (42.7%) reached +35%.
- The selected substantial-dilution veto is strictly greater than 10% YoY share
  growth. It removed 94 observations that otherwise qualified as Buy. Relative
  to the no-dilution-veto counterfactual, completed +30% attainment improved
  from 46.0% to 47.6% over full history and from 51.3% to 52.1% in the newest
  unseen period. The improvement is modest, not a guarantee.
- The complete integrity audit scanned all 78,791 observations: 7,792 carried
  the dilution veto across all score bands, zero substantially diluted Buy
  observations remained, and zero ticker-identity violations remained.

## One-year weight challenger

The SFA tuner now uses the investor goal directly. It searches one shared set
of effective weights for Business Quality, Financial Survival, Growth,
Valuation and Market Confirmation. Information-only groups have zero tuning
weight. The primary goal is a Buy episode touching +30% within 365 days. +25%
is the acceptable lower-target guardrail and +35% is the stronger-upside
guardrail; large losses, maximum decline and one-year return versus SPY must
also remain protected.

The evidence page separately calibrates the one official FairEntry score in
historical score bands. Each band reports +25%, +28%, +30% and +35% one-year
attainment and drawdown. This is descriptive evidence only: it creates no
second score and changes no verdict or production weight.

The replay stores a small derived one-year outcome for every screened
observation. This lets a challenger fairly turn a historical Watch into a Buy
without publishing or duplicating the licensed daily price rows. Repeated
weekly Buys for one issuer remain one episode.

The exact effective weights used by run `sfa-7853ef5c27eb` were Growth 33.75%,
Business Quality 22.50%, Valuation 18.75%, Market Confirmation 18.75%, and
Financial Survival 6.25%. These are displayed directly on both the SFA
evidence page and the live board. The former `18, 5, 27, 15, 15` display scale
is preserved as the named `pre_sfa_display_scale` profile; it has identical
proportions and therefore produces the same tested score.

Decision dates are chronological: 60% development, 20% validation and 20%
untouched final test. The tuner chooses weights using development only. It then
requires later-period improvement, sample-size checks and stability across
years and sectors. `promotion: manual` is operational: the report can say
"Ready for manual review," but it never edits the live preset.
