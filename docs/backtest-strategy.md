# FairEntry — Backtest Strategy & Review Log (living document)

A running record for **continuously reviewing** whether the Buy filter still
works, and a **decision log** for any change to the scoring weights. Update the
"Current status" and "Decision log" sections whenever a backtest is reviewed or a
change is made.

- **How the backtest works (mechanics + examples):** see [`backtesting.md`](./backtesting.md).
- **The live scoring model:** see [`methodology.md`](./methodology.md) (generated from `config/scoring.yaml`).

---

## How we run it

| | |
|---|---|
| **Automated** | `.github/workflows/backtest.yml` — weekly (Sun 06:00 UTC), on demand, and whenever backtest code changes. Seeds real Yahoo prices → runs the rolling backtest → runs the regime-robust weight tuner → posts both tables to the run summary and uploads the data. Read-only (never deploys). |
| **Local — validate the filter** | `python scripts/seed_backtest.py` then `python scripts/backtest.py --db data/backtest.db --rolling` |
| **Local — tune weights (recommend only)** | `python scripts/tune_weights.py --db data/backtest.db --holds 20,30,60 --folds 4 --reg 0.4 --protect risk,survival` |
| **Local — SFA point-in-time replay** | `python scripts/sfa_backtest.py --step 30 --hold 30` (private licensed warehouse; preferred historical evidence) |

The tuner **never edits config** — it recommends. Weight changes are applied by
hand and recorded in the Decision log below.

---

## What "healthy" looks like (acceptance criteria)

**The Buy filter is validated when the rolling backtest shows:**
- **Monotonic** ladder: Buy α ≥ Watch α ≥ Avoid α.
- A **positive Buy − Avoid alpha spread** whose **block-bootstrap 90% CI excludes
  zero** (`significant: true`) — the CI resamples whole cohorts, so it reflects
  the real independent sample despite heavy cohort overlap.
- Buy **hit-rate > 50%** and clearly above Avoid's.
- It **holds across many cohorts**, not one window.

The backtest scores only **screener-passing names as-of** (matching the live
board), skips a **warmup** so momentum/trend metrics exist, and reports the spread
with its CI. Absolute α is still **optimistic** (see survivorship below) — trust
the *relative* ladder and the CI, not the absolute magnitude.

**A weight change is worth adopting only when the regime-robust tuner shows:**
- It **wins the final held-out fold at every hold window** (20/30/60d), loses none materially.
- It is **no worse on the worst-case (fold × hold) slice** than the current weights.
- The **defensive categories (`risk`, `survival`) stay near default** (guardrailed) — we do not cut downside protection on data that covers only one macro regime.

α = a name's forward return minus the run's declared benchmark. The preferred
SFA replay uses SPY total return; the legacy seeded replay uses its cohort's
cross-sectional mean.

---

## Current status

### 2026-08-20 — Sharadar SFA point-in-time baseline

The first full SFA replay used snapshot `20260810T132048Z`, 333 monthly entry
cohorts from 1998-10-27 through 2026-07-29, historical active **and delisted**
securities, as-reported fundamentals available on each decision date, next-close
entries, configured costs, and SPY total return as the benchmark. It covered
2,916 unique securities after screening and factor-coverage controls.

```text
Buy    n=1,116   +0.01% mean alpha   47.7% beat-benchmark rate
Watch  n=48,336  -0.29% mean alpha   47.3% beat-benchmark rate
Avoid  n=24,391  -0.44% mean alpha   46.6% beat-benchmark rate
Buy - Avoid spread +0.45% · 90% cohort-block CI [-0.33%, +1.30%]
Monotonic ladder: yes
```

**Conclusion:** the current Backtest Recommended weights and Buy filter are
**not fully validated by the SFA baseline**. The ordering now points in the
right direction, but the likely Buy-minus-Avoid range still crosses zero and
fewer than half of Buy observations beat SPY over 30 days. The full replay also
shows 278 of 644 completed Buy episodes (43.2%) reached a net +30% within one
year. The weights remain unchanged; the chronological challenger failed the
complete promotion checks.

