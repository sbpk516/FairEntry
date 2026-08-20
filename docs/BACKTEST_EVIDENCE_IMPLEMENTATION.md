# FairEntry Backtest Evidence — Reviewed Strategy and Implementation

## Purpose

Backtesting is the evidence system behind a FairEntry recommendation. It must
show not only whether Buy names outperformed after a fixed period, but the exact
historical decision, its frozen target, whether and when the target was reached,
the price path, the comparison benchmark, and the quality of every input.

The existing 30/60-day tests remain. Target attainment, 90/180/365-day outcomes,
drawdown, provenance, and stock-level drill-down are additive.

## Family-facing evidence design rule

The backtest is a decision record, not a scorecard. Its user interface follows
these permanent rules:

- begin with a short answer in plain English;
- explain every unfamiliar label beside the number with an information icon;
- let the reader open the exact rule, dates, data, calculation, and stock-level
  evidence without leaving the page;
- keep technical reproduction IDs behind an optional details panel;
- use charts, comparison cards, timelines, and year maps when they make a result
  easier to understand;
- never hide incomplete cases, repeated recommendations, Day 0 value references,
  different target time limits, or licensed-data sampling;
- keep thresholds, holding periods, counting choices, and filters selectable;
- prefer one reusable evidence calculation over page-specific calculations.

The Practical valuation goal starts at 30% by default. The fixed-return explorer
is a separate question and currently supports 10%, 15%, 20%, 25%, 30%, 40%,
50%, 75%, 100%, 150%, and 200% gains over 3, 6, 9, 12, 24, 36, and 60 months.

## Two primary success tests

The page now starts with two separate answers. Neither one changes how a stock
receives Buy, Watch, or Avoid:

1. **Fixed minimum result:** did one Buy episode reach a net +30% investment
   return within 365 calendar days? Later results remain visible as year 2,
   year 3, after year 3, never within three years, or still waiting.
2. **Frozen Practical Target:** did the first Buy reach the Practical Target
   calculated on that day before the target's size-based deadline? A target up
   to +30% gets one year, above +30% through +70% gets two years, above +70%
   through +120% gets three years, and above +120% through +271% gets five
   years. Larger targets receive a clearly calculated longer deadline.

A Practical Target reached after its promised deadline is recorded as
`reached_late`. It remains visible as evidence but does not count as an on-time
success. A target still inside its deadline is `active` and is not counted as a
failure. Targets already below the Buy price remain Day 0 references and do not
enter either post-entry success percentage.

Both tests use the first Buy in a continuous Buy episode. Later repeated Buy
signals remain available for audit, but they do not become separate investments.
The stock-level evidence freezes the first entry, the +30% amount, the Practical
Target, both deadlines, first hit dates, time taken, and the largest fall before
the result.

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
- **Target status:** `reached`, `expired`, `active`, `already_above_at_entry`,
  or unavailable. A value already below the entry price is a Day 0 valuation
  reference, not a post-entry target hit, and is excluded from hit-rate
  numerators and denominators.
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

### Phase 3 — Shared target models and practical expectation

- every existing fair-value method remains visible as a target milestone;
- `fundamental`: median of applicable non-analyst fair-value methods;
- `technical`: the configured minimum-upside continuation objective or recovery
  to the 200-week mean;
- `blended`: median of available fundamental and technical objectives;
- `practical`: the nearest credible objective that clears the same minimum-upside
  setting used by current screening (30% by default).

Targets are frozen at entry and include their basis, applicability and quality.
Below-minimum values remain visible as reference milestones rather than becoming
"unavailable." Analyst targets reconstructed from current-value proxies remain
visible but are excluded from historical practical-target selection.

The shared engine is called by both `build_board` and rolling replay. A valid
price always produces one practical target. The backtest enforces this as a
runtime invariant and refuses to publish an observation without both a practical
target price and an outcome status.

### Phase 4 — Outcome and reliability engine

