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
returns of 10%, 15%, 20%, 25%, 30%, 40%, 50%, 75%, 100%, 150% and 200% within
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

## Latest completed replay

Run `sfa-927c352ce0b3` used snapshot `20260810T132048Z`. It evaluated 333
monthly decision dates from October 1998 through June 2026, comprising 73,843
issuer-deduplicated observations under the new testable-factor contract.

- The 30-day Buy-minus-Avoid extra-return difference was +0.45 percentage
  points. Its date-block likely range was -0.33 to +1.30, so this is not a
  proven short-term advantage.
- Among 644 completed current-weight Buy episodes, 312 (48.4%) touched a net
  +25% within one year. The 90% likely range was 45.2% to 51.7%, and successful
  episodes typically took 110.5 days. A net +30% was reached by 278 (43.2%).
- The Practical value hit rate was 32.7%: 256 of 783 completed Buy goals.
- The constrained Buy portfolio compounded +103.69%, but experienced an
  -84.47% maximum drawdown and an approximate 0.24 Sharpe ratio. This confirms
  that absolute return alone is not an acceptance criterion.
- The older-period winner increased Growth, Quality, Survival and Market
  Confirmation while reducing Valuation. It improved +25% attainment from
  48.4% to 51.4% on development and from 50.8% to 53.3% on validation.
- It failed promotion: validation one-year extra return versus SPY was -4.04%,
  compared with -3.24% for the current weights. The untouched final-period
  +25% rate was 46.9% versus 47.0% for the current weights. The lower end of
  its likely range was also slightly worse.
  The live default therefore remains unchanged.

## One-year weight challenger

The SFA tuner now uses the investor goal directly. It searches one shared set
of effective weights for Business Quality, Financial Survival, Growth,
Valuation and Market Confirmation. Information-only groups have zero tuning
weight. The primary goal is a Buy episode touching +25% within 365 days; +30%,
large losses, maximum decline and one-year return versus SPY are guardrails.

The replay stores a small derived one-year outcome for every screened
observation. This lets a challenger fairly turn a historical Watch into a Buy
without publishing or duplicating the licensed daily price rows. Repeated
weekly Buys for one issuer remain one episode.

Decision dates are chronological: 60% development, 20% validation and 20%
untouched final test. The tuner chooses weights using development only. It then
requires later-period improvement, sample-size checks and stability across
years and sectors. `promotion: manual` is operational: the report can say
"Ready for manual review," but it never edits the live preset.
