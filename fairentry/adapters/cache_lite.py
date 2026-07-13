"""Tiny JSON file cache with per-entry TTL (ported from v1). Used by adapters to
avoid re-fetching unchanged data. Negative results are cached too (as {}).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_ROOT = ROOT / "data" / "cache"


def _slug(key: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(key))
    return safe if len(safe) <= 80 else hashlib.md5(str(key).encode()).hexdigest()


def cache_get(ns: str, key: str, ttl_days: float):
    p = CACHE_ROOT / ns / f"{_slug(key)}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        at = datetime.fromisoformat(data.get("_at", "2000-01-01"))
        if datetime.now() - at > timedelta(days=ttl_days):
            return None
        return data.get("payload")
    except Exception:
        return None


def cache_put(ns: str, key: str, payload):
    p = CACHE_ROOT / ns / f"{_slug(key)}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps({"_at": datetime.now().isoformat(), "payload": payload})
    tmp = p.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(blob, encoding="utf-8")
    os.replace(tmp, p)
