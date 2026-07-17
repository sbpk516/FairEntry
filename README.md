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
python scripts/backtest.py            # prospective signal backtest once signals mature
python -m fairentry.mcp.stdio_server  # local MCP for Codex / Claude / ChatGPT clients

# view the app
cd web && python -m http.server 8795   # open http://localhost:8795
# portfolio tracker: http://localhost:8795/portfolio.html
```

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
`web/portfolio.html`. SEC/insider/news enrichment adapters port from BagHunter v1 next.

See `docs/IMPLEMENTATION_PLAN.md` for the full plan and traceability matrix, and
`docs/methodology.md` (generated from config) for the live scoring model. See
`docs/fairentry-mcp.md` for ChatGPT/Codex/Claude connection steps.
