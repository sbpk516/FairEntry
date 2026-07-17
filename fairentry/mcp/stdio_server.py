"""Minimal stdio MCP server for local Codex/Claude integrations."""
from __future__ import annotations

import json
import sys
from typing import Any

from . import data
from .tools import TOOLS, call_tool


SERVER_INFO = {"name": "fairentry", "version": "0.1.0"}
WIDGET_URI = "ui://fairentry/board.html"
WIDGET_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>
body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;margin:0;padding:14px;background:#f6f7fb;color:#171b23}
.card{background:white;border:1px solid #dde2ec;border-radius:10px;padding:12px}
.k{font-size:11px;text-transform:uppercase;color:#657083;font-weight:800}.v{font-size:24px;font-weight:850}
</style></head><body><div class="card"><div class="k">FairEntry</div><div class="v">Stock research tools connected</div><p>Ask ChatGPT to summarize Buy stocks, compare tickers, explain scores, or inspect demand and momentum.</p></div></body></html>"""


def _result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    req_id = request.get("id")
    try:
        if method == "initialize":
            return _result(req_id, {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": SERVER_INFO,
            })
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return _result(req_id, {"tools": TOOLS})
        if method == "resources/list":
            return _result(req_id, {"resources": [{
                "uri": WIDGET_URI,
                "name": "FairEntry board widget",
                "mimeType": "text/html",
                "description": "Simple ChatGPT App widget scaffold for FairEntry.",
            }]})
        if method == "resources/read":
            params = request.get("params") or {}
            if params.get("uri") != WIDGET_URI:
                return _error(req_id, -32000, f"Unknown resource: {params.get('uri')}")
            return _result(req_id, {"contents": [{
                "uri": WIDGET_URI,
                "mimeType": "text/html",
                "text": WIDGET_HTML,
            }]})
        if method == "tools/call":
            params = request.get("params") or {}
            payload = call_tool(str(params.get("name")), params.get("arguments") or {})
            return _result(req_id, {
                "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
                "structuredContent": payload,
            })
        return _error(req_id, -32601, f"Method not found: {method}")
    except (data.FairEntryDataError, KeyError, TypeError, ValueError) as exc:
        return _error(req_id, -32000, str(exc))


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            response = handle(json.loads(line))
        except json.JSONDecodeError as exc:
            response = _error(None, -32700, f"Parse error: {exc}")
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
