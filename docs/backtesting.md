# FairEntry — How the Backtest Works (in plain English)

This explains, with small worked examples, how we check whether the model's
**Buy / Watch / Avoid** calls are any good. No finance background needed.

---

## 1. The one question a backtest answers

> When the model says **Buy**, do those stocks actually go up more than the ones
> it says **Watch** or **Avoid**?

If yes, the filter has real skill and we can trust it. If Buy names do *no
better* than Avoid names, the model is just guessing — and we'd want to know
that before acting on it.

That's it. Everything below is machinery to answer that question **honestly**
(without fooling ourselves with luck or a rising market).

---

## 2. The raw material: point-in-time history

Every time the pipeline runs, it saves a **timestamped snapshot** of every
number for every stock into a table called `metrics_history`. Think of it as a
photo album — one photo of the whole market per run.

A few snapshots for one stock might look like:

| date       | ticker | price | fwd_pe | target_price |
|------------|--------|------:|-------:|-------------:|
| 2026-06-01 | ACME   |  50.00|   9.0  |   72         |
| 2026-06-08 | ACME   |  52.50|   9.4  |   72         |
| 2026-07-01 | ACME   |  57.00|  10.3  |   72         |

Because we kept the photo from **June 1**, we can later ask: *"Given only what
we knew on June 1, what did the model say — and what happened next?"* That
"given only what we knew then" part is what makes it a fair test (no cheating
with future knowledge).

---

## 3. Example 1 — the simple backtest (one start, one finish)

This is `harness.run` (`scripts/backtest.py` with no flags).

**Step 1 — score each stock as of the start date.** Rewind to June 1, feed the
model *only* the June 1 snapshot, and record its verdict.

**Step 2 — measure what happened.** Compare the price on the start date to the
price on the finish date.

Take one stock:

```
ACME on 2026-06-01:  model says BUY,  price = $50
ACME on 2026-07-01:  price = $57
Forward return = 57 / 50 − 1 = +14%
```

**Step 3 — sort every stock into a bucket by its verdict and average.**

| verdict | # stocks | avg forward return | hit-rate (% that rose) |
|---------|---------:|-------------------:|-----------------------:|
| Buy     |       38 |            +6.2%   |                  71%   |
| Watch   |      210 |            +1.1%   |                  52%   |
| Avoid   |      180 |            −3.4%   |                  38%   |

**How to read it:** Buy beat Watch beat Avoid, and more Buys rose than fell.
That's the shape you want. If the Buy row looked like the Avoid row, the filter
isn't working.

This simple version is a good sanity check, but it has three weaknesses that the
next examples fix:
1. It's **one window** — a single roll of the dice (maybe we got lucky).
2. It uses **raw returns** — a rising market flatters *everything*.
3. It can suffer **survivorship bias** — a stock that collapsed and left the
   universe quietly disappears.

---

## 4. Example 2 — why "raw return" can lie (→ use *alpha*)

Suppose we backtest a month where **the whole market went up ~15%**.

| verdict | avg **raw** return |
|---------|-------------------:|
| Buy     |            +18%    |
| Avoid   |            +12%    |

At a glance: "Buy made +18%, great!" But look again — **Avoid also made +12%**.
Everything went up because the *market* went up. The model barely helped.

The fix: subtract the **average stock's** return (our benchmark) so we measure
*selection skill*, not the tide. This benchmark-relative number is called
**alpha**.

| verdict | raw return | − market (+15%) | = **alpha** |
|---------|-----------:|----------------:|------------:|
| Buy     |     +18%   |         −15%    |   **+3%**   |
| Avoid   |     +12%   |         −15%    |   **−3%**   |

Now the truth shows: Buys beat the average stock by 3 points, Avoids *lagged* it
by 3. **Alpha strips out the market so we only see whether we picked better than
a dart throw.** Our harness uses each cohort's own cross-sectional mean (the
average of every stock scored that day) as the benchmark — so no extra data is
needed.

