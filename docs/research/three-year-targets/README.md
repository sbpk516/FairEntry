# Three-year return research — resume here

Status: completed initial experiment; **research only, not a production strategy**.
Branch: `codex/three-year-return-research`.
Production baseline at branch creation: `d6b28e2` (Quality minimum 50; EMA/OBV optional).

## User objective

Find which path has stronger evidence: earning at least 30% in each year, or
doubling capital within at most three years with uneven annual returns. Desired
success rate: above 75%. +30% total is a secondary goal, not a substitute for
doubling. The user allowed fundamental, valuation, momentum and technical
approaches but asked for tested evidence and explicit uncertainty.

## What we found

**No tested strategy demonstrated a 75% chance of doubling.** Uneven three-year
growth was more common than +30% in each separate year. Do not describe observed
historical rates as forward probabilities.

Later-period stock results (2020–August 2023 entries, outcomes through August 2026):

| Rule | Positions | +30% within 3 years | 2× within 3 years | +30% each year |
|---|---:|---:|---:|---:|
| Current Quality 50 | 67 | 74.63% | 37.31% | 1.49% |
| Discounted cash growth | 90 | 65.56% | 37.78% | 1.11% |
| Durable compounder | 93 | 79.57% | 41.94% | 2.15% |

The stock figures above are **target touches**, not executed sales or portfolio
returns. Durable compounder was strongest in the later test but weaker earlier;
selecting it based on this table would be retrospective selection.

Discounted cash growth was selected for the doubling objective using development
and validation results; Current Quality 50 was selected for the +30% objective.
The aggressive discounted-cash-growth portfolio variant (take profit at +100%,
stop signal at −30%, reinvest) had these later-period results:

- Mean three-year return: +57.67%.
- Reached portfolio 2×: 20.93% of 43 overlapping monthly start windows.
- Finished at 2×: 9.30% of those windows.
- Worst portfolio drawdown: −48.96% in the test period, −68.42% in development.
- No later-period portfolio window achieved +30% in all three separate years.

This is not evidence for promoting that exit variant. Older-period losses are
material, and stop signals do not cap portfolio losses at the stop percentage.

## Preserved artifacts

- [Readable report](three-year-strategy-comparison.html): all methods, periods,
  exits, reinvestment, SPY comparison, assumptions and limitations.
- [Stock results](three-year-target-research.json): 12 hypotheses and frozen selections.
- [Portfolio test results](three-year-reinvestment.json).
- [Portfolio development results](three-year-reinvestment-development.json).
- [Portfolio validation results](three-year-reinvestment-validation.json).
- [Manifest](manifest.json): input identity, source hash and aggregate-artifact hashes.

These files contain derived results, not raw vendor prices or financial records.
The licensed warehouse and full private source replay remain under ignored
`data/sharadar/`; a Git clone does **not** restore those inputs. Keep an authorized
separate backup of that directory if exact raw-data reproducibility is required.

## Code map

- `scripts/research_three_year_targets.py`: fixed entry hypotheses, point-in-time
  ROIC, daily target paths, individual-stock holding/exit comparisons and selection.
- `scripts/research_target_reinvestment.py`: daily portfolio marking, next-close
  exits, monthly reinvestment and comparisons with SPY.
- `scripts/render_three_year_research.py`: HTML report from the aggregate JSONs.
- `tests/test_three_year_targets.py`: touches versus fills, annual compounding,
  stops, hard vetoes and nonoverlapping issuer entries.
- `tests/test_target_reinvestment.py`: portfolio execution, idle cash and bankruptcy.

## Review saved results without the private warehouse

Open the checked-in HTML report above. To rebuild its tables from saved results,
run from the repository root:

```powershell
python scripts/render_three_year_research.py --reports-dir docs/research/three-year-targets
```

The renderer contains narrative numbers tied to this frozen experiment. Review
and update that prose when creating a new experiment; rendering alone does not
automatically update every explanatory sentence.

## Rerun the experiment

Prerequisites: repository Python dependencies, including `ijson`, `numpy`,
`pandas`, `duckdb`; the private full replay and warehouse named in the manifest.
Do not substitute the sampled public `web/data/backtest-sfa.json` for the full
private replay. Check the source SHA-256 for an exact reproduction.

Run in this order from the repository root:

```powershell
python scripts/research_three_year_targets.py
python scripts/research_target_reinvestment.py --period development
python scripts/research_target_reinvestment.py --period validation
python scripts/research_target_reinvestment.py --period test
python scripts/render_three_year_research.py
python -m pytest tests/test_three_year_targets.py tests/test_target_reinvestment.py tests/test_entry_alignment_backtest.py tests/test_roic_direction.py -q
```

The stock run must finish before the portfolio runs: the portfolio reads its
frozen entry-rule selections. Generated outputs go to ignored
`data/sharadar/reports/`. Preserve a new experiment in a **new versioned directory**;
do not overwrite this snapshot when trying different hypotheses.

## Controls and limitations to retain

- 67,070 mature observations drawn from the saved monthly point-in-time replay;
  only screened Technology, Consumer Cyclical and Communication Services names.
- Development entries 1998–2008; validation 2012–2016; test 2020–August 2023.
  Three-year gaps keep earlier outcomes out of the next entry period.
- August 7, 2026 is the price-data cutoff; August 7, 2023 is the maturity cutoff.
  These are explicitly coded and must be reviewed together when extending data.
- Twelve economic hypotheses were predefined. Entry-rule selection uses only
  development/validation results. The dataset was explored previously, so the
  later period is not a pristine external holdout.
- Stock statistics allow one entry per issuer per 1,095 days. Portfolio simulations
  use ten 10% entry slots, monthly signals, a fixed ranking, a 30-day issuer
  cooldown after exits, zero cash interest, and no leverage or taxes.
- Adjusted daily closes and 15 basis points per side; target/stop signals execute
  at the following available close, not the signal close. A touch may not be captured.
- Terminal outcomes inherit bankruptcy-zero or last-close treatment from the
  source replay. Last-close treatment for uncertain delistings is a limitation.
- Monthly portfolio windows overlap. Wilson intervals for stock outcomes do not
  fully account for correlated issuers/market cycles.
- Target touches, final returns, executable profit-taking, worst loss from entry,
  and peak-to-trough drawdown are different measurements; keep labels explicit.
- Dynamic FCF valuation is still research-only elsewhere in the repository.
  This experiment did not finish its live integration or change the valuation engine.

## Sensible next research steps

1. Independently audit the execution and delisting handling against a second
   implementation before using results for real allocation decisions.
2. Broaden the point-in-time universe and test industry-specific, growth-aware
   valuation. Keep new hypotheses separate from the original experiment.
3. Add rolling walk-forward evaluation with purged three-year outcome windows and
   issuer/time-block uncertainty estimates; avoid optimizing to the observed test.
4. Freeze a candidate and paper-trade prospectively. Do not promote a 75% doubling
   claim unless sufficiently broad, independent evidence actually supports it.

No production configuration, live recommendations, or deployment was changed by
this research. The separate Quality-50 deployment predates this branch.
