# Valuation disagreement and ROIC: initial research experiment

## Production promotion: recent direction only

The user approved promoting **direction only**, not level, volatility or valuation-disagreement gates. `config/scoring.yaml` enables `roic_direction_gate`. The shared production rule uses the last three annual ROIC values plus latest TTM; any adjacent decline greater than 2 percentage points, or a net decline greater than 2 points, caps Buy at Watch. No level minimum or large-jump exclusion applies. Existing hard Avoid decisions are preserved. Missing history is Watch.

The live provider does not supply the required annual history. Live exports use derived dated statuses in `config/roic_direction_snapshot.json`, generated from the local Sharadar warehouse with `python scripts/build_roic_direction_snapshot.py`. This file contains statuses and evidence dates, not the annual licensed ratios. Update the warehouse and regenerate/commit this file after financial-data refreshes. Evidence or snapshot older than 180 days, annual period older than 550 days, or future dates fail closed to Watch. GitHub refresh does not silently manufacture annual histories or refresh this snapshot. This operational limitation is intentional and must be maintained.

SFA replay obtains annual history as of each signal from the warehouse and never uses the present-day live snapshot. Other replay sources without annual evidence cannot produce Buy under this gate. The committed old SFA artifact is not rewritten as a new replay; the page explicitly labels it and separately shows the 54.0% fixed-entry direction-filter result.

Validation at promotion: 320 tests pass, including missing-history Buy caps, score preservation, future-revision exclusion, date freshness and the pattern examples. No expected improvement in full portfolio performance is claimed.

## Current version: three-part ROIC assessment (v3)

This section supersedes both older ROIC definitions below. Production scoring remains unchanged. The current runner emits separate level, direction and consistency assessments, an overall Pass/Watch, and plain-language Watch reasons for each episode.

- **Level proxy:** compare latest provider ROIC with an assumed 10% capital cost. Negative ROIC is labelled negative; 0%–below 10% is below assumed cost; 10%–below 13% is a small cushion; 13% or more is a healthy proxy. Only the last category passes this experiment. This is not verified economic health: the provider's ratio is not independently reconciled to an after-tax, WACC-compatible definition. No company-specific historical WACC has been established.
- **Direction:** use the last three annual observations plus latest TTM. A decline exceeding 2 percentage points in any adjacent step, or a net decline exceeding 2 points from the first selected annual value to latest TTM, triggers Watch. Older declines outside this recent window do not block an established recovery.
- **Consistency:** an absolute change exceeding 15 percentage points in any of those steps triggers Watch. This is a fixed candidate threshold, not an optimized or universal definition of volatility.
- **Coverage:** all three recent annual observations and latest TTM must be valid. Missing older observations do not matter. Missing recent data triggers Watch with an insufficient-evidence reason.
- **Overall:** Pass only if all three components pass; otherwise Watch, never automatic Fail/Avoid. Pass is eligibility for this research filter, not a standalone Buy recommendation. For backtest comparison, Watch entries are omitted without replacement.

Tests reproduce all four user examples: steady Apple-like growth passes; 20,18,15,15,18,18 passes; -20,-10,5,60,200 is Watch for large jumps; 40,35,30,20,15 is Watch for deterioration. Capital-cost boundaries and cumulative small declines are tested too.

### V3 backtest results

| Filter | Entries kept | Evaluated target outcomes | +30% in one year | Mean available one-year entry return |
|---|---:|---:|---:|---:|
| Baseline | 296 | 288 | 46.53% | 11.52% |
| Direction only | 117 | 113 | 53.98% | 14.19% |
| Level proxy only | 282 | 276 | 45.29% | 11.25% |
| Consistency only | 158 | 156 | 45.51% | 9.84% |
| All three ROIC checks | 74 | 74 | 52.70% | 12.84% |
| All three plus valuation 3x | 56 | 56 | 48.21% | 11.33% |

All-three ROIC retains 39 target successes and 35 target failures. It puts 222 entries on Watch, including 95 target successes, 119 target failures and 8 uncounted outcomes. Of Watch entries with a one-year return, 90 had negative returns. Target failures and negative returns are different concepts.

Period success rates (baseline vs all-three): before 2015 41.81% vs 48.72% (39 filtered evaluated entries); 2015–2019 50.00% vs 46.15% (13); 2020 onward 56.34% vs 63.64% (22). Small samples and mixed period performance do not establish robustness. Direction alone performs better in aggregate than all three; the level proxy and volatility constraint have not shown incremental aggregate benefit.

59 related tests passed. Results are retrospective fixed-entry filtering, not portfolio replay or untouched out-of-sample validation. No live scoring or public artifact changed.

All subsequent historical sections and their tables describe superseded experiments. The current machine-readable report is regenerated by `python scripts/valuation_roic_research.py`.

## Revised ROIC rule (supersedes the initial experiment below)

At the user's request, the active runner now tests **stable or increasing ROIC**, not the old positive-history/median-floor rule. It checks the latest five annual observations and compares latest TTM ROIC against the final annual value. Any adjacent decline fails; equal values pass. A 1e-9 percentage-point tolerance handles floating-point noise only. There is no 10% median requirement and no positive-years requirement: recovery from negative ROIC can pass.

At least three annual values are required and missing values within the selected history prevent passing. The report distinguishes `declining` from `insufficient_evidence`. This strict interpretation also rejects a rising overall sequence with an intervening annual decline. It is not a regression-slope or smoothed-trend test.