---

## 5. Example 3 — one window is a coin flip (→ *rolling* cohorts)

One start date is one sample. To be confident, we repeat the test over **many
overlapping start dates** — a new "cohort" every week (`--step 7`), each held for
a fixed window (`--hold 30`) — then average the results.

| cohort start | Buy alpha |
|--------------|----------:|
| 2026-04-06   |   +9.8%   |
| 2026-04-13   |   +4.1%   |
| 2026-04-20   |   −1.2%   |
| …            |     …     |
| **average**  | **+4.2%** |

One cohort can be lucky or unlucky; the **average across dozens** of them is what
we trust. This is `harness.run_rolling` (`scripts/backtest.py --rolling`).

---

## 6. Putting it together — a full worked cohort

Here is one cohort of six stocks, from raw prices all the way to the verdict
ladder. (Small and made-up, so you can follow every number.)

**Start prices, finish prices, and the model's verdict at the start:**

| ticker | verdict | price start | price finish | raw return |
|--------|---------|------------:|-------------:|-----------:|
| ACME   | Buy     |        50   |         57   |   +14.0%   |
| BOLT   | Buy     |        80   |         88   |   +10.0%   |
| CINDER | Watch   |        40   |         42   |    +5.0%   |
| DELTA  | Watch   |       100   |        101   |    +1.0%   |
| ECHO   | Avoid   |        30   |         28   |    −6.7%   |
| FOX    | Avoid   |        60   |         54   |   −10.0%   |

**Step 1 — the benchmark (average stock this cohort):**
```
(14 + 10 + 5 + 1 − 6.7 − 10) / 6  =  +2.22%
```

**Step 2 — alpha = each stock's return − 2.22%:**

| ticker | verdict | raw    | alpha    |
|--------|---------|-------:|---------:|
| ACME   | Buy     | +14.0% | +11.78%  |
| BOLT   | Buy     | +10.0% |  +7.78%  |
| CINDER | Watch   |  +5.0% |  +2.78%  |
| DELTA  | Watch   |  +1.0% |  −1.22%  |
| ECHO   | Avoid   |  −6.7% |  −8.92%  |
| FOX    | Avoid   | −10.0% | −12.22%  |

**Step 3 — average alpha per bucket:**

| verdict | mean alpha | hit-rate (alpha > 0) |
|---------|-----------:|---------------------:|
| Buy     | **+9.78%** |                 100% |
| Watch   | **+0.78%** |                  50% |
| Avoid   | **−10.57%**|                   0% |

**The two headline numbers:**
- **Monotonic?** Buy (+9.78) ≥ Watch (+0.78) ≥ Avoid (−10.57) → **Yes.** The
  ladder goes the right direction.
- **Buy − Avoid spread** = 9.78 − (−10.57) = **+20.35%.** Bigger = the filter
  separates winners from losers more sharply.

The real tool does exactly this, then **averages across all cohorts**. A healthy
result is *monotonic with a positive spread*. A broken or meaningless filter
looks flat (Buy ≈ Watch ≈ Avoid) or upside-down (Avoid on top).

---

## 7. Reading the actual report

```
Rolling backtest 2023-01-02 -> 2024-02-19 · 28 cohorts · hold 30d/step 14d
verdict     n    mean α  median α  hit-rate   raw ret
Buy       336    +5.31%    +5.31%    100.0%    +6.15%
Watch     224    +2.07%    +2.07%    100.0%    +2.90%
Avoid     336    -6.69%    -6.69%      0.0%    -5.86%

Buy-Avoid alpha spread: +12.00%   monotonic: True
```

- **n** — how many (stock, cohort) observations landed in each bucket. Bigger =
  more reliable.
- **mean α / median α** — average and middle alpha. If median ≪ mean, a few
  outliers are doing the work.
