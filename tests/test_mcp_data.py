import json

import pytest

from fairentry.mcp import data


def sample_board():
    return {
        "meta": {"generated_at": "2026-07-17T00:00:00+00:00"},
        "stocks": [
            {
                "ticker": "AAA",
                "company": "Alpha App",
                "sector": "Technology",
                "country": "USA",
                "verdict": "Buy",
                "preliminary": 82,
                "price": 10,
                "strategy": ["quality_growth"],
                "valuation": {"base": 16, "upside": 60, "label": "cheap"},
                "categories": [{"id": "quality", "label": "Quality", "score": 90, "items": []}],
                "thesis": {"summary": "Good business."},
                "demand_momentum": {
                    "tone": "strong",
                    "summary": "Demand looks strong.",
                    "relative_strength": [{"period": "3m", "market_alpha_pct": 12, "sector_alpha_pct": 4}],
                },
            },
            {
                "ticker": "BBB",
                "company": "Beta Stores",
                "sector": "Consumer Cyclical",
                "verdict": "Watch",
                "preliminary": 61,
                "price": 20,
                "strategy": ["deepvalue"],
                "valuation": {"base": 24, "upside": 20, "label": "fair"},
                "categories": [{"id": "quality", "label": "Quality", "score": 50, "items": []}],
                "thesis": {"summary": "Needs confirmation."},
                "demand_momentum": {"tone": "weak", "summary": "Weak demand.", "relative_strength": []},
            },
        ],
    }


def test_board_summary_counts_and_top_buys():
    summary = data.board_summary(sample_board())

    assert summary["stock_count"] == 2
    assert summary["verdicts"] == {"Buy": 1, "Watch": 1}
    assert summary["top_buys"][0]["ticker"] == "AAA"
    assert summary["top_buys"][0]["fair_value"] == 16


def test_find_stocks_filters_by_verdict_and_demand():
    result = data.find_stocks(sample_board(), verdict="Watch", demand_tone="weak")

    assert result["count"] == 1
    assert result["stocks"][0]["ticker"] == "BBB"


def test_buy_filter_includes_quant_buy_for_backward_compatibility():
    board = sample_board()
    board["stocks"][0]["verdict"] = "Quant Buy"
    result = data.find_stocks(board, verdict="Buy")
    assert result["stocks"][0]["ticker"] == "AAA"
    assert data.board_summary(board)["buy_count"] == 1


def test_mcp_prefers_user_facing_quant_buy_but_keeps_model_verdict():
    board = sample_board()
    board["stocks"][0]["display_verdict"] = "Quant Buy"
    brief = data.get_stock(board, "AAA")["brief"]
    assert brief["verdict"] == "Quant Buy"
    assert brief["model_verdict"] == "Buy"


def test_get_stock_unknown_ticker_raises():
    with pytest.raises(data.FairEntryDataError):
        data.get_stock(sample_board(), "NOPE")


def test_backtest_summary_keeps_compact_tables():
    summary = data.backtest_summary({"generated_at": "now", "rows": list(range(20)), "alpha": 2.5})

    assert summary["generated_at"] == "now"
    assert summary["metrics"]["alpha"] == 2.5
    assert summary["tables"]["rows"] == list(range(10))


def test_stdio_handle_tools_call(monkeypatch):
    from fairentry.mcp import stdio_server

    monkeypatch.setattr(data, "load_board", lambda: sample_board())
    response = stdio_server.handle({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "get_stock", "arguments": {"ticker": "AAA"}},
    })

    assert response["result"]["structuredContent"]["brief"]["ticker"] == "AAA"
    assert json.loads(response["result"]["content"][0]["text"])["brief"]["ticker"] == "AAA"
