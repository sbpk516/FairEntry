# FairEntry — Implementation Plan

Date: 2026-07-12
Status: **Draft for your confirmation.** On approval, this drives an end-to-end build.
Companion to: `baghunter-v2-requirements-revised.md`, `baghunter-v2-scoring-model-revised.md`, and the working UX prototype in `FairEntry/prototype/`.

> **How to read this:** Section 5 is the phased build. Section 9 is the **requirements-traceability matrix** — every requirement from both specs mapped to where it gets implemented, so you can confirm nothing is missed. Section 11 explains how the "auto-mode" build + review + test will actually run.

---

## 1. Locked decisions (from your answers)

| Decision | Choice |
|---|---|
| Name | **FairEntry** (working) |
| Stack | **Python 3.11** back-end + **static HTML/JS** UI (no framework build step) |
| Store | **SQLite** canonical (built each run, **gitignored**, uploaded as CI artifact) + small **JSON exports** committed for the UI |
| Hosting | **GitHub Pages** (Actions deploys) + local server; local & CI data may be separate |
| Sectors (start) | Technology, Communication Services, Consumer Cyclical (Auto/EV) — config-driven |
| Screeners | Two only: **Deep Value** and **Quality Growth Entry** |
| Verdict bands | Buy ≥ 72 · Watch 50–71 · Avoid < 50 (config, tunable) |
| LLM | **DeepSeek** (V4 Flash) at the edges only; provider-abstracted |
| Reuse | Port as much proven v1 logic as possible |
| Scope | Personal tool |
| Runtime | Minimize; refresh-only-what's-due + shortlist-only LLM |

---

## 2. Repository structure

```
FairEntry/
  README.md
  requirements.txt
  .env.example                # documents needed keys; real .env is gitignored
  .gitignore                  # .env, data/*.db, caches
  config/
    catalog.yaml              # THE data catalog — every field to pull (single source)
    sectors.yaml              # sector universe + filters
    scoring.yaml              # categories/items/weights/rules/bands/vetoes/gates + presets
    defaults.yaml             # default user-editable settings (MoS, target, holding period)
  fairentry/
    __init__.py
    config.py                 # load + validate all config (schema-checked)
    store/                    # SQLite schema + read/write API + JSON export
      schema.sql  db.py  export.py
    adapters/                 # one per source; the ONLY place that fetches
      base.py  finviz.py  sec_edgar.py  yfinance_adapter.py  finnhub.py  form4.py  thirteenf.py
    catalog/                  # catalog runner: which fields are due, fetch, normalize, store
      refresh.py  cadence.py  provenance.py  cache.py
    screeners/                # store-only screeners
      registry.py  deep_value.py  quality_growth.py
    scoring/                  # deterministic Layer A + roll-up + trace
      engine.py  categories.py  rollup.py  fair_value.py
    reasoning/                # Layer B (thesis) — LLM at the edges
      provider.py  deepseek.py  local_stub.py  thesis.py  growth_entry.py
      situation.py  peers.py  news_sentiment.py  cache.py
    decision/                 # Layer C + D
      vetoes.py  gates.py  verdict.py  action.py
    pipeline/                 # orchestration
      run.py  phases.py
    tracking/                 # recommendations + paper portfolio
      recommendations.py  paper_portfolio.py
    backtest/                 # replay scoring over history
      harness.py
    lib/                      # ported v1 helpers (red_flags, risk_model, intrinsic, insider_flow, near_200wma …)
  web/                        # the UI (ported from prototype/)
    index.html  app.js  styles.css  factor-card.js …
    data/                     # JSON exports the UI reads (committed)
  scripts/                    # thin CLI entry points
    refresh.py  screen.py  score.py  export.py  build_all.py
  tests/
  .github/workflows/
    refresh.yml               # scheduled + manual dispatch; deploy Pages
  docs/
    IMPLEMENTATION_PLAN.md    # this file
    methodology.md            # GENERATED from config
```

---

## 3. Configuration — the single sources of truth