- **hit-rate** — share with positive alpha (beat the average stock). Consistency.
- **raw ret** — the plain return, for reference.
- **spread / monotonic** — the verdict on the filter itself.

---

## 8. Two ways to get the history

**A. Let it accumulate (live).** The twice-daily pipeline saves a snapshot every
run, and CI now persists the database between runs, so `metrics_history` fills up
on its own. Fully accurate — but you must **wait weeks** for enough history.

```bash
python scripts/backtest.py --rolling      # once history is deep enough
```

**B. Seed it from real prices (backtest today).** `scripts/seed_backtest.py`
rewinds the tape using **real weekly prices from Yahoo Finance**. For each past
week it reconstructs what the model would have seen and writes it to a *separate*
`data/backtest.db`.

```bash
python scripts/build_all.py --refresh        # populate today's data first
python scripts/seed_backtest.py --limit 150  # ~3 years of history from real prices
python scripts/backtest.py --db data/backtest.db --rolling --hold 30 --step 7
```

For a more practical free historical test, add SEC filing fundamentals:

```bash
python scripts/seed_backtest.py --sec-history --limit 150
python scripts/backtest.py --db data/backtest.db --rolling --hold 30 --step 7
```

**What the seeder reconstructs accurately** (from the real price path):
- the actual **price** each week,
- **valuation ratios** (P/E, P/S, P/B, P/FCF) — they move with price, so we scale
  today's ratio by `price_then / price_now`,
- **momentum/trend** (1-year performance, distance from moving averages) —
  computed directly from the price series.

**What `--sec-history` reconstructs from SEC filing dates**:
- gross/operating/profit margins,
- current ratio, debt/equity, ROE, ROIC proxy,
- revenue growth between filed periods,
- share-count dilution,
- market cap, P/S, P/B, and P/FCF where shares/cash-flow facts are available,
- Altman-Z proxy using the facts filed by that date.

**What it still holds constant or omits** because SEC does not provide it:
analyst target, analyst recommendation, short float, beta, insider score,
13F score, estimate revisions, and news sentiment.

---

## 9. Honest limitations (so you don't over-trust it)

