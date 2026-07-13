"""Cache LLM reasoning by (ticker, prompt_version, input_hash, model). If the
inputs didn't change, reuse the previous reasoning (never re-call).
"""
from __future__ import annotations

import hashlib
import json

from ..adapters.cache_lite import cache_get, cache_put

NS = "reasoning"
TTL_DAYS = 14


def key(ticker: str, prompt_version: str, model: str, inputs: dict) -> str:
    h = hashlib.md5(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()[:10]
    return f"{ticker}_{prompt_version}_{model}_{h}"


def get(k):
    return cache_get(NS, k, TTL_DAYS)


def put(k, payload):
    cache_put(NS, k, payload)