Examples: 40,35,30,20,15 fails; 15,18,20,22,25 passes; -20,-10,5,60,200 passes; 15,15,15 passes. Tests cover all of these and a decline in the latest TTM observation.

Revised run: 18 of 296 entries pass; all 18 have evaluated outcomes, with 9 reaching +30% within one year (50.0%, versus baseline 46.53%). It rejects 145 target failures and 125 target successes, plus 8 uncounted outcomes. This is a very restrictive rule and the remaining sample is too small to establish improvement. 56 related tests pass.

The current JSON output is regenerated by the runner. All numerical tables below are **archived results for the prior rule**, not results for the revised rule. Production remains unchanged.

Production effect: none. No website artifact, scoring rule, recommendation, or trading execution was changed by this experiment.

## Reproduce

```powershell
python scripts/valuation_roic_research.py
python -m pytest tests/test_valuation_roic_research.py tests/test_backtest_evidence.py -q
```

Private output: `data/sharadar/reports/valuation-roic-research.json`. It contains input provenance, all episode decisions, period comparisons and sector comparisons. Do not publish this licensed-data diagnostic without reviewing its contents.

## Definitions

- Universe: the 296 episode roots in the existing public artifact, matched to the private replay by ticker/entry and run ID, snapshot ID and implementation fingerprint. Outcomes are reused, not recalculated with new trades.
- Valuation: only FCF, peer P/S and book estimates; exclude unavailable, inapplicable, explicitly excluded, low-relevance or non-positive/non-finite estimates. Never count blended/median targets as independent methods. This uses existing relevance metadata, not a new audit of peer suitability or accounting assumptions.
- Two methods: require at least two qualifying estimates.
- Valuation 2x/3x/5x: require two methods AND highest/lowest at most the stated ratio. Thus these variants combine disagreement and evidence coverage. The separate two-method variant helps expose coverage effects; pure disagreement-only isolation remains future work.
- Sustained ROIC: latest five annual observations, at least three valid values, at least 80% positive and median at least 10%. Annual records must have report period and availability date no later than the signal, with a six-year freshness bound. One latest available revision per fiscal-ending year; not overlapping quarterly TTM samples.
- ROIC decline: same minimum history, latest TTM ROIC no more than 10 percentage points below the annual median. This is not a year-over-year decline test.
- Combined: valuation rule plus both ROIC conditions.
- Missing annual history causes insufficient-evidence rejection, not a conclusion that the company is bad. Provider ROIC and its capital denominator have not been independently reconstructed.

## Initial results

Target is +30% within 365 days, preserving the existing terminal-event and incomplete-history policy. Of 296 entries, 288 have counted target outcomes. A target failure is not necessarily a losing investment.

| Variant | Entries kept | Target success rate | Rejected target failures | Rejected target successes |
|---|---:|---:|---:|---:|
| Baseline | 296 | 46.53% | 0 | 0 |
| Two suitable methods | 272 | 45.28% | 9 | 14 |
| Valuation 2x | 194 | 42.11% | 44 | 54 |
| Valuation 3x | 238 | 43.10% | 22 | 34 |
| Valuation 5x | 264 | 44.57% | 11 | 19 |
| Sustained ROIC | 226 | 47.96% | 39 | 28 |
| ROIC decline | 259 | 47.43% | 21 | 14 |
| Combined 2x | 140 | 47.83% | 82 | 68 |
| Combined 3x | 166 | 46.30% | 67 | 59 |
| Combined 5x | 183 | 46.93% | 59 | 50 |

The baseline mean available one-year entry return is 11.52%; sustained ROIC gives 12.36%. These are equal-entry averages, NOT portfolio returns. Availability counts differ from target counts: some terminal outcomes can be counted without a one-year return, and a one-year horizon price can exist without the full target observation policy being satisfied.

## Chronological sensitivity

| Period | Baseline success | Sustained ROIC | Combined 2x |
|---|---:|---:|---:|
| Before 2015 | 41.8% (177 evaluated) | 45.0% (140) | 46.5% (86) |
| 2015–2019 | 50.0% (40) | 50.0% (36) | 54.2% (24) |
| 2020 onward | 56.3% (71) | 55.6% (45) | 46.4% (28) |

These are fixed-rule retrospective slices, not untouched holdout validation. Several cases were inspected before designing the rules. Do not select a production threshold from this table.

## Case checks

- COHU April 2000: 78.29x valuation disagreement; blocked by all valuation variants, passes both ROIC variants.
- BBOX November 2000: 5.98x disagreement; only two valid annual ROIC observations, so ROIC evidence is insufficient.
- CAN November 2021: one suitable valuation method; only two valid annual ROIC values, both negative.
- BABA June 2021: 2.76x disagreement; blocked at 2x but not 3x/5x. Passes both current ROIC variants. Its year-over-year deterioration is not equivalent to our median-relative rule.

## Decision and remaining validation

Keep official Buy rules unchanged. Valuation disagreement is useful as a review warning, but these simple hard filters reduced the target success rate. ROIC consistency has a small aggregate improvement that does not persist in the recent slice.

Before promotion: independently audit valuation suitability and ROIC denominators; isolate missing-data effects; add a separately specified year-over-year deterioration test; run a chronological selection/validation procedure without tuning on later outcomes; quantify uncertainty with issuer-aware grouping; and replay portfolios with a predefined cash/replacement policy to measure portfolio returns, time in cash and drawdown. None of those portfolio or pristine out-of-sample claims is established by this initial episode-filter experiment.
