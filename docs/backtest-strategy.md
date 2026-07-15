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
- A **positive Buy − Avoid alpha spread**.
- Buy **hit-rate > 50%** and clearly above Avoid's.
- It **holds across many cohorts**, not one window.

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

**Weight tuning:** a quality/growth tilt improves out-of-sample alpha at all hold
windows. The *unprotected* tuner also cut `risk` 14→6.2 and `survival` 18→14.6 —
rejected as single-regime overfit (all history is one recovery/bull market). We
adopt the **downside-protected** version instead (see Decision log).

---

## Decision log

Newest first. Record every weight change: date, what changed, the evidence, and
what would reverse it.

### 2026-07-15 — Adopt a downside-protected quality/growth tilt
- **Change:** default category weights re-tuned toward quality + growth, with
  `risk`/`survival` **pinned within ±3 of their defaults** (guardrail).
- **Evidence:** regime-robust tuner (`--holds 20,30,60 --folds 4 --reg 0.4
  --protect risk,survival`) — tuned beat default on the held-out fold at every
  hold window and did not weaken the worst-case regime.
- **Why protected, not the raw tuner output:** the raw tuner cut risk/survival
  hard, which shines in a bull tape but is untested in a drawdown. Our data
  (2023–26) is one macro regime, so we keep downside weights near default.
- **Adopted weights:** _(filled in when applied — see `config/scoring.yaml`)_
- **What would reverse this:** the weekly backtest showing the tilt stops
  beating default out-of-sample, or a drawdown period entering the history that
  favors the defensive categories.

_(Prior to this, weights were the original hand-set defaults.)_

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

- **One macro regime.** All seeded history (2023–26) is a recovery/bull market.
  Blocked cross-validation guards against *sampling* overfit, not *macro-regime*
  overfit. **Revisit** the defensive weights once a real drawdown is in the data.
- **Seeded fundamentals are frozen at today's values.** The seeded backtest is
  valuation/momentum-accurate but can't catch fundamentals decaying before price.
  As the *live* `metrics_history` deepens, cross-check with a live-history run
  (`scripts/backtest.py --rolling` on `data/fairentry.db`), which has no such
  limitation.
- **Deterministic gate only.** The backtest validates the numbers-based Buy
  filter, not the LLM thesis nudge.
- **Alpha is vs the universe average, not a formal index**, and prices exclude
  dividends.

Treat every result as **strong evidence, not proof**, and lean on the live
paper-portfolio track record (`fairentry/tracking/`) as it accrues true
out-of-sample results.
