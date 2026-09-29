"""Source-dated macro observations, independent of stock scoring."""
from __future__ import annotations

import csv
import calendar
import io
import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[2]
HISTORY_START = "1990-01-01"


def definitions():
    return yaml.safe_load((ROOT / "config/macro.yaml").read_text(encoding="utf-8"))["indicators"]


def parse_series(text, series, today):
    observations = {}
    for row in csv.DictReader(io.StringIO(text)):
        try:
            observed = date.fromisoformat(row["observation_date"])
            value = float(row[series])
        except (KeyError, ValueError, TypeError):
            continue
        if observed <= today and math.isfinite(value):
            observations[observed.isoformat()] = value
    if not observations:
        raise ValueError("No valid observations")
    return [{"date": d, "value": v} for d, v in sorted(observations.items())]


def transform(history, method):
    if method == "level":
        return history
    by_date = {p["date"]: p["value"] for p in history}
    result = []
    for point in history:
        d = date.fromisoformat(point["date"])
        prior = by_date.get(d.replace(year=d.year - 1).isoformat())
        if prior is not None and prior > 0:
            result.append({"date": point["date"], "value": (point["value"] / prior - 1) * 100})
    return result


def fetch_series(series, today):
    response = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv",
                            params={"id": series, "cosd": "1989-01-01"}, timeout=25)
    response.raise_for_status()
    return parse_series(response.text, series, today)


def describe(rule, value, change):
    if value is None:
        return "Unavailable"
    if rule == "growth":
        return "Output expanding" if value > 0 else "Output contracting" if value < 0 else "Output unchanged"
    if rule == "inflation":
        return "Above 2% target" if value > 2 else "Below 2% target" if value < 2 else "At 2% target"
    if rule == "curve":
        return "Inverted" if value < 0 else "Positive slope" if value > 0 else "Flat curve"
    if rule == "conditions":
        return "Tighter than average" if value > 0 else "Looser than average" if value < 0 else "At historical average"
    return "Comparison unavailable" if change is None else "Rising" if change > 0 else "Falling" if change < 0 else "Unchanged"


def build_macro(cache_path=None, *, now=None, fetcher=None):
    now = now or datetime.now(timezone.utc)
    fetcher = fetcher or fetch_series
    today = now.date()
    cache_path = Path(cache_path or ROOT / "data/cache/macro.json")
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(cached, dict):
            cached = {}
    except (OSError, ValueError):
        cached = {}
    specs = definitions()

    def load(pair):
        key, spec = pair
        prior = cached.get(key, {})
        error = None
        if prior.get("retrieved_at", "")[:10] != today.isoformat() or prior.get("history_version") != 2:
            try:
                history = fetcher(spec["series"], today)
                if not history:
                    raise ValueError("No observations")
                prior = {"history": history, "retrieved_at": now.isoformat(), "history_version": 2}
            except Exception as exc:
                error = f"Refresh failed ({type(exc).__name__})"
        return key, prior, error

    with ThreadPoolExecutor(max_workers=4) as pool:
        fetched = list(pool.map(load, specs.items()))
    indicators = []
    for key, prior, error in fetched:
        spec = specs[key]
        cached[key] = prior
        raw = [p for p in prior.get("history", []) if p["date"] <= today.isoformat()]
        history = transform(raw, spec["method"])
        latest = history[-1] if history else {}
        value = latest.get("value")
        previous = history[-2] if len(history) > 1 else {}
        change = round(value - previous["value"], 6) if previous else None
        freshness_date = latest.get("date")
        if freshness_date and spec["frequency"] == "Quarterly":
            d = date.fromisoformat(freshness_date)
            month = ((d.month - 1) // 3 + 1) * 3
            freshness_date = date(d.year, month, calendar.monthrange(d.year, month)[1]).isoformat()
        stale = bool(history and (today - date.fromisoformat(freshness_date)).days > spec["max_age_days"])
        curve = {}
        if spec["rule"] == "curve" and history:
            negatives = [p for p in history if p["value"] < 0]
            start = None
            if value < 0:
                for point in reversed(history):
                    if point["value"] >= 0:
                        break
                    start = point["date"]
            curve = {"last_inverted": negatives[-1]["date"] if negatives else None,
                     "inverted_since": start}
        indicators.append({**spec, "id": key, "value": value, "as_of": latest.get("date"),
                           "previous_date": previous.get("date"), "change": change, "freshness_date": freshness_date,
                           "reading": describe(spec["rule"], value, change),
                           "status": "unavailable" if value is None else "stale" if stale else "cached" if error else "available",
                           "error": error, "retrieved_at": prior.get("retrieved_at"),
                           "source_url": "https://fred.stlouisfed.org/series/" + spec["series"],
                           "history": [p for p in history if p['date'] >= HISTORY_START],
                           **curve})
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temp = cache_path.with_suffix(".tmp")
    temp.write_text(json.dumps(cached, allow_nan=False), encoding="utf-8")
    temp.replace(cache_path)
    return {"generated_at": now.isoformat(), "scope": "United States", "score_effect": 0,
            "verdict_effect": "none", "indicators": indicators}


def write_macro(output_path=None, **kwargs):
    result = build_macro(**kwargs)
    path = Path(output_path or ROOT / "web/data/macro.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)
    return path
