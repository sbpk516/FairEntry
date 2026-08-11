# SFA point-in-time backtest

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
returns of 10%, 15%, 20%, 25%, 30% and 50% within 90, 180, 270, 365 and 730
calendar days. Attainment uses the first dividend-adjusted daily close that
clears the threshold after configured entry and exit costs.

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

Run `sfa-bfdabde939fa` used snapshot `20260810T132048Z` and implementation
commit `0708270`. It evaluated 333 monthly cohorts from October 1998 through
June 2026, comprising 41,896 issuer-deduplicated observations.

- The 30-day Buy-minus-Avoid alpha spread was +1.17 percentage points. Its
  cohort-block 90% interval was +0.03 to +2.25, while the more conservative
  cohort-and-issuer interval was -0.45 to +2.93. Treat the short-horizon edge
  as promising, not yet robustly proven.
- The 365-day and 548-day spreads were -2.27 and -3.66 percentage points, with
  intervals crossing zero. The strategy does not demonstrate a reliable
  long-horizon selection edge in this replay.
- The Practical target hit rate was 40.9% (108 of 264 evaluable Buys). The FCF
  hit rate was 31.6%; 29 FCF values were already below entry and are disclosed
  separately rather than counted as post-entry hits.
- The constrained Buy portfolio compounded +526.21%, but experienced a -64.44%
  maximum drawdown and an approximate 0.40 Sharpe ratio. Absolute return alone
  is therefore not an acceptance criterion.
- Neither the Deep Value nor Quality Growth challenger passed both validation
  and untouched-test gates. No weight promotion is recommended.
- Consecutive Buy recommendations collapsed from 335 signals to 227 investment
  episodes. Among mature episodes, 123 of 224 (54.9%) reached a net +25% within
  one year; 148 of 222 (66.7%) reached it within two years. Median time among
  successful episodes was 111 and 143 days respectively.
