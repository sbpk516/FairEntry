"""Provider-neutral FairEntry data access for MCP tools.

The functions in this module are deliberately small and deterministic so the
same behavior can be exposed through local stdio MCP, remote HTTP MCP, ChatGPT,
Claude, or tests.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BOARD_PATH = ROOT / "web" / "data" / "board.json"
BACKTEST_PATH = ROOT / "web" / "data" / "backtest.json"


class FairEntryDataError(RuntimeError):
    """Raised when exported FairEntry data is unavailable or invalid."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FairEntryDataError(f"Missing FairEntry data file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FairEntryDataError(f"Invalid JSON in FairEntry data file: {path}") from exc


def load_board(path: Path | str | None = None) -> dict[str, Any]:
    return _read_json(Path(path) if path else BOARD_PATH)


def load_backtest(path: Path | str | None = None) -> dict[str, Any]:
    return _read_json(Path(path) if path else BACKTEST_PATH)


def stocks(board: dict[str, Any]) -> list[dict[str, Any]]:
    return list(board.get("stocks") or [])


def stock_by_ticker(board: dict[str, Any], ticker: str) -> dict[str, Any]:
    t = ticker.upper().strip()
    for stock in stocks(board):
        if str(stock.get("ticker", "")).upper() == t:
            return stock
    raise FairEntryDataError(f"Ticker not found in FairEntry board: {ticker}")


def _category_scores(stock: dict[str, Any]) -> dict[str, int | float | None]:
    out: dict[str, int | float | None] = {}
    for cat in stock.get("categories") or stock.get("cats") or []:
        cid = cat.get("id") or cat.get("label")
        if cid:
            out[str(cid)] = cat.get("score")
    return out


def _brief_stock(stock: dict[str, Any]) -> dict[str, Any]:
    dm = stock.get("demand_momentum") or {}
    rel = next((r for r in dm.get("relative_strength") or [] if r.get("period") == "3m"), {})
    valuation = stock.get("valuation") or {}
    thesis = stock.get("thesis") or {}
    return {
        "ticker": stock.get("ticker"),
        "company": stock.get("company"),
        "sector": stock.get("sector"),
        "country": stock.get("country"),
        "verdict": stock.get("display_verdict") or stock.get("verdict"),
        "model_verdict": stock.get("model_verdict") or stock.get("verdict"),
        "score": stock.get("preliminary", stock.get("score")),
        "price": stock.get("price"),
        "fair_value": valuation.get("base"),
        "upside_pct": valuation.get("upside"),
        "valuation_label": valuation.get("label"),
        "strategy": stock.get("strategy"),
        "thesis_summary": thesis.get("summary"),
        "demand_tone": dm.get("tone"),
        "demand_summary": dm.get("summary"),
        "three_month_vs_spy_pct": rel.get("market_alpha_pct"),
        "three_month_vs_sector_pct": rel.get("sector_alpha_pct"),
        "category_scores": _category_scores(stock),
    }


def board_summary(board: dict[str, Any]) -> dict[str, Any]:
    ss = stocks(board)
    verdicts = Counter(str(s.get("display_verdict") or s.get("verdict") or "Unknown") for s in ss)
    model_verdicts = Counter(str(s.get("model_verdict") or s.get("verdict") or "Unknown") for s in ss)
    sectors = Counter(str(s.get("sector") or "Unknown") for s in ss)
    strategies = Counter(
        str(strategy)
        for s in ss
        for strategy in (s.get("strategy") or [])
    )
    buys = [s for s in ss if s.get("verdict") in {"Buy", "Quant Buy"}]
    return {
        "generated_at": (board.get("meta") or {}).get("generated_at"),
        "stock_count": len(ss),
        "verdicts": dict(verdicts),
        "model_verdicts": dict(model_verdicts),
        "sectors": dict(sectors),
        "strategies": dict(strategies),
        "buy_count": len(buys),
        "top_buys": sorted(
            (_brief_stock(s) for s in buys),
            key=lambda x: (x.get("score") or 0, x.get("upside_pct") or -999),
            reverse=True,
        )[:10],
        "note": "FairEntry is research tooling, not financial advice.",
    }


