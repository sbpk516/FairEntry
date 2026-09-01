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