The SFA result is the preferred historical reliability check. The older result
below remains useful for implementation comparison, but not as the primary
claim about expected performance.

_Last reviewed: 2026-08-05 using the successful 2026-08-02 scheduled run
(161 cohorts, 2022-08 through 2026-07)._

**Rolling backtest — Buy filter:** useful Buy separation; lower ladder still imperfect
```
Buy   n=931  +3.54% alpha  57.4% hit
Watch        -0.34% alpha  44.0% hit
Avoid        +0.67% alpha  46.7% hit
Buy - Avoid spread +2.87%  ·  90% CI [+1.87%, +3.99%]  ·  monotonic: no
```
The Buy bucket separates positively with a confidence interval above zero, but
Avoid still beats Watch on mean alpha. Treat the model as a useful Buy-candidate
filter, not a perfectly ordered three-bucket ranking.

**Weight tuning:** **protected recommendation adopted as the new default.** The
2026-08-02 tuner improved the final held-out Buy-Avoid spread at 20/30/60 days
(7.28/11.13/19.08 to 8.27/13.69/24.79), won all three windows, and improved the
worst selection-fold result from +0.10% to +0.57%. Risk and survival remained
inside their explicit ±3-point guardrails.

---

## Decision log

Newest first. Record every weight change: date, what changed, the evidence, and
what would reverse it.

### 2026-08-05 — Adopted protected tuner recommendation
- **Decision:** make the 2026-08-02 protected tuner vector the base default and
  the automatic preset for both Deep Value and Quality Growth.
- **Weights:** quality 16.99, survival 17.07, growth 11.20, valuation 16.99,
  confirmation 14.21, catalysts 10.45, risk 13.09.
- **Evidence:** the tuned vector won every held-out 20/30/60-day window and
  improved the worst selection-fold spread while respecting downside guards.
- **Limitation:** this validates short holding windows, not the desired 1–2 year
  horizon. Reassess after adding 6/12/24-month profile-level results.
- **What would reverse it:** repeated loss to the former default, a confidence
  interval crossing zero, or materially worse drawdown/live signal outcomes.

### 2026-07-15 — Reviewed weight tuning → **kept defaults** (no change)
- **Decision:** no change to `config/scoring.yaml`. The scoring weights remain the
  original hand-set defaults.
- **What we tested:** the regime-robust tuner, first unprotected, then with a
  downside guardrail (`--holds 20,30,60 --folds 4 --reg 0.4 --protect
  risk,survival --protect-band 3`).
- **Finding:** unprotected, the tuner's tilt beat default out-of-sample by
  ~+2.25% — but only by cutting `risk` (14→6.2) and `survival` (18→14.6). With
  those categories protected, the tuned vector was *marginally worse* than
  default at all three hold windows (h20 −0.21, h30 −0.29, h60 −0.23), so the
  tuner returned **KEEP DEFAULT (no gain)**.
- **Interpretation:** the apparent edge was **downside risk-taking in a bull
  market**, not selection skill. Adopting it would have quietly reduced the
  model's safety margin on the strength of a single macro regime. Guardrail did
  its job.
- **What would change this:** a repeated ADOPT from the *protected* tuner across
  several weeks, or a real **drawdown** entering the seeded history — at which
  point re-review the defensive weights *without* the guardrail, since that's the
  regime we've been protecting against.

_(Weights unchanged since project start.)_

---

## Review checklist (run each review — weekly-ish)

1. **Open the latest `backtest.yml` run summary.** Is the ladder still monotonic
   with a positive Buy − Avoid spread? Is Buy hit-rate > 50%?
2. **Coverage:** did it seed a healthy number of names (≥ ~120)? A big drop means
   Yahoo throttled — re-run.
3. **Tuner verdict:** did the protected tuner say ADOPT, KEEP DEFAULT, or overfit?
   Only act on a repeated ADOPT across several weeks — not a single run.
