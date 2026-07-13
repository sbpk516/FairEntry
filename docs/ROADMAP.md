# FairEntry — Roadmap (remaining work)

Status as of 2026-07-13. The core platform (all 7 build phases, SEC + Form-4
enrichers, DeepSeek reasoning, growth entry-zone view) is implemented and live on
real data. This roadmap tracks the remaining requirement gaps, grouped by impact.

Legend: **effort** Low / Med · **impact** on verdict quality.

---

> **Progress:** Phase A ✅ done · Phase B ✅ done · Phase C ✅ done. All roadmap phases complete.

## Phase A — Decision quality (makes Buy / Watch / Avoid more correct)  ✅ DONE
*Highest-value work. Start here.*

| # | Item | Why it matters | Effort | Reuse / notes |
|---|---|---|---|---|
| A1 (#2) | **Multi-method fair value** — DCF-lite, Lynch/GARP, peer-relative, asset/book | Valuation is currently single-method (analyst target), the weakest link. Sharpens the Valuation category, `intrinsic_gap`, entry zones, and margin-of-safety together. **Biggest lever.** | Med | Port v1 `intrinsic_overlay.py` + Lynch fair A/B from `flow_overlay.py` |
| A2 (#9) | **`share_count_yoy` dilution** | Feeds survival + risk; quick win. | Low | Compute from SEC XBRL shares YoY (already in the forensic fetch) |
| A3 (#3a) | **Taxonomy items using existing data** — margin expansion, peer-relative valuation | More discriminating category scores. Peer-relative unlocks after A1. | Med | config edits + engine |

## Phase B — Make it a usable daily tool  ✅ DONE

| # | Item | Why | Effort |
|---|---|---|---|
| B1 (#7) | **Editable-weights persistence + saved presets** | Tuning survives reloads; save Conservative/Aggressive presets. | Low (front-end) |
| B2 (#6) | **Wire recommendation tracking + paper-portfolio + degradation alerts** | Follow verdicts over time; flag when a held name's score drops. Module exists, needs wiring. | Low-Med |
| B3 (#5) | **Backtest harness** | Validate/tune weights with evidence. Build now; value accrues as `metrics_history` grows. | Med |

## Phase C — Data breadth & research  ✅ DONE

| # | Item | Why | Status |
|---|---|---|---|
| C1 (#8a) | **Finnhub news → reasoning** | The LLM now reads real recent headlines (60-day, shortlist-only, cached) instead of metrics alone. Stance stays with the LLM; the adapter only tags catalyst *category*, never polarity (avoids the v1 keyword bug). | ✅ `3581848` |
| C2 (#8b) | **13F smart-money adapter** | Replaces the shallow Finviz `inst_trans` proxy with aggregated 13F-HR holdings of a curated manager set (config/managers.yaml). Cost-flat in the universe; high-precision "held by N funds; 2 new, 1 added". | ✅ `1eec2e0` |
| C3 (#1) | **Watchlist intelligence** (§7B) | Up to 5 real sources to follow per name (analyst/filing/data/publication/community), folded into the cached thesis call (no extra cost). Anti-hallucination clause. | ✅ `fc1eaea` |
| C4 (#3b/#4) | **Estimate-revisions item + §9 labels** | Estimate-revision catalysts item computed from analyst-target history; horizon / "Expanding" / "N sources to follow" labels. | ✅ `6d3da34` |

### Data-blocked items — resolved
- **Estimate-revisions** → computed directly from `metrics_history` (daily target snapshots already captured); no separate `analyst_revisions.py` port needed. Activates as history deepens.
- **Customer / expansion** labels → derived from LLM catalyst cues + news categories (enabled by C1).

---

## Sequencing recommendation
Phase A → B → C. Within A: **A1 (multi-method fair value) first** — it's the single biggest quality gain and unblocks peer-relative valuation (A3) and better entry zones. Everything here is additive (config, a new adapter, or a UI view); no core rework.

## Also needs the user (not code)
- Add GitHub Actions secrets (`FINVIZ_API_KEY`, `FINNHUB_API_KEY`, `DEEPSEEK_API_KEY`, `SEC_CONTACT_EMAIL`) + enable Pages → activates the hosted twice-daily app at `https://sbpk516.github.io/FairEntry/`.
- First CI run: the full-universe SEC forensic pass is slow (going-concern text fetch) — bound/parallelize the enrich cap in the workflow when running it.
