"""Local writeable state for MCP portfolio and notes tools."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .data import ROOT


STATE_PATH = ROOT / "data" / "mcp_state.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state(path: Path | str | None = None) -> dict[str, Any]:
    p = Path(path) if path else STATE_PATH
    if not p.exists():
        return {"portfolio": [], "notes": []}
    try:
        state = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"portfolio": [], "notes": []}
    return {
        "portfolio": list(state.get("portfolio") or []),
        "notes": list(state.get("notes") or []),
    }


def save_state(state: dict[str, Any], path: Path | str | None = None) -> dict[str, Any]:
    p = Path(path) if path else STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    return state


def add_position(
    ticker: str,
    shares: float = 100,
    entry_price: float | None = None,
    strategy: str | None = None,
    notes: str = "",
    entry_date: str | None = None,
    path: Path | str | None = None,
) -> dict[str, Any]:
    state = load_state(path)
    t = ticker.upper().strip()
    pos = {
        "id": f"{t}-{int(datetime.now(timezone.utc).timestamp())}",
        "ticker": t,
        "shares": float(shares),
        "entry_price": float(entry_price) if entry_price is not None else None,
        "strategy": strategy or "unspecified",
        "notes": notes,
        "entry_date": entry_date or date.today().isoformat(),
        "created_at": _now(),
        "status": "open",
    }
    state["portfolio"].append(pos)
    save_state(state, path)
    return pos


def list_portfolio(path: Path | str | None = None) -> dict[str, Any]:
    state = load_state(path)
    open_positions = [p for p in state["portfolio"] if p.get("status", "open") == "open"]
    return {"count": len(open_positions), "positions": open_positions}


def close_position(position_id: str, reason: str = "", path: Path | str | None = None) -> dict[str, Any]:
    state = load_state(path)
    for pos in state["portfolio"]:
        if pos.get("id") == position_id:
            pos["status"] = "closed"
            pos["closed_at"] = _now()
            pos["close_reason"] = reason
            save_state(state, path)
            return pos
    raise ValueError(f"Position not found: {position_id}")


def save_note(ticker: str, note: str, tag: str = "general", path: Path | str | None = None) -> dict[str, Any]:
    state = load_state(path)
    row = {
        "id": f"note-{ticker.upper().strip()}-{int(datetime.now(timezone.utc).timestamp())}",
        "ticker": ticker.upper().strip(),
        "tag": tag,
        "note": note,
        "created_at": _now(),
    }
    state["notes"].append(row)
    save_state(state, path)
    return row


def list_notes(ticker: str | None = None, path: Path | str | None = None) -> dict[str, Any]:
    state = load_state(path)
    rows = state["notes"]
    if ticker:
        t = ticker.upper().strip()
        rows = [n for n in rows if n.get("ticker") == t]
    return {"count": len(rows), "notes": rows}
