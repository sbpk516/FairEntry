"""DeepSeek provider (V4 Flash, OpenAI-compatible). Cheap, reasoning-capable.
Returns parsed JSON. Records token usage so cost can be tracked. Never prints
the key.
"""
from __future__ import annotations

import json
import time

import requests

from ..adapters.base import get_key

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"          # V4 Flash non-thinking; cheap structured extraction
NAME = "deepseek"

# cumulative usage this process (provider cost tracking)
USAGE = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}


def available() -> bool:
    return bool(get_key("DEEPSEEK_API_KEY"))


def complete_json(system: str, user: str, max_tokens: int = 900,
                  timeout: int = 60, retries: int = 2) -> dict:
    """Return parsed JSON from the model. Raises on hard failure after retries."""
    key = get_key("DEEPSEEK_API_KEY", required=True)
    body = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    last = None
    for attempt in range(retries + 1):
        try:
            r = requests.post(API_URL, json=body,
                              headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
            if r.status_code != 200:
                last = f"HTTP {r.status_code}: {r.text[:200]}"
                time.sleep(1.5 * (attempt + 1))
                continue
            data = r.json()
            usage = data.get("usage", {})
            USAGE["calls"] += 1
            USAGE["prompt_tokens"] += usage.get("prompt_tokens", 0)
            USAGE["completion_tokens"] += usage.get("completion_tokens", 0)
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
        except Exception as e:  # noqa: BLE001
            last = str(e)
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"deepseek call failed: {last}")
