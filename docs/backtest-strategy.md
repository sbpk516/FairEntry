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

α = a name's forward return minus its cohort's cross-sectional mean (stock
selection, market direction removed).

---

## Current status

_Last reviewed: 2026-07-15 (seeded backtest, 148 names, 2023-07 → 2026-07)._

**Rolling backtest — Buy filter:** ✅ validated
```
Buy   n=2059  +4.73% α  58.6% hit   ·  Buy − Avoid spread +5.82%  ·  monotonic ✓
Watch          -0.37% α  43.6% hit
Avoid          -1.09% α  42.1% hit
```
The Buy filter reliably beats the average stock across 152 cohorts and three
years. Buys are up ~59% of the time vs ~42% for Avoids.

**Weight tuning:** **no change adopted — current defaults kept.** An *unprotected*
tuner found a quality/growth tilt that beat default out-of-sample (+2.25%), but it
did so by cutting `risk` 14→6.2 and `survival` 18→14.6. When we re-ran with the
downside guardrail (`risk`/`survival` pinned near default), the edge **vanished** —
the tuned vector was marginally *worse* than default at every hold window
(verdict: KEEP DEFAULT). Conclusion: the apparent gain was **taking more risk in a
bull market**, not stock-selection skill. See Decision log.

---

## Decision log

Newest first. Record every weight change: date, what changed, the evidence, and
what would reverse it.

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

**Residual — the honest ceiling:**
- **Survivorship bias (biggest residual).** The seeded universe is *today's*
  Finviz survivors — names that delisted or went to zero are absent, so **absolute
  α is optimistic**, worst for the Avoid tail. No free point-in-time-universe
  source exists in the stack to add delisted names. **The real fix is time:** the
  *live* `metrics_history` keeps names after they leave the universe, so a
  live-history run (`scripts/backtest.py --rolling` on `data/fairentry.db`) is
  survivorship-clean **going forward**. Cross-check against it as history deepens.
- **One macro regime.** All history (2023–26) is a recovery/bull market. Blocked
  CV guards *sampling* overfit, not *macro-regime* overfit. **Revisit** the
  defensive weights once a real drawdown is in the data.
- **Fundamentals still partial even with `--sec-history`.** Analyst targets and
  recommendations, short float, beta, news, and some insider/institutional
  signals remain current, omitted, or approximate; without `--sec-history` the
  seed is valuation/momentum-accurate only. The live-history run has no such
  limitation.
- **Deterministic gate only** (no LLM thesis nudge); **α is vs the universe
  average, not a formal index**; prices exclude dividends and trading costs.

Treat every result as **strong evidence, not proof**, and lean on the live
paper-portfolio track record (`fairentry/tracking/`) as it accrues true
out-of-sample results.
