#!/usr/bin/env python3
"""
BagHunter Accounting Red-Flag Panel

Checks every candidate for fraud tells and accounting warnings:
  1. Altman Z-Score < 1.8  → bankruptcy risk warning
  2. Beneish M-Score > -1.78 → earnings manipulation warning
  3. Auditor change in 12 months → warning
  4. Going-concern language in latest 10-Q → DISQUALIFIER

Data sources: SEC XBRL companyfacts, SEC submissions API, SEC filing text.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

# SEC fair-use requires a real identifying User-Agent (set SEC_CONTACT_EMAIL).
SEC_CONTACT_EMAIL = os.environ.get("SEC_CONTACT_EMAIL", "research@fairentry.local")
SEC_HEADERS = {"User-Agent": f"FairEntry Research {SEC_CONTACT_EMAIL}"}

SEC_RATE_LIMIT = 0.15  # ~7 req/sec, conservative

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "xbrl_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── XBRL concept fallback chains ──────────────────────────────────────────────

CONCEPT_FALLBACKS = {
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "retained_earnings": ["RetainedEarningsAccumulatedDeficit", "RetainedEarnings"],
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ],
    "net_income": [
        "NetIncomeLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
        "ProfitLoss",
    ],
    "cogs": [
        "CostOfGoodsAndServicesSold",
        "CostOfRevenue",
        "CostOfGoodsSoldExcludingDepreciationDepletionAndAmortization",
    ],
    "operating_income": ["OperatingIncomeLoss"],
    "depreciation": [
        "Depreciation",
        "DepreciationAndAmortization",
        "DepreciationDepletionAndAmortization",
    ],
    "sga": ["SellingGeneralAndAdministrativeExpense"],
    "ar": ["AccountsReceivableNetCurrent", "AccountsReceivableNet"],
    "ppe": ["PropertyPlantAndEquipmentNet", "PropertyPlantAndEquipmentGross"],
    "cfo": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    "lt_debt": [
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "LongTermDebtAndCapitalLeaseObligations",
    ],
}

# Earnings-quality hurdle: ~10% is the canonical estimated cost of equity for diversified
# US large-cap (Damodaran ERP ~5.5% + Rf ~4.5%). Used as a fixed WACC proxy for ROIC spread.
ROIC_HURDLE = 0.10
# US federal corporate tax rate used to convert OperatingIncome → NOPAT
NOPAT_TAX_RATE = 0.21

# ── SEC API helpers ───────────────────────────────────────────────────────────


def _sec_get(url: str, timeout: int = 30):
    time.sleep(SEC_RATE_LIMIT)
    return requests.get(url, headers=SEC_HEADERS, timeout=timeout)


def _cache_path(cik: str, suffix: str = "") -> Path:
    return CACHE_DIR / f"CIK{cik.zfill(10)}{suffix}.json"


def _cache_read(cik: str, suffix: str = "", ttl_days: int = 7):
    path = _cache_path(cik, suffix)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        cached_at = datetime.fromisoformat(data.get("_cached_at", "2000-01-01"))
        if datetime.now() - cached_at > timedelta(days=ttl_days):
            return None
        return data.get("payload")
    except Exception:
        return None


def _cache_write(cik: str, payload, suffix: str = ""):
    path = _cache_path(cik, suffix)
    path.write_text(json.dumps({"_cached_at": datetime.now().isoformat(), "payload": payload}, indent=2))


# ── XBRL data fetching ────────────────────────────────────────────────────────


def fetch_company_facts(cik: str) -> dict | None:
    """Fetch XBRL companyfacts from SEC with caching."""
    cached = _cache_read(cik, suffix="_facts", ttl_days=7)
    if cached:
        return cached
    try:
        resp = _sec_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik.zfill(10)}.json")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        _cache_write(cik, data, suffix="_facts")
        return data
    except Exception as e:
        print(f"    XBRL fetch error for CIK{cik}: {e}")
        return None


def get_annual_values(facts: dict, concept: str, taxonomy: str = "us-gaap") -> list:
    """Return the two most recent annual (10-K/FY) values for a concept."""
    try:
        concept_data = facts["facts"][taxonomy][concept]
    except KeyError:
        return []
    units = concept_data.get("units", {})
    values = units.get("USD", [])
    annual = [v for v in values if v.get("form") == "10-K" and v.get("fp") == "FY"]
    seen = {}
    for entry in annual:
        end_date = entry["end"]
        if end_date not in seen or entry["filed"] > seen[end_date]["filed"]:
            seen[end_date] = entry
    return sorted(seen.values(), key=lambda x: x["end"], reverse=True)[:2]


def get_metric(facts: dict, metric_key: str) -> list:
    """Get the two most recent annual values for a metric using fallbacks."""
    for concept in CONCEPT_FALLBACKS.get(metric_key, []):
        values = get_annual_values(facts, concept)
        if values:
            return values
    return []


# ── Red-flag computations ─────────────────────────────────────────────────────


def compute_altman_z(xbrl_data: dict, market_cap_b: float) -> float | None:
    """
    Altman Z-Score for bankruptcy prediction.
    Z < 1.8 = distress zone (warning)
    Z 1.8-3.0 = grey zone
    Z > 3.0 = safe zone

    Formula:
    Z = 1.2*(WC/TA) + 1.4*(RE/TA) + 3.3*(EBIT/TA) + 0.6*(MC/TL) + 1.0*(Sales/TA)
    """
    assets = get_metric(xbrl_data, "assets")
    liabilities = get_metric(xbrl_data, "liabilities")
    equity = get_metric(xbrl_data, "equity")
    retained = get_metric(xbrl_data, "retained_earnings")
    revenue = get_metric(xbrl_data, "revenue")
    operating_income = get_metric(xbrl_data, "operating_income")
    current_assets = get_metric(xbrl_data, "current_assets")
    current_liabilities = get_metric(xbrl_data, "current_liabilities")

    if not assets:
        return None

    ta = assets[0]["val"]
    if ta <= 0:
        return None

    # Working Capital
    if current_assets and current_liabilities:
        wc = current_assets[0]["val"] - current_liabilities[0]["val"]
    elif equity:
        wc = equity[0]["val"]  # proxy
    else:
        wc = 0

    # Retained Earnings
    if retained:
        re = retained[0]["val"]
    elif equity:
        re = equity[0]["val"]  # proxy
    else:
        re = 0

    # EBIT
    if operating_income:
        ebit = operating_income[0]["val"]
    else:
        ebit = 0

    # Market Cap (in dollars)
    mc = market_cap_b * 1_000_000_000

    # Total Liabilities
    tl = liabilities[0]["val"] if liabilities else 0
    if tl <= 0:
        tl = ta - (equity[0]["val"] if equity else 0)
    if tl <= 0:
        tl = 1  # avoid div by zero

    # Sales
    sales = revenue[0]["val"] if revenue else 0

    z = (
        1.2 * (wc / ta)
        + 1.4 * (re / ta)
        + 3.3 * (ebit / ta)
        + 0.6 * (mc / tl)
        + 1.0 * (sales / ta)
    )
    return round(z, 2)


def compute_beneish_m(xbrl_data: dict) -> float | None:
    """
    Beneish M-Score for earnings manipulation detection.
    M > -1.78 suggests possible manipulation.

    Requires two consecutive annual periods.
    If any component is missing, returns None.
    """
    # Need two years of data
    ar = get_metric(xbrl_data, "ar")
    revenue = get_metric(xbrl_data, "revenue")
    cogs = get_metric(xbrl_data, "cogs")
    assets = get_metric(xbrl_data, "assets")
    ppe = get_metric(xbrl_data, "ppe")
    depreciation = get_metric(xbrl_data, "depreciation")
    sga = get_metric(xbrl_data, "sga")
    liabilities = get_metric(xbrl_data, "liabilities")
    net_income = get_metric(xbrl_data, "net_income")
    cfo = get_metric(xbrl_data, "cfo")

    # All metrics need at least 2 annual values
    if not all(len(m) >= 2 for m in [revenue, assets, liabilities]):
        return None

    t0, t1 = 0, 1  # t0 = most recent, t1 = previous year

    # Helper: safe division
    def safe_div(a, b):
        return a / b if b and b != 0 else 0

    # DSRI = (AR_t / Revenue_t) / (AR_t-1 / Revenue_t-1)
    if ar and len(ar) >= 2:
        dsri = safe_div(
            safe_div(ar[t0]["val"], revenue[t0]["val"]),
            safe_div(ar[t1]["val"], revenue[t1]["val"])
        )
    else:
        dsri = 1.0  # neutral

    # GMI = [(Rev_t-1 - COGS_t-1) / Rev_t-1] / [(Rev_t - COGS_t) / Rev_t]
    if cogs and len(cogs) >= 2:
        gm_t0 = safe_div(revenue[t0]["val"] - cogs[t0]["val"], revenue[t0]["val"])
        gm_t1 = safe_div(revenue[t1]["val"] - cogs[t1]["val"], revenue[t1]["val"])
        gmi = safe_div(gm_t1, gm_t0) if gm_t0 > 0 else 1.0
    else:
        gmi = 1.0

    # AQI = [1 - (CA_t + PPE_t) / TA_t] / [1 - (CA_t-1 + PPE_t-1) / TA_t-1]
    # Simplified: use (TA - Equity) / TA as proxy for asset quality
    equity = get_metric(xbrl_data, "equity")
    if equity and len(equity) >= 2:
        aqi_t0 = safe_div(assets[t0]["val"] - equity[t0]["val"], assets[t0]["val"])
        aqi_t1 = safe_div(assets[t1]["val"] - equity[t1]["val"], assets[t1]["val"])
        aqi = safe_div(aqi_t1, aqi_t0) if aqi_t0 > 0 else 1.0
    else:
        aqi = 1.0

    # SGI = Revenue_t / Revenue_t-1
    sgi = safe_div(revenue[t0]["val"], revenue[t1]["val"])

    # DEPI = (Dep_t-1 / (Dep_t-1 + PPE_t-1)) / (Dep_t / (Dep_t + PPE_t))
    if depreciation and ppe and len(depreciation) >= 2 and len(ppe) >= 2:
        depi_t1 = safe_div(depreciation[t1]["val"], depreciation[t1]["val"] + ppe[t1]["val"])
        depi_t0 = safe_div(depreciation[t0]["val"], depreciation[t0]["val"] + ppe[t0]["val"])
        depi = safe_div(depi_t1, depi_t0) if depi_t0 > 0 else 1.0
    else:
        depi = 1.0

    # SGAI = (SGA_t / Rev_t) / (SGA_t-1 / Rev_t-1)
    if sga and len(sga) >= 2:
        sgai = safe_div(
            safe_div(sga[t0]["val"], revenue[t0]["val"]),
            safe_div(sga[t1]["val"], revenue[t1]["val"])
        )
    else:
        sgai = 1.0

    # LVGI = (TL_t / TA_t) / (TL_t-1 / TA_t-1)
    lvgi = safe_div(
        safe_div(liabilities[t0]["val"], assets[t0]["val"]),
        safe_div(liabilities[t1]["val"], assets[t1]["val"])
    )

    # TATA = (Net Income - CFO) / TA_t
    if net_income and cfo and len(net_income) >= 2 and len(cfo) >= 2:
        tata = safe_div(net_income[t0]["val"] - cfo[t0]["val"], assets[t0]["val"])
    else:
        tata = 0.0

    m = (
        -4.84
        + 0.92 * dsri
        + 0.528 * gmi
        + 0.404 * aqi
        + 0.892 * sgi
        + 0.115 * depi
        - 0.172 * sgai
        + 4.679 * tata
        - 0.327 * lvgi
    )
    return round(m, 3)


def compute_quality_signals(xbrl_data: dict) -> dict:
    """
    Three earnings-quality signals from XBRL:
      1. Sloan accrual = (NI - CFO) / Total Assets. >10% = aggressive accruals (Sloan 1996).
      2. FCF / Net Income = (CFO - CapEx) / NI. >0.8 = earnings backed by cash.
      3. ROIC vs hurdle spread = NOPAT / (Equity + LT Debt) - ROIC_HURDLE.

    Returns dict with raw values, labels, and per-signal flag list. Any missing input
    yields None for that signal rather than raising — these are advisory, not gating.
    """
    out = {
        "sloan_accrual": None, "sloan_label": "unknown",
        "fcf_ni_ratio": None, "fcf_ni_label": "unknown",
        "roic": None, "roic_spread": None, "roic_label": "unknown",
        "quality_score": None,  # 0-10 composite — None when no XBRL signals available
        "flags": [],
    }

    net_income = get_metric(xbrl_data, "net_income")
    cfo = get_metric(xbrl_data, "cfo")
    assets = get_metric(xbrl_data, "assets")
    capex = get_metric(xbrl_data, "capex")
    op_income = get_metric(xbrl_data, "operating_income")
    equity = get_metric(xbrl_data, "equity")
    lt_debt = get_metric(xbrl_data, "lt_debt")

    # Sloan accrual: high accruals predict ~8-10% annual underperformance
    if net_income and cfo and assets and assets[0]["val"] > 0:
        sloan = (net_income[0]["val"] - cfo[0]["val"]) / assets[0]["val"]
        out["sloan_accrual"] = round(sloan, 4)
        if sloan > 0.10:
            out["sloan_label"] = "aggressive"
            out["flags"].append({
                "type": "WARNING", "category": "earnings_quality",
                "text": f"Sloan accrual = {sloan*100:.1f}% of assets (>10%). Earnings outpacing cash — Sloan 1996 predicts mean-reversion drag.",
            })
        elif sloan > 0.05:
            out["sloan_label"] = "elevated"
        else:
            out["sloan_label"] = "clean"

    # FCF / Net Income: cash conversion. >0.8 means earnings are paid for in cash.
    if net_income and cfo and capex and net_income[0]["val"] > 0:
        # CapEx is reported as a positive cash-outflow magnitude in XBRL
        fcf = cfo[0]["val"] - abs(capex[0]["val"])
        ratio = fcf / net_income[0]["val"]
        out["fcf_ni_ratio"] = round(ratio, 3)
        if ratio >= 0.8:
            out["fcf_ni_label"] = "strong"
        elif ratio >= 0.4:
            out["fcf_ni_label"] = "ok"
        else:
            out["fcf_ni_label"] = "weak"
            out["flags"].append({
                "type": "WARNING", "category": "earnings_quality",
                "text": f"FCF/NI = {ratio:.2f}. Earnings poorly cash-backed — accounting profit not converting to free cash.",
            })

    # ROIC vs ~10% hurdle (Damodaran-style fixed cost-of-equity proxy)
    if op_income and equity:
        eq_v = equity[0]["val"]
        debt_v = lt_debt[0]["val"] if lt_debt else 0
        invested = eq_v + debt_v
        if invested > 0:
            nopat = op_income[0]["val"] * (1 - NOPAT_TAX_RATE)
            roic = nopat / invested
            spread = roic - ROIC_HURDLE
            out["roic"] = round(roic, 4)
            out["roic_spread"] = round(spread, 4)
            if spread >= 0.05:
                out["roic_label"] = "value_creating"
            elif spread >= 0:
                out["roic_label"] = "marginal"
            else:
                out["roic_label"] = "value_destroying"
                out["flags"].append({
                    "type": "WARNING", "category": "capital_efficiency",
                    "text": f"ROIC = {roic*100:.1f}% < {ROIC_HURDLE*100:.0f}% hurdle. Returning less than cost of capital — destroying value.",
                })

    # ── Composite quality_score (0-10) ────────────────────────────────────
    # Single number that captures all three XBRL fraud/quality signals.
    # Promotes these from background ±adjustments to a first-class score
    # used by Tier 1 in screener_growth.score_tier1.
    if (out["sloan_accrual"] is not None
            or out["fcf_ni_ratio"] is not None
            or out["roic_spread"] is not None):
        score = 5.0
        sloan = out["sloan_accrual"]
        if sloan is not None:
            sloan_pct = sloan * 100
            if sloan_pct > 15: score -= 4
            elif sloan_pct > 10: score -= 3
            elif sloan_pct > 5: score -= 1
            elif sloan_pct < 0: score += 2
        fcf_ni = out["fcf_ni_ratio"]
        if fcf_ni is not None:
            if fcf_ni > 1.2: score += 3
            elif fcf_ni > 0.8: score += 2
            elif fcf_ni > 0.4: score += 1
            elif fcf_ni < 0: score -= 2
        spread = out["roic_spread"]
        if spread is not None:
            if spread > 0.05: score += 2
            elif spread > 0: score += 1
            elif spread < -0.05: score -= 2
        out["quality_score"] = max(0, min(10, round(score)))

    return out


# ── Auditor change & going-concern checks ─────────────────────────────────────


def fetch_submissions(cik: str) -> dict | None:
    """Fetch SEC submissions with caching."""
    cached = _cache_read(cik, suffix="_submissions", ttl_days=1)
    if cached:
        return cached
    try:
        resp = _sec_get(f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        _cache_write(cik, data, suffix="_submissions")
        return data
    except Exception as e:
        print(f"    Submissions fetch error for CIK{cik}: {e}")
        return None


def check_auditor_change(cik: str, months_back: int = 12) -> dict:
    """Check for 8-K Item 4.01 (auditor change) in last N months."""
    data = fetch_submissions(cik)
    if not data:
        return {"auditor_change": False, "confidence": "low", "details": []}

    recent = data.get("filings", {}).get("recent", {})
    cutoff = (datetime.now() - timedelta(days=months_back * 30)).strftime("%Y-%m-%d")

    changes = []
    n = len(recent.get("form", []))
    for i in range(min(n, 200)):  # check last 200 filings max
        if recent["form"][i] == "8-K":
            filing_date = recent["filingDate"][i]
            if filing_date >= cutoff:
                items = recent.get("items", [""] * n)[i]
                if "4.01" in items:
                    changes.append({
                        "filing_date": filing_date,
                        "items": items,
                        "accession_number": recent["accessionNumber"][i],
                    })

    return {
        "auditor_change": len(changes) > 0,
        "confidence": "high",
        "details": changes,
    }


def check_going_concern(cik: str) -> dict:
    """
    Download latest 10-Q and search for going-concern language.
    Returns cached result if available.
    """
    cached = _cache_read(cik, suffix="_going_concern", ttl_days=7)
    if cached:
        return cached

    data = fetch_submissions(cik)
    if not data:
        return {"going_concern": False, "confidence": "low", "matched_keywords": []}

    recent = data.get("filings", {}).get("recent", {})
    latest_10q = None
    for i in range(len(recent.get("form", []))):
        if recent["form"][i] == "10-Q":
            latest_10q = {
                "filing_date": recent["filingDate"][i],
                "accession_number": recent["accessionNumber"][i],
                "primary_document": recent["primaryDocument"][i],
            }
            break

    if not latest_10q:
        return {"going_concern": False, "confidence": "low", "matched_keywords": []}

    # Build URL for primary document
    cik_no_lead = str(int(cik))
    accn_nodash = latest_10q["accession_number"].replace("-", "")
    url = (
        f"https://www.sec.gov/Archives/edgar/data/{cik_no_lead}/"
        f"{accn_nodash}/{latest_10q['primary_document']}"
    )

    try:
        resp = _sec_get(url, timeout=30)
        resp.raise_for_status()
        text = resp.text.lower()
    except Exception as e:
        print(f"    10-Q download error for CIK{cik}: {e}")
        return {"going_concern": False, "confidence": "low", "matched_keywords": []}

    # Clean HTML tags for better text search
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)

    KEYWORDS = [
        "going concern",
        "substantial doubt",
        "ability to continue as a going concern",
        "liquidity raises substantial doubt",
        "material uncertainty related to going concern",
        "raises substantial doubt about our ability to continue",
    ]

    findings = [kw for kw in KEYWORDS if kw in text]
    result = {
        "going_concern": len(findings) > 0,
        "confidence": "high",
        "filing_date": latest_10q["filing_date"],
        "matched_keywords": findings,
    }
    _cache_write(cik, result, suffix="_going_concern")
    return result


# ── Main entry point ──────────────────────────────────────────────────────────


def generate_red_flags(ticker: str, cik: str, market_cap_b: float) -> dict:
    """
    Generate complete red-flag panel for a stock.

    Returns:
      {
        "ticker": str,
        "generated_at": str,
        "flags": [
          {"type": "CRITICAL|WARNING", "category": str, "text": str}
        ],
        "flag_count": int,
        "critical_count": int,
        "warning_count": int,
        "disqualify": bool,  # True if going-concern detected
        "scores": {
          "altman_z": float|None,
          "altman_label": str,
          "beneish_m": float|None,
          "beneish_label": str,
        },
        "auditor_change": dict,
        "going_concern": dict,
      }
    """
    flags = []
    scores = {
        "altman_z": None, "altman_label": "unknown",
        "beneish_m": None, "beneish_label": "unknown",
        "sloan_accrual": None, "sloan_label": "unknown",
        "fcf_ni_ratio": None, "fcf_ni_label": "unknown",
        "roic": None, "roic_spread": None, "roic_label": "unknown",
    }

    # Fetch XBRL data once
    xbrl_data = fetch_company_facts(cik)

    if xbrl_data:
        # Altman Z
        try:
            z = compute_altman_z(xbrl_data, market_cap_b)
            scores["altman_z"] = z
            if z is not None:
                if z < 1.8:
                    scores["altman_label"] = "distress"
                    flags.append({
                        "type": "CRITICAL",
                        "category": "bankruptcy_risk",
                        "text": f"Altman Z-Score = {z} (distress zone). Elevated bankruptcy risk.",
                    })
                elif z < 3.0:
                    scores["altman_label"] = "grey"
                    flags.append({
                        "type": "WARNING",
                        "category": "bankruptcy_risk",
                        "text": f"Altman Z-Score = {z} (grey zone). Monitor balance sheet closely.",
                    })
                else:
                    scores["altman_label"] = "safe"
        except Exception as e:
            print(f"    Altman Z error for {ticker}: {e}")

        # Beneish M
        try:
            m = compute_beneish_m(xbrl_data)
            scores["beneish_m"] = m
            if m is not None:
                if m > -1.78:
                    scores["beneish_label"] = "manipulation_warning"
                    flags.append({
                        "type": "WARNING",
                        "category": "earnings_quality",
                        "text": f"Beneish M-Score = {m} (possible earnings manipulation).",
                    })
                else:
                    scores["beneish_label"] = "clean"
        except Exception as e:
            print(f"    Beneish M error for {ticker}: {e}")

        # Sloan accrual + FCF/NI + ROIC vs hurdle
        try:
            qs = compute_quality_signals(xbrl_data)
            scores["sloan_accrual"] = qs["sloan_accrual"]
            scores["sloan_label"] = qs["sloan_label"]
            scores["fcf_ni_ratio"] = qs["fcf_ni_ratio"]
            scores["fcf_ni_label"] = qs["fcf_ni_label"]
            scores["roic"] = qs["roic"]
            scores["roic_spread"] = qs["roic_spread"]
            scores["roic_label"] = qs["roic_label"]
            flags.extend(qs["flags"])
        except Exception as e:
            print(f"    Quality signals error for {ticker}: {e}")

    # Auditor change
    auditor = check_auditor_change(cik)
    if auditor.get("auditor_change"):
        details = auditor["details"][0] if auditor["details"] else {}
        flags.append({
            "type": "WARNING",
            "category": "auditor",
            "text": f"Auditor change detected ({details.get('filing_date', 'recent')}). Investigate why.",
        })

    # Going-concern (DISQUALIFIER)
    gc = check_going_concern(cik)
    if gc.get("going_concern"):
        flags.append({
            "type": "CRITICAL",
            "category": "going_concern",
            "text": f"Going-concern language found in latest 10-Q ({gc.get('filing_date', 'unknown')}). DISQUALIFIED.",
        })

    # Negative book equity (additional critical flag)
    if xbrl_data:
        equity = get_metric(xbrl_data, "equity")
        if equity and equity[0]["val"] < 0:
            flags.append({
                "type": "CRITICAL",
                "category": "balance_sheet",
                "text": "Negative stockholders' equity. Insolvent on book basis.",
            })

    return {
        "ticker": ticker,
        "generated_at": datetime.now().isoformat(),
        "flags": flags,
        "flag_count": len(flags),
        "critical_count": sum(1 for f in flags if f["type"] == "CRITICAL"),
        "warning_count": sum(1 for f in flags if f["type"] == "WARNING"),
        "disqualify": any(f["type"] == "CRITICAL" for f in flags),
        "scores": scores,
        "auditor_change": auditor,
        "going_concern": gc,
    }


def enrich_with_red_flags(stock: dict, cik_map: dict):
    """
    Convenience wrapper: attach red_flags to a stock dict.
    If the stock is disqualified, sets stock["disqualified"] = True.
    """
    ticker = stock.get("ticker", "")
    cik = cik_map.get(ticker)
    if not cik:
        stock["red_flags"] = None
        return

    market_cap_b = stock.get("market_cap_b") or stock.get("market_cap") or 0
    panel = generate_red_flags(ticker, str(cik), market_cap_b)
    stock["red_flags"] = panel
    if panel.get("disqualify"):
        stock["disqualified"] = True
