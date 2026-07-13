"""Source adapters — the ONLY code that fetches external data.
Each adapter exposes `fetch(cfg, tickers=None) -> {ticker: {field_id: value}}`
for the catalog fields it owns.
"""
from . import finviz, yfinance_adapter, sec_edgar, finnhub, form4, thirteenf  # noqa: F401

REGISTRY = {
    "finviz": finviz,
    "yfinance": yfinance_adapter,
    "sec_edgar": sec_edgar,
    "finnhub": finnhub,
    "form4": form4,
    "thirteenf": thirteenf,
}
