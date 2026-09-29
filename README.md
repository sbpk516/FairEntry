# FairEntry

A transparent, data-first stock-decision platform. Pull data once into a
canonical store; every screener reads from the store; a config-driven, fully
transparent scoring model produces **Buy / Watch / Avoid** with drill-down from
verdict → category → item → raw value. Two strategies: **Deep Value** and
**Quality Growth Entry**. Personal tool. Not financial advice.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env          # add FINVIZ_API_KEY (+ FINNHUB / DEEPSEEK later)

python scripts/refresh.py             # pull the universe into data/fairentry.db
python scripts/build_all.py           # screen -> score -> export web/data/board.json
python scripts/build_all.py --refresh --reason   # full run incl. LLM reasoning
python scripts/validate_live_refresh.py # fail if official/Emerging data is stale or inconsistent
python scripts/backtest.py            # prospective signal backtest once signals mature
python scripts/backtest.py --db data/backtest.db --rolling --json-out web/data/backtest.json
python -m fairentry.mcp.stdio_server  # local MCP for Codex / Claude / ChatGPT clients

# licensed Sharadar SFA historical replay (raw data stays under ignored data/)
python scripts/sharadar_snapshot.py --build-warehouse
python scripts/build_sfa_features.py
python scripts/sfa_backtest.py --step 30 --hold 30
python scripts/emerging_candidate_backtest.py  # fixed Broad/Balanced/Selective replay

# independently recalculate recorded SFA Buy returns with VectorBT Community
pip install -r requirements-research.txt
python scripts/vectorbt_crosscheck.py --require-complete
# optional full-verdict audits (larger, memory-bounded internally)
python scripts/vectorbt_crosscheck.py --verdicts Watch --require-complete --json-out data/reports/vectorbt-crosscheck-watch.json
python scripts/vectorbt_crosscheck.py --verdicts Avoid --require-complete --json-out data/reports/vectorbt-crosscheck-avoid.json

