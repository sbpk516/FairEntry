"""HTTP JSON-RPC wrapper for the FairEntry MCP server.

This is intentionally small: deploy it behind HTTPS (Render/Fly/Railway/Cloud Run
or a reverse proxy) and set FAIRENTRY_MCP_TOKEN to require Bearer auth.
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from .stdio_server import handle


def _token() -> str:
    return os.environ.get("FAIRENTRY_MCP_TOKEN", "").strip()


def is_authorized(headers: Any) -> bool:
    expected = _token()
    if not expected:
        return True
    return headers.get("Authorization", "") == f"Bearer {expected}"


class FairEntryMCPHandler(BaseHTTPRequestHandler):
    server_version = "FairEntryMCP/0.1"

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", os.environ.get("FAIRENTRY_MCP_CORS_ORIGIN", "*"))
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(200, {"ok": True})

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            self._send(200, {"ok": True, "server": "fairentry-mcp"})
            return
        self._send(404, {"error": "not_found", "hint": "Use POST /mcp for MCP JSON-RPC."})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/mcp":
            self._send(404, {"error": "not_found"})
            return
        if not is_authorized(self.headers):
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:
            self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}})
            return
        response = handle(request)
        self._send(200, response or {"ok": True})

    def log_message(self, fmt: str, *args: Any) -> None:
        if os.environ.get("FAIRENTRY_MCP_LOG", "").lower() in {"1", "true", "yes"}:
            super().log_message(fmt, *args)


def main() -> int:
    host = os.environ.get("FAIRENTRY_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("FAIRENTRY_MCP_PORT", "8789"))
    server = ThreadingHTTPServer((host, port), FairEntryMCPHandler)
    print(f"FairEntry MCP HTTP server listening on http://{host}:{port}/mcp", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
