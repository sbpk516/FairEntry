"""Form-4 insider-buying analysis (ported from v1 insider_flow.py + the Form-4
parsers in screener_kitty.py), made self-contained. Produces an insider_summary
and a 0-25 materiality score used by the Market Confirmation category.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

from .red_flags import SEC_HEADERS
from ..adapters.cache_lite import cache_get, cache_put

_RATE = 0.15
_TTL = 14


def _sec_get(url, timeout=30):
    time.sleep(_RATE)
    return requests.get(url, headers=SEC_HEADERS, timeout=timeout)


def _days_between(date_str):
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).days
    except Exception:
        return 9999


def _classify_title(title):
    t = (title or "").lower()
    if any(k in t for k in ("ceo", "cfo", "coo", "chief", "president", "chairman")):
        return "top_exec"
    if "director" in t:
        return "director"
    return "other"


def _form4_urls(filings, cik, lookback_days=180):
    recent = filings.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    cik_int = str(int(cik))
    urls = []
    for i, f in enumerate(forms):
        if f not in ("4", "4/A"):
            continue
        if i < len(dates) and dates[i] < cutoff:
            continue
        if i < len(accs) and i < len(docs):
            acc = accs[i].replace("-", "")
            doc = docs[i].split("/")[-1] if "/" in docs[i] else docs[i]
            urls.append({"url": f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/{doc}",
                         "date": dates[i] if i < len(dates) else ""})
    return urls


def _parse_form4(url):
    cached = cache_get("form4", url, _TTL)
    if cached is not None:
        return cached
    try:
        resp = _sec_get(url)
        if "<html" in resp.text[:200].lower():
            cache_put("form4", url, [])
            return []
        root = ET.fromstring(resp.text)
    except Exception:
        return []
    reporter = ""
    rp = root.find(".//reportingOwner/reportingOwnerId/rptOwnerName")
    if rp is not None and rp.text:
        reporter = rp.text.strip()
    title = ""
    rel = root.find(".//reportingOwner/reportingOwnerRelationship")
    if rel is not None:
        ot = rel.find("officerTitle")
        if ot is not None and ot.text:
            title = ot.text.strip()
        elif rel.find("isDirector") is not None and (rel.find("isDirector").text or "") == "1":
            title = "Director"
    txns = []
    for txn in root.findall(".//nonDerivativeTransaction"):
        try:
            code_el = txn.find(".//transactionCoding/transactionCode")
            code = code_el.text.strip() if code_el is not None and code_el.text else ""
            if code not in ("P", "S"):
                continue
            sh = txn.find(".//transactionAmounts/transactionShares/value")
            shares = float(sh.text) if sh is not None and sh.text else 0
            pr = txn.find(".//transactionAmounts/transactionPricePerShare/value")
            price = float(pr.text) if pr is not None and pr.text else 0
            dt = txn.find(".//transactionDate/value")
            date = dt.text.strip() if dt is not None and dt.text else ""
            txns.append({"who": reporter, "title": title or "Insider",
                         "type": "Buy" if code == "P" else "Sell",
                         "shares": int(shares), "price": round(price, 2),
                         "value": round(shares * price, 2), "date": date})
        except Exception:
            continue
    cache_put("form4", url, txns)
    return txns


def _mat_rel(pct):
    return 12 if pct >= 1.0 else 9 if pct >= 0.25 else 6 if pct >= 0.05 else 3 if pct >= 0.01 else 1


def _mat_abs(val):
    return 12 if val >= 1e6 else 9 if val >= 250_000 else 6 if val >= 50_000 else 3 if val >= 10_000 else 1


def score_materiality(s: dict) -> int:
    if not s or s.get("buy_count", 0) == 0:
        return 0
    score = max(_mat_rel(s.get("buy_pct_mktcap", 0)), _mat_abs(s.get("total_buy", 0)))
    tev = s.get("top_exec_buy_value", 0)
    score += 5 if tev >= 1e6 else 3 if tev >= 250_000 else 1 if tev > 0 else 0
    if s.get("is_cluster_buy"):
        score += 4
    d = s.get("days_since_last_buy")
    if d is not None:
        score += 3 if d <= 30 else 1 if d <= 90 else 0
    return min(score, 25)


def insider_summary(cik, market_cap_b, lookback_days=180, max_filings=15):
    resp = _sec_get(f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json")
    filings = resp.json()
    txns = []
    for f4 in _form4_urls(filings, cik, lookback_days)[:max_filings]:
        txns.extend(_parse_form4(f4["url"]))
    for t in txns:
        t["days_ago"] = _days_between(t.get("date", ""))
        t["role"] = _classify_title(t.get("title", ""))
    buys = [t for t in txns if t["type"] == "Buy"]
    total_buy = sum(b["value"] for b in buys)
    buyers_30 = len({b["who"] for b in buys if b["days_ago"] <= 30})
    buyers_90 = len({b["who"] for b in buys if b["days_ago"] <= 90})
    top_exec = [b for b in buys if b["role"] == "top_exec"]
    cap_usd = (market_cap_b or 0) * 1e9
    return {
        "buy_count": len(buys),
        "total_buy": round(total_buy, 2),
        "days_since_last_buy": min((b["days_ago"] for b in buys), default=None),
        "is_cluster_buy": buyers_30 >= 2 or buyers_90 >= 3,
        "top_exec_buy_value": round(sum(b["value"] for b in top_exec), 2),
        "buy_pct_mktcap": round(total_buy / cap_usd * 100, 3) if cap_usd else 0,
        "label": "Net Buying" if len(buys) else "No buying",
    }
