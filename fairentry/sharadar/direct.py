"""Direct-to-consumer Sharadar API adapter.

It emits the same logical table codes used by the Nasdaq snapshot importer so
the historical warehouse and backtester remain provider-neutral.
"""

from __future__ import annotations

from collections.abc import Iterator

import requests

from ..adapters.base import get_key
from .snapshot import _require_ok

DIRECT_TO_SFA = {
    "fundamentals": "SF1",
    "stocks": "SEP",
    "tickers": "TICKERS",
    "actions": "ACTIONS",
    "daily": "DAILY",
    "funds": "SFP",
    "insiders": "SF2",
    "holdings": "SF3",
    "holdings_ticker": "SF3A",
}


class DirectSharadarClient:
    BASE = "https://api.sharadar.com/v1.0/data"

    def __init__(
        self, api_key: str | None = None, session: requests.Session | None = None
    ):
        self.api_key = api_key or get_key("SHARADAR_API_KEY", required=True)
        self.session = session or requests.Session()

    def pages(
        self, table: str, *, limit: int = 10_000, **filters
    ) -> Iterator[list[dict]]:
        offset = 0
        while True:
            response = self.session.get(
                f"{self.BASE}/{table}",
                params={
                    "api_key": self.api_key,
                    "format": "json",
                    "limit": limit,
                    "offset": offset,
                    **filters,
                },
                timeout=120,
            )
            _require_ok(response, f"direct Sharadar {table} request")
            payload = response.json()
            rows = (
                payload.get("data", payload) if isinstance(payload, dict) else payload
            )
            if not rows:
                return
            yield rows
            if len(rows) < limit:
                return
            offset += len(rows)

    @staticmethod
    def logical_code(table: str) -> str:
        try:
            return DIRECT_TO_SFA[table]
        except KeyError as exc:
            raise ValueError(f"unsupported direct Sharadar table: {table}") from exc
