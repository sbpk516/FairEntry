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

**What the seeder reconstructs accurately** (from the real price path):
- the actual **price** each week,
- **valuation ratios** (P/E, P/S, P/B, P/FCF) — they move with price, so we scale
  today's ratio by `price_then / price_now`,
- **momentum/trend** (1-year performance, distance from moving averages) —
  computed directly from the price series.

**What it holds constant at today's value** (we can't get their history for
free): margins, growth rates, ROIC, debt, Altman-Z, analyst target, red flags.

---

## 9. Honest limitations (so you don't over-trust it)

- **Seeded fundamentals are frozen at today's values.** So the seeded backtest
  tests **entry/valuation/timing** well, but won't catch a company whose
  *fundamentals* rotted *before* its price fell. (The live-accumulated history
  has no such issue — it's the price of not waiting.)
- **Survivorship bias.** A name that cratered and dropped out of the universe can
  silently vanish from a window, flattering the averages.
- **Sector medians use the current snapshot** (a small look-ahead). Cheap to
  remove later by computing medians as-of the entry date.
- **Deterministic gate only.** The backtest scores on the numbers; it does not
  apply the LLM thesis nudge, so it validates the *numbers-based* Buy filter.
- **Prices only.** No dividends; alpha is vs. the universe average, not a formal
  index.

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