def get_stock(board: dict[str, Any], ticker: str) -> dict[str, Any]:
    stock = stock_by_ticker(board, ticker)
    return {
        "brief": _brief_stock(stock),
        "valuation": stock.get("valuation"),
        "categories": stock.get("categories") or stock.get("cats"),
        "labels": stock.get("labels"),
        "context": stock.get("context"),
        "demand_momentum": stock.get("demand_momentum"),
        "thesis": stock.get("thesis"),
        "coverage_pct": stock.get("coverage_pct"),
        "coverage_confidence": stock.get("coverage_confidence"),
        "action": stock.get("action") or stock.get("action_plan"),
        "vetoes": stock.get("vetoes"),
        "soft_gates": stock.get("soft_gates") or stock.get("soft"),
    }


def find_stocks(
    board: dict[str, Any],
    verdict: str | None = None,
    sector: str | None = None,
    min_score: float | None = None,
    min_upside: float | None = None,
    demand_tone: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    rows = []
    for stock in stocks(board):
        brief = _brief_stock(stock)
        if verdict:
            actual = str(brief.get("verdict", "")).lower()
            wanted = verdict.lower()
            if wanted == "buy" and actual not in {"buy", "quant buy"}:
                continue
            if wanted != "buy" and actual != wanted:
                continue
        if sector and str(brief.get("sector", "")).lower() != sector.lower():
            continue
        if min_score is not None and (brief.get("score") or 0) < min_score:
            continue
        if min_upside is not None and (brief.get("upside_pct") or -999) < min_upside:
            continue
        if demand_tone and str(brief.get("demand_tone", "")).lower() != demand_tone.lower():
            continue
        rows.append(brief)
    rows.sort(key=lambda x: (x.get("score") or 0, x.get("upside_pct") or -999), reverse=True)
    return {"count": len(rows), "stocks": rows[: max(1, min(int(limit), 100))]}


def compare_stocks(board: dict[str, Any], tickers: list[str]) -> dict[str, Any]:
    return {"stocks": [_brief_stock(stock_by_ticker(board, t)) for t in tickers]}


def explain_score(board: dict[str, Any], ticker: str) -> dict[str, Any]:
    stock = stock_by_ticker(board, ticker)
    cats = stock.get("categories") or stock.get("cats") or []
    rows = []
    for cat in cats:
        rows.append({
            "category": cat.get("label") or cat.get("id"),
            "score": cat.get("score"),
            "items": [
                {
                    "label": item.get("label"),
                    "score": item.get("score"),
                    "actual": item.get("actual"),
                    "expected": item.get("expected"),
                    "rule": item.get("rule"),
                    "source": item.get("source"),
                }
                for item in cat.get("items") or []
            ],
        })
    return {
        "ticker": stock.get("ticker"),
        "company": stock.get("company"),
        "verdict": stock.get("verdict"),
        "score": stock.get("preliminary", stock.get("score")),
        "base_score": stock.get("base_score"),
        "thesis_modifier": stock.get("thesis_modifier"),
        "categories": rows,
        "plain_summary": (stock.get("thesis") or {}).get("summary"),
    }


def backtest_summary(backtest: dict[str, Any]) -> dict[str, Any]:
    # Preserve the raw top-level structure but keep the response compact.
    return {
        "generated_at": backtest.get("generated_at") or backtest.get("run_at"),
        "summary": backtest.get("summary") or backtest.get("headline") or backtest.get("note"),
        "metrics": {
            k: v for k, v in backtest.items()
            if k not in {"rows", "cohorts", "signals", "details"} and not isinstance(v, (list, dict))
        },
        "tables": {
            k: v[:10] for k, v in backtest.items()
            if isinstance(v, list)
        },
    }


def answer_from_board(board: dict[str, Any], question: str, limit: int = 10) -> dict[str, Any]:
    """Return deterministic context for an LLM to turn into prose.

    This is not an LLM. It intentionally returns selected facts for common
    questions so ChatGPT/Claude can reason over compact, relevant data.
    """
    q = question.lower()
    if "cheap" in q or "upside" in q:
        result = find_stocks(board, min_upside=30, limit=limit)
        result["interpretation_hint"] = "These have at least 30% upside to base fair value."
        return result
    if "weak" in q and ("demand" in q or "momentum" in q):
        result = find_stocks(board, demand_tone="weak", limit=limit)
        result["interpretation_hint"] = "These have weak context-only price demand/momentum."
        return result
    if "buy" in q:
        result = find_stocks(board, verdict="Buy", limit=limit)
        result["interpretation_hint"] = "These are current FairEntry Buy verdicts."
        return result
    return {
        "summary": board_summary(board),
        "interpretation_hint": "Use get_stock, find_stocks, compare_stocks, and explain_score for deeper analysis.",
    }