- **Without `--sec-history`, seeded fundamentals are frozen at today's values.** So the seeded backtest
  tests **entry/valuation/timing** well, but won't catch a company whose
  *fundamentals* rotted *before* its price fell. (The live-accumulated history
  has no such issue — it's the price of not waiting.)
- **With `--sec-history`, core fundamentals improve, but coverage is uneven.**
  SEC facts are filing-based, concept names vary, and foreign issuers/ADRs can
  be less complete. Missing fields are omitted rather than guessed.
- **Survivorship bias.** A name that cratered and dropped out of the universe can
  silently vanish from a window, flattering the averages.
- **Sector medians are reconstructed as-of each entry cohort.** This avoids the
  earlier current-snapshot look-ahead in relative scoring rules.
- **Deterministic gate only.** The backtest scores on the numbers; it does not
  apply the LLM thesis nudge, so it validates the *numbers-based* Buy filter.
- **Prices only.** No dividends; alpha is vs. the universe average, not a formal
  index. Configured entry slippage and transaction costs are applied to both
  headline forward returns and target-evidence entry prices.

Target hits are censored at each method's contractual expiry. The reader may
load the first weekly close after that boundary to prove expiration, but a
threshold crossed only after expiry is recorded as expired, not reached. Only
cohorts meeting the minimum-population rule contribute to headline performance
or target-calibration denominators.

The versioned strategy contract is executable rather than aspirational. Values
outside the implemented entry (`snapshot_close`), target-hit (`close`) and
benchmark (`cohort_mean`) capabilities are rejected. Configured target models
filter the disclosed/evaluated target set, and data-quality modes control which
source classes may enter screening and scoring.

Each candidate is replayed with the same primary strategy and preset resolution
as the live board (Deep Value wins a dual-screener tie). The report publishes
separate Deep Value and Quality Growth results so later preset changes cannot
silently diverge from the backtest.

Pass `--prospective-db data/fairentry.db` to attach the live `signal_events`
ledger as a separate survivorship-clean comparison. The weekly workflow does
this automatically; it never mixes those prospective observations with the
seeded universe.

None of these are fatal — they're the normal caveats of a lightweight backtest.
Read the result as **strong evidence**, not gospel, and lean on the live
paper-portfolio track record (`fairentry/tracking/`) as it accrues real
out-of-sample results over time.

---

## 10. Cheat sheet

| I want to…                              | Command |
|-----------------------------------------|---------|
| Quick sanity check on live history      | `python scripts/backtest.py` |
| Rigorous rolling/alpha on live history  | `python scripts/backtest.py --rolling` |
| Backtest **now** from real prices       | `python scripts/seed_backtest.py` then `python scripts/backtest.py --db data/backtest.db --rolling` |
| Change holding window / cadence         | `... --rolling --hold 60 --step 7` |
| See the full JSON                       | `... --rolling --json` |

**Green light to trust the filter:** the ladder is **monotonic** (Buy ≥ Watch ≥
Avoid) with a **positive Buy − Avoid spread**, and it holds up across **many
cohorts**, not just one.
# Evidence implementation

The versioned target/outcome design and its trust boundaries are documented in
[`BACKTEST_EVIDENCE_IMPLEMENTATION.md`](BACKTEST_EVIDENCE_IMPLEMENTATION.md).

## SFA point-in-time replay

The preferred historical backtest is now the private SFA warehouse replay. It
includes active and delisted securities, as-reported filing dimensions, daily
corporate-action-aware prices and SPY total-return benchmarking. See
[`SFA_BACKTEST.md`](SFA_BACKTEST.md) for storage, commands, public-data
boundaries and residual limitations. The seeded Yahoo/SEC runner remains as a
legacy comparison and free fallback.

The SFA contract additionally requires live-universe parity, observed-only
coverage, identical stock/SPY execution dates, round-trip costs, terminal-event
censoring, multi-horizon evidence through approximately 18 months, and separate
chronological tuning for Deep Value and Quality Growth. Run the complete local
validation with `./scripts/run_sfa_validation.ps1`.

## Controlled LLM research cycle

Failure explanation and predictive-rule testing are deliberately separate:

- `python scripts/failure_research.py queue --backtest web/data/backtest-sfa.json`
  creates the retrospective diagnosis queue. Later evidence may explain a miss,
  but every finding remains non-scoring.
- `python scripts/research_cycle.py --backtest data/sharadar/reports/backtest-sfa-full.json`
  tests entry-date-only rules from `config/predictive_rules.json` on chronological
  development, validation and final-test periods.

The deterministic runner rejects outcome fields, reports +20%, +25%, +28%, +30% and +35%
attainment through three years, checks sample size and drawdown, and never edits
production scores or weights. An eligible rule still requires explicit human
promotion. Every normal SFA replay now regenerates both research contracts.

## Factor explorer

Run `python scripts/factor_explorer.py --public-attach web/data/backtest-sfa.json`
to compare completed +30%-within-one-year successes and failures using only
information available on each original Buy date. The first factor families are
revenue-growth deceleration, operating-margin direction, free-cash-flow margin,
P/E relative to revenue growth, relative strength and trend regime. Licensed
fundamental amounts remain private; the UI receives only derived aggregate
distributions and walk-forward results.

The explorer uses rolling chronological folds. Thresholds are learned from all
older folds, must reproduce on a second older validation slice, and are then
evaluated on the next unseen fold. It tests revenue and earnings consistency,
growth stability, margins, cash flow, several valuation measures, dilution,
debt direction, market and sector conditions, and entry timing. Point-in-time
Business Quality and Financial Strength research now also covers gross
profitability, cash conversion, accrual quality, ROIC, net debt to free cash
flow and interest coverage. Pre-declared
economic combinations are tested instead of brute-forcing every possible pair.

Historical analyst-estimate revisions and next-year EPS estimates are explicitly
reported as unavailable because the licensed replay does not contain complete
point-in-time series for them. They remain non-quantifiable information with no
score effect. The explorer's changing thresholds are research hypotheses only:
it cannot edit production scoring, weights, verdicts or configuration. A stable
result may nominate a separate, frozen rule for the controlled validation cycle,
but promotion remains explicit and manual.

## Earliest-entry research

The SFA evidence report starts each Buy episode on the earliest date the
unchanged point-in-time model actually said Buy. It compares conservative
valuation upside of at least 30%, 45%, 50% and 60%, first by itself and then
with complete stable-value/no-thesis-deterioration evidence. The main outcome
remains +30% within one year; +50% within two years and +100% within three years
are separate longer-horizon measurements. These comparisons are research-only
and add no score or verdict rule.

## Movement-capacity research

Every earliest SFA Buy can also be grouped by its trailing 52-week high/low
range as known on that date. The research requires at least 200 prior sessions,
uses no post-Buy price in the factor, and compares exclusive range bands plus
direct 20% and 30% cutoffs. It reports +30% within one year, +30% within two
years and +50% within two years, together with drawdown and candidate count.

The report keeps cumulative return, annualized volatility and high/low range
as different measurements. It also shows combinations of constructive trend
and controlled downside volatility so a large, downside-heavy range is not
mistaken for favorable movement capacity. All fields are transparent,
backtestable and information-only. No low-range stock is removed and no score
changes unless a separately frozen out-of-time confirmation is later approved.

## Relative momentum and experiment-count control

The frozen relative-momentum study tests whether Buy episodes with improving
six-month stock-minus-sector performance reach +30% within one year more often
than Buy episodes with deteriorating relative performance. The 126-session
lookback, 21-session direction comparison, zero-percent relative boundary and
sector-ETF benchmark are registered before evaluation.

The experiment registry makes multiple testing visible. Every future
definition must receive a new ID rather than silently replacing the first
rule, and the report publishes the number and IDs of all definitions tested.
This reduces the risk of selecting a lucky result from many hidden
combinations.

The historical result remains research-only even when favorable. Production
promotion requires enough completed cases and issuers, consistent validation
and newest-period results, no material drawdown deterioration, and genuinely
new future shadow confirmation. Missing price history is unavailable rather
than favorable, and all measurements stop before the first Buy date.

## Point-in-time ticker identity

The SFA replay uses the `strict_point_in_time` ticker-identity policy. Sharadar
can place a company's entire price history under its later permanent ticker, so
the replay checks ACTIONS metadata before creating the historical universe. A
displayed ticker is eligible only on or after the latest recorded change into
that exact ticker.

This identity check runs before issuer selection, screening, scoring and target
measurement. Corporate-action metadata is used only to identify the correct
historical security; it is never used as a predictive factor. A strict run
fails closed when ACTIONS is unavailable, and publication is rejected unless
the artifact reports zero remaining invalid ticker observations.

## Substantial-dilution hard veto

FairEntry forces Avoid when the point-in-time year-over-year share-count change
is strictly greater than 10%. Exactly 10% passes this boundary; missing data is
reported as unknown and does not trigger the veto. The observed percentage and
the 10% threshold are stored with every triggered veto. The veto changes no
numerical score.

The threshold study compares 5%, 10%, 15% and 20% using chronological 60%
development, 20% validation and 20% final-unseen periods. On the corrected
ticker-identity sample, the 10% rule improved completed +30%-within-one-year
attainment from 46.0% to 47.6% over full history and from 51.3% to 52.1% in the
newest unseen period. This is a modest historical improvement. Every private
replay is independently scanned before publication; publication fails if a
Buy with measured dilution above 10% remains.