Four declarative files. Adding/changing a field, sector, screener, or scoring item = one edit here, nowhere else (Requirement: *config over code*).

**`catalog.yaml`** — every field (example rows):
```yaml
- id: price
  entity: security
  source: finviz
  adapter: finviz
  cadence: twice_daily
  unit: usd
  raw_path: Price
  freshness_limit_h: 8
  required_for: [valuation, market_confirmation]
  cost_tier: cheap
- id: sma_200week
  entity: security
  source: yfinance
  adapter: yfinance
  cadence: weekly
  transform: mean_of_last_200_weekly_closes
  freshness_limit_h: 200
  required_for: [market_confirmation]
- id: altman_z
  entity: company
  source: sec_edgar
  adapter: sec_edgar
  cadence: filing_based
  transform: altman_z            # ported from v1 risk_model
  required_for: [survival, vetoes]
```

**`sectors.yaml`** — sector list + liquidity/universe filters.
**`scoring.yaml`** — categories → items (weight, metric ref, rule), category weights, verdict bands, veto list, soft-gate list, presets (Conservative Deep Value / Aggressive Deep Value / Quality Growth Entry / Patient Entry).
**`defaults.yaml`** — default user-editable settings (MoS %, target upside %, holding period, method weights) with per-setting `recommended`, `impact`, `range`.

Each file is loaded through `config.py` with **schema validation** — a typo fails loudly (Requirement: config validation).

---

## 4. Data contract (store ⇄ UI)

The UI reads committed JSON exports whose shape matches the prototype's drill-down (scoring model §10). Frozen shape:

```json
{
  "meta": { "generated_at": "...", "config_version": "...", "sectors": [...] },
  "stocks": [{
    "ticker": "AVNT", "company": "...", "sector": "...", "strategy": ["deepvalue"],
    "price": 29.4,
    "verdict": "Buy", "action": "Buy Now", "score": 74, "confidence": "...",
    "base_score": 71.0, "thesis_modifier": 3, "preliminary": 74.0,
    "categories": [{
      "id": "quality", "label": "Business Quality", "weight": 16, "score": 72,
      "items": [{ "label": "...", "weight": 30, "score": 72,
        "actual": "48%", "expected": "≥ sector 41%", "rule": "...",
        "source": "fundamentals", "fetched_at": "..." }]
    }],
    "thesis": { "type": "recovery", "score": 78, "modifier": 3, "summary": "...",
      "situation": [{ "reason": "...", "status": "active", "temporary_vs_structural": "...",
        "severity": "...", "expected_duration": "...", "evidence": "...", "source": "..." }],
      "kill_switch": "..." },
    "valuation": { "fair_low": 34, "fair_base": 42.5, "fair_high": 52,
      "buy_zone": 36.1, "margin_of_safety_pct": 15, "upside_pct": 45, "label": "cheap",
      "methods": [ ... ] },
    "vetoes": [], "soft_gates": [], "labels": [ ... ],
    "action_plan": { "position_size": "...", "entry_logic": "...", "add_trigger": "...",
      "exit_or_stop": "...", "review_date": "...", "key_watch_items": [...] },
    "watchlist_sources": [ ... ],
    "provenance": { "min_fetched_at": "...", "coverage_pct": 96, "stale_fields": [...] }
  }]
}
```

This contract is the seam between back-end and front-end; it's fixed before Phase 2 so both sides build to it.

---

## 5. Build phases

