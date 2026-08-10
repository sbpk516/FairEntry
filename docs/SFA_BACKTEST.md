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