The engine reports 30/60/90/180/365-day prices and returns, target status,
first hit date, time to target, maximum gain, and maximum drawdown. Completed
targets are separated from active/unavailable observations. Existing alpha,
Buy–Avoid spread, cohort bootstrap interval, and verdict ladder remain intact.

### Phase 5 — Progressive-disclosure evidence UI

The Backtest page has four explicit transparency layers before the stock ledger:

1. **Strategy contract:** the version, run identity, data/entry windows,
   population, cadence, warmup, costs, benchmark, horizons, target policy,
   quality mode and tuning policy actually recorded in the artifact.
2. **Validation health:** the Buy/Watch/Avoid ladder, raw and benchmark-relative
   returns, Buy-Avoid spread, cohort-bootstrap interval, monotonicity and visible
   acceptance-criteria checks.
3. **Stability and concentration:** year and sector diagnostics, best/worst Buy
   cohorts, quality-grade distribution, and explicit disclosure when prospective
   or challenger comparisons are not bundled in the artifact.
4. **Target calibration:** eligible, unique, Day 0, active, reached, expired and
   evaluable counts; hit rate with a 90% Wilson interval; median timing/upside;
   and the method's observed expiry range.

The filterable historical ledger then expands into a stock record with target
methods, horizon outcomes, price path, screen decisions, weights, factors,
sources, effective dates, and quality labels. The complete JSON can be
downloaded. Diagnostic year/sector tables are labelled as raw-return summaries,
not separately validated strategies.

### Phase 6 — Strategy laboratory and controlled tuning

The YAML contract is the reusable laboratory interface; CLI overrides create a
new strategy/run rather than editing production settings. Tuning remains a
challenger workflow. Scheduled jobs may calculate and publish candidates, but
`promotion: manual` prevents silent production changes. The SFA challenger now
uses fixed-return calibration, time-to-target, large-loss, drawdown and SPY
guardrails from strict point-in-time history. Factor-level tuning remains a
separate later step so the first search does not overfit too many choices.

## Trust boundaries

This implementation makes limitations visible; it does not erase them. The
current universe is still today's seeded top-N and target detection uses weekly
closes. A result dominated by current-proxy fields should be treated as
experimental. Production adoption requires adequate sample size, chronological
validation, an untouched test period, stability across regimes, and comparison
against simple baselines.

## Correctness invariants

- A cohort contributes observations and target outcomes only after it passes
  the configured minimum-population requirement.
- Reported returns and benchmark-relative alpha use the configured
  cost-adjusted entry price, matching the execution contract shown in the UI.
- The weekly sampling grace period may prove that a target expired, but a price
  first observed after contractual expiry is never counted as a successful hit.
- Values already below the entry price remain Day-0 references and stay outside
  post-entry target hit-rate denominators.
- Contract values for entry, target-hit rule and benchmark are validated against
  implemented capabilities; unsupported values fail at load time instead of
  generating a misleading new strategy ID.
- `target.models` filters the shared target engine's output. The configured list
  explicitly names every model intended for disclosure.
- Quality modes are operational: `experimental` includes every source,
  `mostly_point_in_time` excludes configured current proxies, and `strict` also
  excludes approximated sources.
- Historical candidates use the same primary-strategy tie-break and configured
  per-strategy preset as the live board. Separate Deep Value and Quality Growth
  summaries make future preset divergence visible.
- Target tables report unique stocks/cohorts, Wilson and cohort-block intervals,
  per-entry-year calibration, and each method's actual expiry range.
- The scheduled seeded report also reads the separate live store's prospective
  signal ledger. Prospective outcomes are displayed as a survivorship-clean
  control and are never pooled with seeded results.

## Promotion policy

The tuner runs as part of the full replay and publishes a plain-English
comparison and a keep-or-review decision. It never edits scoring configuration,
commits code, or deploys weights. Promotion remains a manual, audited version
change.
