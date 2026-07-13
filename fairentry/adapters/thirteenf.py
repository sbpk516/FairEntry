"""SEC 13F adapter — "smart-money" institutional flow.

Instead of one shallow net-institutional-% number (Finviz `inst_trans`), this
aggregates the actual 13F-HR holdings of a curated set of respected managers
(config/managers.yaml) and answers a sharper question: *do funds you respect own
this, and are they adding or trimming?*

Design notes
------------
- **Cost is flat in the universe.** We fetch each tracked manager's two most
  recent 13F-HR filings once per run (cached weekly), build one aggregate index,
  then every ticker is a dict lookup. Adding tickers costs nothing here.
- **Matching is by normalized issuer name.** 13F infotables key on CUSIP + issuer
  name; there is no free ticker->CUSIP map, so we normalize both the filing's
  `nameOfIssuer` and our stored company name to a comparable base (drops INC/CORP/
  share-class suffixes). Multi-class names (Alphabet CL A / CL C) collapse to one
  base, which is what we want. Precise, if not exhaustive.
- **High precision, sparse coverage.** We only emit a score when >=1 tracked fund
  holds the name; "not held by our 12 funds" is not evidence of anything, so we
  stay silent and the scoring item simply drops (category renormalizes).
- Robust: any network/parse failure is isolated; the adapter returns {} rather
  than raising.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
import yaml

from ..lib import red_flags as rf   # SEC_HEADERS (UA with contact, per SEC policy)
from .cache_lite import cache_get, cache_put

OWNS = "thirteenf"
IMPLEMENTED = True

ROOT = Path(__file__).resolve().parent.parent.parent
_MANAGERS_YAML = ROOT / "config" / "managers.yaml"
_SUBM_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"
_RATE_S = 0.2   # SEC asks <=10 req/s; be polite

# name-normalization: strip legal/share-class noise so "APPLE INC" == "Apple Inc."
_SUFFIX = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|lp|llc|"
    r"holdings?|group|the|sa|nv|ag|class|cl|com|new|ord|adr|ads|shs|"
    r"a|b|c)\b", re.I)
_NONWORD = re.compile(r"[^a-z0-9 ]+")


def _norm(name: str) -> str:
    if not name:
        return ""
    s = name.lower().replace("&", " and ")
    s = _NONWORD.sub(" ", s)
    s = _SUFFIX.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _managers() -> list[dict]:
    try:
        data = yaml.safe_load(_MANAGERS_YAML.read_text(encoding="utf-8"))
        out = []
        for m in data.get("managers", []):
            cik = str(m.get("cik", "")).strip().lstrip("0")
            if cik:
                out.append({"name": m.get("name", cik), "cik": cik})
        return out
    except Exception:
        return []


def _get(url: str):
    time.sleep(_RATE_S)
    return requests.get(url, headers=rf.SEC_HEADERS, timeout=30)


def _recent_13f(cik: str, k: int = 2) -> list[str]:
    """Return the k most recent 13F-HR accession numbers (no dashes) for a CIK."""
    try:
        r = _get(_SUBM_URL.format(cik=cik.zfill(10)))
        if r.status_code != 200:
            return []
        recent = r.json().get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accns = recent.get("accessionNumber", [])
        dates = recent.get("filingDate", [])
        hits = [(dates[i], accns[i].replace("-", ""))
                for i in range(len(forms)) if forms[i].startswith("13F-HR")]
        hits.sort(reverse=True)   # newest first by filing date
        return [a for _, a in hits[:k]]
    except Exception:
        return []


def _infotable_url(cik: str, acc: str) -> str | None:
    """Find the information-table XML within a filing folder."""
    try:
        base = _ARCHIVE.format(cik=cik, acc=acc)
        r = _get(base + "/index.json")
        if r.status_code != 200:
            return None
        for item in r.json().get("directory", {}).get("item", []):
            nm = item.get("name", "")
            low = nm.lower()
            if low.endswith(".xml") and ("infotable" in low or "form13f" in low or "table" in low):
                return base + "/" + nm
        # fallback: any .xml that isn't the primary_doc header
        for item in r.json().get("directory", {}).get("item", []):
            nm = item.get("name", "")
            if nm.lower().endswith(".xml") and "primary_doc" not in nm.lower():
                return base + "/" + nm
    except Exception:
        return None
    return None


def _holdings(cik: str, acc: str) -> dict:
    """Parse one 13F infotable -> {normalized_issuer_name: total_shares}."""
    url = _infotable_url(cik, acc)
    if not url:
        return {}
    try:
        r = _get(url)
        if r.status_code != 200:
            return {}
        # strip namespaces for simple tag matching
        text = re.sub(r'\sxmlns(:\w+)?="[^"]+"', "", r.text)
        text = re.sub(r"<(/?)\w+:", r"<\1", text)
        root = ET.fromstring(text)
    except Exception:
        return {}
    out: dict[str, float] = {}
    for it in root.iter("infoTable"):
        name = (it.findtext("nameOfIssuer") or "").strip()
        shares_el = it.find("shrsOrPrnAmt")
        shares = shares_el.findtext("sshPrnamt") if shares_el is not None else None
        try:
            sh = float(shares) if shares else 0.0
        except ValueError:
            sh = 0.0
        n = _norm(name)
        if n:
            out[n] = out.get(n, 0.0) + sh
    return out


def _aggregate() -> dict:
    """Aggregate tracked managers' latest-vs-prior 13F holdings into a per-issuer
    index. Cached weekly (keyed on the set of managers)."""
    mgrs = _managers()
    ck = "agg_" + "_".join(sorted(m["cik"] for m in mgrs))
    cached = cache_get("thirteenf", ck, ttl_days=7)
    if cached is not None:
        return cached

    agg: dict[str, dict] = {}
    for m in mgrs:
        accs = _recent_13f(m["cik"], k=2)
        if not accs:
            continue
        q0 = _holdings(m["cik"], accs[0])
        q1 = _holdings(m["cik"], accs[1]) if len(accs) > 1 else {}
        for nm, sh in q0.items():
            a = agg.setdefault(nm, {"funds": [], "sh0": 0.0, "sh1": 0.0,
                                    "new": 0, "added": 0, "trimmed": 0, "exited": 0})
            if m["name"] not in a["funds"]:
                a["funds"].append(m["name"])
            a["sh0"] += sh
            prev = q1.get(nm)
            if prev is None:
                a["new"] += 1
            elif sh > prev * 1.02:
                a["added"] += 1
            elif sh < prev * 0.98:
                a["trimmed"] += 1
            a["sh1"] += (prev or 0.0)
        for nm, sh in q1.items():
            if nm not in q0:
                a = agg.setdefault(nm, {"funds": [], "sh0": 0.0, "sh1": 0.0,
                                        "new": 0, "added": 0, "trimmed": 0, "exited": 0})
                a["exited"] += 1
                a["sh1"] += sh
    cache_put("thirteenf", ck, agg)
    return agg


def _score(a: dict) -> int:
    """0-100 from breadth (how many respected funds hold it) + net flow."""
    funds = len(a["funds"])
    if funds == 0:
        return 50
    score = 40 + min(funds, 6) * 6          # breadth: 46..76
    if a["sh1"] > 0:                         # net share change QoQ
        chg = (a["sh0"] / a["sh1"] - 1) * 100
        score += max(-15, min(15, chg / 4))
    score += min(a["new"], 3) * 4           # brand-new positions are a strong tell
    score -= min(a["exited"], 3) * 6        # exits sting
    score += min(a["added"], 4) * 2
    score -= min(a["trimmed"], 4) * 2
    return int(max(0, min(100, round(score))))


def _flow_text(a: dict) -> str:
    funds = len(a["funds"])
    bits = [f"held by {funds} tracked fund" + ("s" if funds != 1 else "")]
    if a["new"]:
        bits.append(f"{a['new']} new")
    if a["added"]:
        bits.append(f"{a['added']} added")
    if a["trimmed"]:
        bits.append(f"{a['trimmed']} trimmed")
    if a["exited"]:
        bits.append(f"{a['exited']} exited")
    names = ", ".join(a["funds"][:3]) + ("…" if funds > 3 else "")
    return "; ".join(bits) + f" ({names})"


def fetch(cfg, field_ids, tickers=None, names=None):
    """names: {ticker: company_name} for issuer matching. Emits thirteenf_score
    (0-100) and thirteenf_flow (text) only for names >=1 tracked fund holds."""
    names = names or {}
    agg = _aggregate()
    if not agg:
        return {}
    out = {}
    for t in (tickers or []):
        a = agg.get(_norm(names.get(t, "")))
        if not a or not a["funds"]:
            continue
        vals = {"thirteenf_score": _score(a), "thirteenf_flow": _flow_text(a)}
        out[t] = {k: v for k, v in vals.items() if k in field_ids}
    return out