Each phase is independently useful, testable, and ends with a **verification** step (drive it, don't just unit-test). I commit at phase boundaries.

### Phase 0 — Scaffold & config (foundation)
- Repo tree (§2), `requirements.txt`, `.gitignore`, `.env.example`.
- Write `catalog.yaml`, `sectors.yaml`, `scoring.yaml`, `defaults.yaml` (derived directly from the scoring model).
- `config.py` with schema validation + tests.
- Freeze the JSON data contract (§4).
- **Verify:** `python -m fairentry.config --validate` passes; contract documented.

### Phase 1 — Data layer
- `store/schema.sql` (tables: companies, securities, metrics_current, metrics_history, source_fetch_log, screen_results, score_results, thesis_results, recommendations, paper_portfolio) + `db.py` read/write API.
- `adapters/` — one per source, **the only code that fetches**. Port v1 fetch/cache: Finviz universe, SEC/XBRL (red_flags/companyfacts), yfinance (200wma), Finnhub news, Form 4 insiders, 13F.
- `catalog/refresh.py` + `cadence.py`: compute which fields are due, fetch via adapters, normalize per catalog `transform`, write to store with **provenance** (`source`, `fetched_at`) and **append point-in-time history**.
- `cache.py`: warm caches across runs (port v1 cache patterns); negative-cache failures.
- Source-failure isolation: one bad source degrades, never corrupts.
- **Verify:** `python scripts/refresh.py --sectors tech,comm,consumer_cyclical` populates the store; freshness/provenance rows present; second run mostly cache hits (fast).

### Phase 2 — Screener layer
- `screeners/registry.py` + `deep_value.py` + `quality_growth.py` — **store-only** (no fetching), each declares input fields + filters + output schema; writes `screen_results`.
- **Verify:** each screener runs from the store alone, produces a candidate list; `screen.py --screener deep_value` and `--screener all` both work.

### Phase 3 — Scoring engine (deterministic Layer A)
- `scoring/engine.py` reads `scoring.yaml` → computes each **item** (actual vs expected via rule → 0–100), **category** (weighted avg), **base score** (weighted avg of categories) — fully config-driven and **traceable** (emits the full node tree for the contract).
- `fair_value.py`: multi-method fair value (multiples, DCF-lite, Lynch/GARP reused from v1, peer-relative, asset/book, analyst sanity) → fair_low/base/high, buy_zone, MoS, label.
- Reuse v1: factor_score logic, intrinsic_overlay, risk_model.
- **Verify:** scores reproduce from a fixed store snapshot (reproducibility test); numbers add up exactly as shown in the UI; **score regression tests**.

### Phase 4 — Reasoning (Layer B) + Decision (Layers C, D)
- `reasoning/provider.py` — provider interface (`deepseek`, `local_stub`, future). `deepseek.py` (V4 Flash, OpenAI-compatible). **Shortlist-only**, **cache by (ticker, prompt-version, source-hash, model)**, **evidence-linked** structured output, time-boxed, fallback to deterministic.
- `thesis.py` (recovery: why-down situation list, 5-why, recovery_score 0–100, thesis_modifier per §B4) + `growth_entry.py` (growth_entry_score, required_growth_to_justify_price, fair_value_by_case, entry_zone/starter/wait, upside) + `situation.py` + `peers.py` (leader/peer set, peer-relative valuation) + `news_sentiment.py` (fixes v1's keyword bug).
- `decision/vetoes.py` (hard vetoes → Avoid), `gates.py` (soft gates → cap Buy→Watch), `verdict.py` (base + modifier − risk → bands → verdict), `action.py` (Buy Now / Starter / Wait for Pullback / Wait for Confirmation / Watch / Avoid + size, entry logic, add trigger, stop, review).
- Reuse v1: buy_reason, bear_thesis, news_judge, quality_recovery_overlay, insider_flow, near_200wma.
- **Verify:** run reasoning on a small shortlist with the real DeepSeek key; confirm cached second run makes zero LLM calls; every LLM output carries evidence + source IDs.

### Phase 5 — UI (port the prototype into the product)
- Move `prototype/` → `web/`; split inline JS/CSS into files; replace sample data with a `fetch()` of the committed JSON exports.
- Wire the **editable weights/settings** panel to persist locally and recompute against the exported category scores (already prototyped); show current/default/impact/reset/version.
- Add the **Quality Growth entry-zone view** and **watchlist-intelligence** panel.
- All required visible labels (requirements §9) rendered from the contract.
- **Verify:** drive the built UI against real exported data; drill-down reaches raw values; both strategy modes; light/dark; contrast AA (automated audit as we've been doing).

### Phase 6 — Orchestration, CI, backtesting, tracking, docs
- `pipeline/run.py`: the two-phase pipeline (refresh due data → normalize/store → screen → score → LLM shortlist → vetoes/gates → action → export JSON → track). Run one screener or all; **refresh without rescreen** and **rescreen without refetch** flags; per-step timing + run summary (run-time budget).
- `.github/workflows/refresh.yml`: scheduled (twice daily) + manual dispatch (choose screener); build store as artifact; commit JSON exports; **deploy Pages**. Secrets from Actions.
- `tracking/`: record recommendations + paper portfolio + degradation alerts.
- `backtest/harness.py`: replay scoring over `metrics_history` → hit-rate / forward returns (tune weights with evidence).
- `docs/methodology.md` **generated from config** (self-documenting).
- **Verify:** full `build_all.py` runs end-to-end locally within the runtime budget; CI dry-run succeeds; backtest produces a report.

---

## 6. DeepSeek / LLM specifics (Requirement R8, scoring §9)

- **Used only for:** news sentiment/stance, why-down diagnosis, temporary-vs-structural, catalyst extraction, peer/leader ID, bear thesis, MD&A/litigation summary, final human-readable explanation, watchlist-source discovery.
- **Never for:** ratio scoring, full-universe passes, repeated calls on unchanged inputs, silent score changes.
- **Controls:** shortlist-only; cache by input hash + prompt version; store provider/model/cost/timestamp (**provider cost tracking**); time-box; deterministic fallback; UI shows "reasoning stale/missing."
- **Provider abstraction:** scoring depends on `reasoning_provider`, not DeepSeek directly — swap/AB with `local_stub` or another provider.

---

## 7. Testing, review & verification strategy

- **Unit tests:** scoring rules, fair-value methods, veto/gate logic, cadence, config validation.
- **Data-contract tests:** each adapter returns catalog-declared shapes.
- **Reproducibility/regression tests:** fixed store snapshot → identical scores; guard against silent scoring drift.
- **Golden-file tests:** the JSON export for a fixed snapshot.
- **End-to-end verification each phase:** actually run the thing (refresh → store → screen → score → export → UI), not just tests.
- **Code review:** after each substantial phase I run the review skill, fix priority (correctness/security) defects, re-verify. UI gets the automated contrast/interaction checks we've used.

---

## 8. Non-functional coverage (Requirement §12)

Config validation ✔ · data-freshness flags ✔ (provenance) · source-failure isolation ✔ · point-in-time history ✔ (`metrics_history`) · backtesting harness ✔ (Phase 6) · score regression tests ✔ · generated methodology docs ✔ · no API keys in repo ✔ (`.env`/secrets) · not-financial-advice disclaimer ✔ (UI + docs) · run-time budget tracking ✔ (per-step timing) · rate-limit protection ✔ (cadence + cache) · provider cost tracking ✔ (LLM cache metadata).

---

## 9. Requirements traceability — nothing missed

| Requirement (spec) | Where |
|---|---|
| Single source of data / store-only screeners | Phase 1 store + Phase 2 |
| Data Catalog (one place to declare fields) | `config/catalog.yaml`, Phase 0/1 |
| Refresh cadence (2×/day, daily, weekly, filing, event) | `catalog/cadence.py`, Phase 1 |
| Source adapters (Finviz, SEC/XBRL, yfinance, Finnhub, Form 4, 13F) | `adapters/`, Phase 1 |
| Sector scope, configurable | `config/sectors.yaml` |
| Screener registry + two screeners | `screeners/`, Phase 2 |
| 4-layer decision (score → thesis → veto/gate → action) | Phases 3–4 |
| Verdict bands + inputs (score, thesis, valuation, risk, veto, gate, confidence, freshness) | `decision/verdict.py` |
| Config-driven taxonomy | `config/scoring.yaml`, Phase 3 |
| **User-editable weights/thresholds** (current/default/impact/reset/version) | `defaults.yaml` + Phase 5 UI |
| Transparent drill-down (score→…→raw value/source/fetched/confidence/missing) | Data contract §4 + Phases 3/5 |
| 7-category scoring taxonomy + items | `scoring.yaml`, Phase 3 |
| **Thesis & recovery reasoning** (structured fields, 5-why, recovery score, modifier) | `reasoning/thesis.py`, Phase 4 |
| **Quality Growth entry reasoning** (required growth, fair-price cases, entry/starter/wait zones, upside) | `reasoning/growth_entry.py` + `fair_value.py`, Phases 3–4 |
| **Watchlist intelligence** (analysts/investors/social sources) | `reasoning/` + contract, Phases 4–5 |
| DeepSeek policy (edges, shortlist, cache, evidence, abstraction) | §6, Phase 4 |
| Progressive-disclosure UI (filters, tiles, labels, drill-down) | Phase 5 (prototype ✔) |
| Required visible labels (§9 list) | Phase 5 from contract |
| Execution & scheduling (run one/all, local + Actions manual + scheduled, refresh-vs-rescreen split) | Phase 6 |
| Strategy-specific entry actions (Deep Value / Growth) | `decision/action.py`, Phase 4 |
| Reuse from v1 (action_board, buy_reason, risk_model, red_flags, intrinsic, flow, insider_flow, quality_recovery, news_judge, bear_thesis, near_200wma, paper_portfolio) | `lib/` across phases |
| Non-functional (validation, freshness, isolation, history, backtest, regression, generated docs, no keys, disclaimer, runtime, rate-limit, cost) | §8 |
| Backtestable & reproducible | Phase 3 tests + Phase 6 harness |
| Optimize 6mo–3yr, 30%+ upside target | `defaults.yaml` + gates |

---

## 10. Open decisions (needed during build, not blockers to start)

These are tunable config values; I'll start with the recommended defaults and we adjust:
- Exact category/item weights (starting: scoring model §4/§5).
- Final veto & soft-gate lists (starting: scoring model §7).
- How much the thesis modifier can move a score (starting: ±6/±10 per §B4).
- Whether user-edited weights are global or per-strategy (starting: **per-strategy presets + overrides**).
- Default MoS % and target upside % (starting: 15% / 30%).
- Final product name; second-screener name (starting: FairEntry / Quality Growth Entry).

---

## 11. How the "auto-mode" build will run

On your approval I execute the phases in order, and at each phase I:
1. Build the modules + config + tests for that phase.
2. **Verify end-to-end** (run it, drive it — not just unit tests).
3. Run a **code review**, fix priority (correctness/security) defects, re-verify.
4. Commit on a branch with a clear message and give you a short phase summary.

I'll **pause to check in** only at genuine decision points (e.g., confirming the catalog field list before wiring adapters, or the first scored output before the LLM layer) — otherwise I proceed. If a step needs the DeepSeek/Finviz/Finnhub keys, I load them the safe way (§ below) and never echo them.

**Sequencing note:** Phases 0–3 + 5 need **no LLM key** (deterministic core + UI). The DeepSeek key is only needed at **Phase 4**. So you can confirm now and I can build most of the system before we wire the LLM.

---

## 12. Secrets / keys

- `DEEPSEEK_API_KEY` — likely already in your local secrets file; if not, add to `.env` (gitignored). CI: GitHub Actions secret.
- `FINVIZ_API_KEY`, `FINNHUB_API_KEY` — same mechanism (already available locally).
- `.env` and `data/*.db` are gitignored; **no key ever enters the repo or chat.**

---

## 13. What I need from you to start

1. **Confirm this plan** (or mark changes).
2. Confirm the **starting sectors** (Tech / Comm / Consumer-Cyclical) and that **Buy≥72 / Watch≥50** bands are fine to start.
3. Nothing else is blocking — I can begin Phase 0 immediately and won't need the DeepSeek key until Phase 4.
```
