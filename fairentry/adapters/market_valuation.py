"""Public macro valuation data. Context only; never an input to stock scoring."""
from __future__ import annotations

import calendar
import csv
import io
import json
import math
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
EQUITY_SERIES = "BOGZ1FL883164115Q"
SHILLER_URL = "https://shillerdata.com/"
DEFINITIONS = {
    "buffett": {
        "name": "Buffett indicator", "unit": "%", "frequency": "Quarterly",
        "max_age_days": 180,
        "formula": "US public corporate equity value ÷ annualized nominal GDP × 100",
        "description": "Compares the size of the stock market with the economy that supports it.",
        "limitations": "Uses the Fed's all-domestic-sector public equity series, not a daily index estimate. Overseas corporate earnings, interest rates and changes in public listings affect comparisons. 100% is not a universal fair-value threshold.",
        "sources": [
            {"label": "Federal Reserve public equities", "url": f"https://fred.stlouisfed.org/series/{EQUITY_SERIES}"},
            {"label": "BEA nominal GDP", "url": "https://fred.stlouisfed.org/series/GDP"},
        ],
    },
    "cape": {
        "name": "CAPE ratio (Shiller P/E)", "unit": "×", "frequency": "Monthly",
        "max_age_days": 90,
        "formula": "S&P composite real price ÷ average real earnings over the preceding 10 years",
        "description": "Smooths the earnings cycle to put the market's price in a longer-term context.",
        "limitations": "Uses Shiller's published conventional CAPE, not total-return CAPE. Recent months may contain preliminary prices and estimated inflation or earnings. Accounting, buybacks and sector mix affect comparisons.",
        "sources": [{"label": "Robert Shiller dataset", "url": SHILLER_URL}],
    },
}


def positive(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def parse_fred(content, series):
    result = {}
    for row in csv.DictReader(io.StringIO(content)):
        value = positive(row.get(series))
        if value is not None:
            result[date.fromisoformat(row["observation_date"])] = value
    if not result:
        raise ValueError(f"No usable observations for {series}")
    return result


def buffett_history(equities, gdp, today):
    history = []
    # Before late 1996, the equity history includes closely held corporations.
    for period in sorted(equities.keys() & gdp.keys()):
        end_month = ((period.month - 1) // 3 + 1) * 3
        end = date(period.year, end_month, calendar.monthrange(period.year, end_month)[1])
        cap, output = positive(equities[period]), positive(gdp[period])
        if period.year >= 1997 and end <= today and cap and output:
            history.append({"date": end.isoformat(), "value": cap / (output * 1000) * 100,
                            "equity_millions": cap, "gdp_billions_annualized": output})
    return history


class DownloadLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.url = None

    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get("href", "")
        url = urljoin(SHILLER_URL, href)
        parsed = urlparse(url)
        if (tag == "a" and parsed.scheme == "https" and
                parsed.hostname in {"img1.wsimg.com", "shillerdata.com"} and
                parsed.path.endswith("/ie_data.xls")):
            self.url = url


def cape_history(frame, today):
    header = next((i for i in range(len(frame))
                   if str(frame.iloc[i, 0]).strip() == "Date" and
                   any(str(v).strip() == "CAPE" for v in frame.iloc[i])), None)
    if header is None:
        raise ValueError("Shiller Date/CAPE columns not found")
    column = next(i for i, v in enumerate(frame.iloc[header]) if str(v).strip() == "CAPE")
    history = {}
    for _, row in frame.iloc[header + 1:].iterrows():
        raw, value = positive(row.iloc[0]), positive(row.iloc[column])
        if raw is None or value is None:
            continue
        year = int(raw)
        month = round((raw - year) * 100)
        if not 1 <= month <= 12 or not 1997 <= year <= today.year:
            continue
        observed = date(year, month, 1)
        if observed <= today:
            history[observed.isoformat()] = value
    return [{"date": d, "value": v} for d, v in sorted(history.items())]


def fetch_history(key, session, today):
    def get(url, **kwargs):
        response = session.get(url, timeout=30, **kwargs)
        response.raise_for_status()
        return response

    if key == "buffett":
        series = [parse_fred(get("https://fred.stlouisfed.org/graph/fredgraph.csv",
                                 params={"id": name}).text, name)
                  for name in (EQUITY_SERIES, "GDP")]
        return buffett_history(*series, today)
    links = DownloadLinks()
    links.feed(get(SHILLER_URL).text)
    if not links.url:
        raise ValueError("Shiller workbook link unavailable")
    frame = pd.read_excel(io.BytesIO(get(links.url).content), sheet_name="Data",
                          header=None, engine="xlrd")
    return cape_history(frame, today)


def build_market_valuation(cache_path=None, *, now=None, fetcher=None):
    now = now or datetime.now(timezone.utc)
    fetcher = fetcher or fetch_history
    cache_path = Path(cache_path or ROOT / "data/cache/market-valuation.json")
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = {}
    result = {"generated_at": now.isoformat(), "score_effect": 0,
              "verdict_effect": "none", "indicators": {}}
    with requests.Session() as session:
        for key, definition in DEFINITIONS.items():
            prior = cached.get(key, {})
            error = None
            # A successful daily download is cached, separately for each source.
            if prior.get("retrieved_at", "")[:10] != now.date().isoformat():
                try:
                    history = fetcher(key, session, now.date())
                    if not history:
                        raise ValueError("No usable observations")
                    prior = {"history": history, "retrieved_at": now.isoformat()}
                    cached[key] = prior
                except Exception as exc:
                    error = f"Source refresh failed ({type(exc).__name__})"
            history = [p for p in prior.get("history", []) if p["date"] <= now.date().isoformat()]
            latest = history[-1] if history else {}
            values = [p["value"] for p in history]
            value = latest.get("value")
            stale = bool(history and (now.date() - date.fromisoformat(latest["date"])).days > definition["max_age_days"])
            result["indicators"][key] = {
                **definition, "value": value, "as_of": latest.get("date"),
                "retrieved_at": prior.get("retrieved_at"), "history": history,
                "status": "unavailable" if not history else "stale" if stale else "cached" if error else "available",
                "error": error, "stale": stale,
                "percentile": (100 * sum(v <= value for v in values) / len(values)) if values else None,
                "comparison_start": history[0]["date"] if history else None,
                "observation_count": len(history),
            }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cached, allow_nan=False), encoding="utf-8")
    return result


def write_market_valuation(output_path=None, **kwargs):
    result = build_market_valuation(**kwargs)
    path = Path(output_path or ROOT / "web/data/market-valuation.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    return path
