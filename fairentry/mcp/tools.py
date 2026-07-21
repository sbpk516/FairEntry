"""FairEntry MCP tool registry."""
from __future__ import annotations

from typing import Any, Callable

from . import data, state


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_board_summary",
        "description": "Summarize the latest FairEntry board: counts, sectors, strategies, and top Buy names.",
        "inputSchema": _schema({}),
    },
    {
        "name": "get_stock",
        "description": "Return full FairEntry details for one ticker, including valuation, categories, thesis, and context-only demand/momentum.",
        "inputSchema": _schema({"ticker": {"type": "string", "description": "Ticker symbol, e.g. ATAT"}}, ["ticker"]),
    },
    {
        "name": "find_stocks",
        "description": "Filter FairEntry stocks by verdict, sector, score, upside, and context-only demand tone.",
        "inputSchema": _schema({
            "verdict": {"type": "string", "enum": ["Quant Buy", "Buy", "Watch", "Avoid"]},
            "sector": {"type": "string"},
            "min_score": {"type": "number"},
            "min_upside": {"type": "number"},
            "demand_tone": {"type": "string", "enum": ["strong", "improving", "mixed", "weak"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        }),
    },
    {
        "name": "compare_stocks",
        "description": "Compare a list of tickers side by side using score, valuation, demand, and category scores.",
        "inputSchema": _schema({
            "tickers": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20}
        }, ["tickers"]),
    },
    {
        "name": "explain_score",
        "description": "Explain how one stock's FairEntry score was built from categories and metric items.",
        "inputSchema": _schema({"ticker": {"type": "string"}}, ["ticker"]),
    },
    {
        "name": "get_backtest_summary",
        "description": "Return the latest exported FairEntry backtest summary, if available.",
        "inputSchema": _schema({}),
    },
    {
        "name": "ask_fairentry",
        "description": "Return compact FairEntry facts relevant to a natural-language investing question.",
        "inputSchema": _schema({
            "question": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        }, ["question"]),
    },
    {
        "name": "add_portfolio_position",
        "description": "Add a local dummy portfolio position to data/mcp_state.json. Defaults to 100 shares.",
        "inputSchema": _schema({
            "ticker": {"type": "string"},
            "shares": {"type": "number", "default": 100},
            "entry_price": {"type": "number"},
            "strategy": {"type": "string"},
            "notes": {"type": "string"},
            "entry_date": {"type": "string"},
        }, ["ticker"]),
    },
    {
        "name": "list_portfolio",
        "description": "List local dummy portfolio positions stored by the FairEntry MCP server.",
        "inputSchema": _schema({}),
    },
    {
        "name": "close_portfolio_position",
        "description": "Mark a local dummy portfolio position as closed.",
        "inputSchema": _schema({
            "position_id": {"type": "string"},
            "reason": {"type": "string"},
        }, ["position_id"]),
    },
    {
        "name": "save_stock_note",
        "description": "Save a local research note for a ticker.",
        "inputSchema": _schema({
            "ticker": {"type": "string"},
            "note": {"type": "string"},
            "tag": {"type": "string"},
        }, ["ticker", "note"]),
    },
    {
        "name": "list_stock_notes",
        "description": "List local research notes, optionally filtered by ticker.",
        "inputSchema": _schema({"ticker": {"type": "string"}}),
    },
    {
        "name": "get_refresh_instructions",
        "description": "Return safe local/GitHub commands to refresh FairEntry data. This tool does not execute commands.",
        "inputSchema": _schema({}),
    },
]


def call_tool(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    board = data.load_board()
    if name == "get_board_summary":
        return data.board_summary(board)
    if name == "get_stock":
        return data.get_stock(board, str(args["ticker"]))
    if name == "find_stocks":
        return data.find_stocks(
            board,
            verdict=args.get("verdict"),
            sector=args.get("sector"),
            min_score=args.get("min_score"),
            min_upside=args.get("min_upside"),
            demand_tone=args.get("demand_tone"),
            limit=int(args.get("limit") or 20),
        )
    if name == "compare_stocks":
        return data.compare_stocks(board, list(args["tickers"]))
    if name == "explain_score":
        return data.explain_score(board, str(args["ticker"]))
    if name == "get_backtest_summary":
        return data.backtest_summary(data.load_backtest())
    if name == "ask_fairentry":
        return data.answer_from_board(board, str(args["question"]), int(args.get("limit") or 10))
    if name == "add_portfolio_position":
        ticker = str(args["ticker"])
        stock = data.stock_by_ticker(board, ticker)
        return state.add_position(
            ticker=ticker,
            shares=float(args.get("shares") or 100),
            entry_price=args.get("entry_price", stock.get("price")),
            strategy=args.get("strategy") or ("deep_value" if "deepvalue" in (stock.get("strategy") or []) else "quality_growth"),
            notes=str(args.get("notes") or ""),
            entry_date=args.get("entry_date"),
        )
    if name == "list_portfolio":
        return state.list_portfolio()
    if name == "close_portfolio_position":
        return state.close_position(str(args["position_id"]), str(args.get("reason") or ""))
    if name == "save_stock_note":
        data.stock_by_ticker(board, str(args["ticker"]))
        return state.save_note(str(args["ticker"]), str(args["note"]), str(args.get("tag") or "general"))
    if name == "list_stock_notes":
        return state.list_notes(args.get("ticker"))
    if name == "get_refresh_instructions":
        return {
            "local_refresh": "python scripts\\build_all.py",
            "local_backtest_ui": "python scripts\\backtest.py --db data\\backtest.db --rolling --json-out web\\data\\backtest.json",
            "github_pages": "Push to main or run the 'FairEntry - refresh + deploy' workflow in GitHub Actions.",
            "note": "The MCP tool returns instructions only; it does not execute refresh commands.",
        }
    raise data.FairEntryDataError(f"Unknown FairEntry MCP tool: {name}")