4. **Regime watch:** has the history started to include a market **drawdown**? If
   so, re-review the `risk`/`survival` weights *without* the guardrail — that's
   the missing regime we've been protecting against.
5. **Drift:** if the live board's verdicts look off vs the backtest, check that
   `config/scoring.yaml` weights match what the tuner last endorsed.

**Red flags (investigate before trusting a result):**
- Ladder goes flat (Buy ≈ Avoid) or inverts → the filter degraded.
- Huge train↔test or fold-to-fold swings → regime effect; don't tune on it.
- Tuner wants to cut a defensive category hard → guardrail is doing its job; do
  not remove it without drawdown data.
- Seeded count collapses → data problem, not a model problem.
- Multiple share classes for one issuer appear as independent recommendations
  → universe deduplication failed; retain only the primary/liquid class.

---

## Known limitations & when to revisit

**Fixed (2026-07-15 review):**
- ~~Backtest scored the full universe, not the screened board~~ → now **screens
  as-of** and only scores screener-passing names (matches the live board).
- ~~`n` over-counted (overlapping cohorts)~~ → the spread now ships with a
  **block-bootstrap 90% CI** that resamples whole cohorts.
- ~~200-week MA always missing~~ → seeder now pulls **≥208 weeks**; a **warmup**
  skips the early cohorts that lacked momentum history.
- ~~All fundamentals frozen at today's values~~ → `scripts/seed_backtest.py
  --sec-history` reconstructs core filing fundamentals (margins, growth, debt,
  Altman inputs, dilution) from **SEC companyfacts by filing date**, materially
  reducing the frozen-fundamentals look-ahead.
- ~~Execution costs appeared only in target cards~~ → configured entry
  slippage/costs now affect headline returns and alpha as well.
- ~~Rejected small cohorts leaked into target evidence~~ → performance and
  calibration now use the identical accepted-cohort population.
- ~~Post-expiry grace observations could count as hits~~ → target crossings are
  now strictly censored at each target's contractual expiry.
- ~~Several YAML settings were descriptive only~~ → implemented settings are
  operational and unsupported entry/hit/benchmark values fail fast; target
  model selection and quality-source policies now change replay behavior.
- ~~Per-strategy presets were not structurally replayed~~ → candidates now use
  the live primary-strategy tie-break and that strategy's configured preset,
  with separate portfolio summaries.
- ~~Target rows looked like independent trials~~ → calibration now publishes
  unique-stock/cohort counts, cohort-block intervals and per-year results.

**Residual — the honest ceiling:**
- **Legacy replay survivorship bias.** The legacy seeded universe is *today's*
  Finviz survivors — names that delisted or went to zero are absent, so **absolute
  α is optimistic**, worst for the Avoid tail. No free point-in-time-universe
  source exists in the legacy stack to add delisted names. The SFA replay closes
  this gap with a historical active-and-delisted universe. Continue using the
  prospective `metrics_history` ledger as a true out-of-sample cross-check.
- **Legacy replay has one macro regime.** Its 2023–26 history is predominantly a
  recovery/bull market. The SFA replay now spans multiple regimes since 1998 and
  reports regime slices, but strategy changes still require a chronologically
  held-out period rather than tuning on the entire SFA history.
- **Fundamentals still partial even with `--sec-history`.** Analyst targets and
  recommendations, short float, beta, news, and some insider/institutional
  signals remain current, omitted, or approximate; without `--sec-history` the
  seed is valuation/momentum-accurate only. The live-history run has no such
  limitation.
- **Deterministic gate only** (no LLM thesis nudge); **α is vs the universe
  average, not a formal index**; prices exclude dividends. Configured entry
  costs are included, but the model does not simulate liquidity-dependent
  spreads, market impact, taxes, or exit commissions.

Treat every result as **strong evidence, not proof**, and lean on the live
paper-portfolio track record (`fairentry/tracking/`) as it accrues true
out-of-sample results.
