# FairEntry Backtest Evidence — Reviewed Strategy and Implementation

## Purpose

Backtesting is the evidence system behind a FairEntry recommendation. It must
show not only whether Buy names outperformed after a fixed period, but the exact
historical decision, its frozen target, whether and when the target was reached,
the price path, the comparison benchmark, and the quality of every input.

The existing 30/60-day tests remain. Target attainment, 90/180/365-day outcomes,
drawdown, provenance, and stock-level drill-down are additive.

## Review findings

The original rolling harness correctly reconstructs metrics as of each cohort,
uses the production screeners/scorer, calculates point-in-time sector medians,
and uses cohort-block bootstrap intervals. Its principal limitations were:

- no immutable per-stock decision artifact;
- no frozen, reproducible target or time-to-target measurement;
- no multi-horizon price path in the UI;
- no field-level disclosure of reconstructed/current-proxy inputs;
- no versioned strategy/run identity;
- a current top-N universe, which creates survivorship bias;
- tuner output optimizes horizon alpha and recommends but does not auto-promote.

## Definitions

- **Entry:** the historical snapshot close plus configured slippage and costs.
- **Primary target hit:** a sampled adjusted close at or above the frozen target.
- **Target status:** `reached`, `expired`, `active`, or unavailable.
- **Active:** the target was not reached and the complete expiry window is not
  present. Active observations are never counted as failures.
- **Target time:** calendar days from entry to the first qualifying close.
- **Point-in-time:** genuinely observable at entry.
- **Reconstructed:** derived from a filing date and historical prices.
- **Current proxy:** today's value copied into history; never presented as
  historically authentic.

The seeded dataset is weekly. Therefore target hits mean a sampled weekly close,
not an intraday touch. Daily OHLC history is required before claiming intraday
hit statistics.

## Six-phase implementation

### Phase 1 — Versioned strategy contract

`config/backtest.yaml` controls universe policy, execution assumptions,
benchmarks, horizons, target models, data-quality mode, and tuner promotion.
Every configuration has a deterministic strategy ID; every run has a run ID.

### Phase 2 — Immutable evidence ledger

Each rolling observation contains entry, classification, score, weights,
thresholds, screening outcomes, vetoes, gates, category/item traces, valuation,
field-level provenance and a data-quality grade. The generated JSON artifact is
the immutable portable ledger for that run.

### Phase 3 — Target models

- `fundamental`: median of non-analyst fair-value methods available at entry;
- `technical`: 10% continuation or recovery to the 200-week mean, capped;
- `blended`: median of available fundamental and technical targets.

Targets are frozen at entry, bounded by configured minimum/maximum upside, and
include their basis. Analyst targets are excluded from the primary historical
target because the seeded dataset cannot prove their point-in-time value.

### Phase 4 — Outcome and reliability engine

The engine reports 30/60/90/180/365-day prices and returns, target status,
first hit date, time to target, maximum gain, and maximum drawdown. Completed
targets are separated from active/unavailable observations. Existing alpha,
Buy–Avoid spread, cohort bootstrap interval, and verdict ladder remain intact.

### Phase 5 — Progressive-disclosure evidence UI

The Backtest page moves from aggregate results to target summary, filterable
historical recommendations, and an expandable stock record with target methods,
horizon outcomes, price path, screen decisions, weights, factors, sources,
effective dates, and quality labels. The complete JSON can be downloaded.

### Phase 6 — Strategy laboratory and controlled tuning

The YAML contract is the reusable laboratory interface; CLI overrides create a
new strategy/run rather than editing production settings. Tuning remains a
challenger workflow. Scheduled jobs may calculate and publish candidates, but
`promotion: manual` prevents silent production changes. Future tuning should add
target calibration, time-to-target and drawdown to alpha only after strict
point-in-time data is sufficiently deep.

## Trust boundaries

This implementation makes limitations visible; it does not erase them. The
current universe is still today's seeded top-N and target detection uses weekly
closes. A result dominated by current-proxy fields should be treated as
experimental. Production adoption requires adequate sample size, chronological
validation, an untouched test period, stability across regimes, and comparison
against simple baselines.

## Promotion policy

The tuner runs automatically and publishes an `ADOPT`, `KEEP DEFAULT`, or
`NO GAIN` recommendation. It never edits scoring configuration, commits code,
or deploys weights. Promotion remains an explicit, audited version change.