# view the app
cd web && python -m http.server 8795   # open http://localhost:8795
# portfolio tracker: http://localhost:8795/portfolio.html
```

Every JSON backtest build also writes
`web/data/target-failure-research-queue.json`. It contains only completed Buy
episodes that failed the fixed +30% one-year test and do not yet have saved
research. Existing entries in `config/target_failure_research.json` are never
overwritten. After reviewing authoritative sources, copy the example findings
file, fill it in, and append the verified findings:

```bash
python scripts/failure_research.py queue --backtest web/data/backtest.json
python scripts/failure_research.py apply --findings path/to/verified-findings.json
python scripts/backtest.py --db data/backtest.db --rolling --json-out web/data/backtest.json
```

Findings require a concise reason and at least one HTTPS source. They are
qualitative context only and never change the score or historical result.

## Market valuation context

The stock board also shows a compact US economic overview, linked to
`macro.html` for growth, employment, inflation, interest rates, both Treasury
yield-curve spreads and financial conditions. Each measure includes its level,
change from the previous observation, source, dates, explanation and history.
The Macro page also includes Buffett and CAPE valuation context. All eleven
charts share 2/5/10-year and full-history controls, historical reference ranges,
dated 1/2/5/10-year comparisons, explained colors and inspectable/downloadable
data. Official targets are distinguished from descriptive historical bands;
see [comparison methodology](docs/MACRO_COMPARISONS.md).

Run `python scripts/macro.py` to refresh economic data independently. Normal
board builds refresh it too. Series, display rules and freshness limits live in
`config/macro.yaml`. Sources are FRED's BEA, BLS, Labor Department and Federal
Reserve series. PCE annual inflation uses the exact matching month a year
earlier; missing months are not filled. Negative and zero readings are retained.
Successful source downloads are cached for a UTC day; a failed source retains
its original retrieval date and is labeled separately. No composite score or
crash probability is generated, and macro data does not enter stock scoring.

The board includes the Buffett indicator and conventional Shiller CAPE, with
source links, observation dates, historical trends and percentiles since 1997.
These are long-term valuation context, not crash forecasts; they add zero stock
score weight and do not change verdicts or trading alerts.

`python scripts/market_valuation.py` refreshes only this panel. Normal board builds
also refresh it, caching each successful source download for the UTC day under
`data/cache/`. Failed downloads retain prior observations with a visible warning;
missing data stays unavailable. The browser also flags overdue downloads.

Buffett uses Federal Reserve all-domestic-sector **public** equity liabilities
(`BOGZ1FL883164115Q`, millions of dollars), divided by same-quarter BEA annualized
nominal GDP (`GDP`, billions converted to millions), times 100. It is quarterly,
not a live market estimate. History begins in 1997 to avoid the earlier inclusion
of closely held equities. CAPE comes from the conventional `CAPE` column in
Robert Shiller's published workbook at [Shiller Data](https://shillerdata.com/);
recent months can be preliminary. Observations older than 180 days (Buffett) or
90 days (CAPE) are flagged stale. These are display freshness rules, not trading
thresholds. Historical percentiles use revised data and are not a backtest.

## Outside-universe monitoring

Every successful Finviz refresh maintains two independent snapshots:

- `finviz`: the official universe (currently at least $10M average daily dollar
  volume). Only this universe can create scores, recommendations, positions,
  and trading alerts.
- `finviz_discovery`: all $5M+ names, separated into $5M-$10M, $10M-$20M and
  $20M+ bands. Basic, Strong and Strict Match research levels are shadow
  evidence with zero official score or verdict effect.

Current emerging state is stored in `emerging_candidates`; the append-only
`emerging_candidate_events` table records every observation and lifecycle
change, including `graduated_to_active` and `no_longer_qualified`. Previously
recommended or owned stocks that later leave Finviz continue to receive
tracking-only quotes from the independent Yahoo source.

## How it works

The separate [Investors dashboard](web/investors.html) combines disclosed firm holdings and public research into an adjustable research shortlist, with full holdings, dated changes, stock evidence and configurable disclosure emails. See [setup, source coverage and alert behavior](docs/INVESTORS.md). Run `python scripts/investors.py` to build its data; SEC access requires a real `SEC_CONTACT_EMAIL`, and sending requires explicit Investors email configuration.

```
config/*.yaml → catalog refresh (adapters) → SQLite store
             → screeners (store-only) → scoring engine (config-driven)
             → reasoning (DeepSeek, shortlist-only) → board.json → web UI
```

- **`config/`** — the only place to change things: `catalog.yaml` (fields to
  pull), `sectors.yaml`, `scoring.yaml` (categories/weights/rules/vetoes/gates),
  `defaults.yaml` (user settings). Validated on load.
- **`fairentry/`** — `store/` (SQLite + provenance + history), `adapters/`
  (the only code that fetches), `catalog/` (cadence-aware refresh), `screeners/`,
  `scoring/` (transparent Layer A), `reasoning/` (Layer B, provider-abstracted),
  `pipeline/` (build + export).
- **`web/`** — the progressive-disclosure UI; reads `web/data/board.json`.
- **`fairentry/mcp/`** — local/remote MCP tools so ChatGPT, Codex, and Claude
  can query the FairEntry board, backtests, dummy portfolio, and notes.

## Status

Deterministic core (data → store → screen → score → UI) is complete and runs on
real data. The DeepSeek reasoning layer is wired and activates when the account
has balance. Builds now record a point-in-time signal ledger for prospective
backtesting, and the web app includes a browser-local dummy portfolio tracker at
`web/portfolio.html`. SEC, Form-4 insider, Finnhub news, curated-manager 13F,
watchlist-intelligence and estimate-revision enrichment are implemented. The
licensed SFA replay now includes strict ticker identity, dilution controls,
development-selected research challengers, and a capacity-aware exit-policy
portfolio replay. Research failures remain production-inert by design.

See `docs/IMPLEMENTATION_PLAN.md` for the full plan and traceability matrix, and
`docs/methodology.md` (generated from config) for the live scoring model. See
`docs/fairentry-mcp.md` for ChatGPT/Codex/Claude connection steps.
The rolling evidence report preserves frozen targets, target-hit timing,
30/60/90/180/365-day outcomes, decision traces, and field-level provenance.
Its reviewed architecture and trust boundaries are in
[`docs/BACKTEST_EVIDENCE_IMPLEMENTATION.md`](docs/BACKTEST_EVIDENCE_IMPLEMENTATION.md).
