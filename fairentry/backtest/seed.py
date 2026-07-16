"""Backtest history seeder — build a point-in-time metrics_history from real
historical prices (yfinance) so the model can be backtested TODAY, instead of
waiting weeks for the live pipeline to accumulate snapshots.

Method (and its honest limits)
------------------------------
For each ticker we pull its weekly close history and, at each historical week,
write a metrics snapshot:

  * ``price``                        — the actual close that week.
  * ``fwd_pe / ps_ratio / pb_ratio / pfcf_ratio`` — today's ratio scaled by
        ``price_then / price_now``. Exact IF earnings/sales/book/FCF were ~flat
        over the window (a P/E moves with price when E is constant).
  * ``perf_year / sma50 / sma200 / dist_200wma_pct`` — derived from the price
        series itself, so these are fully point-in-time correct.
  * with ``use_sec_history=True``, core filing fundamentals are reconstructed
        from SEC companyfacts using the filing date as the availability date:
        margins, ROIC proxy, leverage/liquidity, dilution, and valuation ratios
        that can be computed from SEC fundamentals + historical price.
  * every other non-SEC field (analyst target, beta, short interest, insider,
        estimate revisions) — HELD CONSTANT at today's value, written once at
        the earliest date.

So this is a *valuation- and momentum-accurate* backtest with fundamentals held
constant. It validates the **entry / valuation / timing** discipline well; it
cannot catch a name whose fundamentals rotted before its price did. Read the
results as a check on entry timing, not on the full fundamental screen.

Seeded data is written to a SEPARATE store (default ``data/backtest.db``) so it
never pollutes the live accumulating history the CI cache persists.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from ..store import Store

# ratios that move linearly with price (denominator held constant)
_PRICE_SCALED = ("fwd_pe", "ps_ratio", "pb_ratio", "pfcf_ratio")

# fundamentals we can't reconstruct historically -> held at today's value
_CONSTANT = (
    "gross_margin", "oper_margin", "profit_margin", "roe", "roic",
    "rev_growth_qoq", "eps_growth_next_y", "debt_eq", "current_ratio",
    "target_price", "analyst_recom", "beta", "short_float", "market_cap",
    "altman_z", "red_flags_score", "red_flags_critical", "going_concern",
    "share_count_yoy", "insider_score", "thirteenf_score", "estimate_revision_score",
)

_SEC_HIST_FIELDS = {
    "gross_margin", "oper_margin", "profit_margin", "roe", "roic",
    "rev_growth_qoq", "debt_eq", "current_ratio", "market_cap",
    "altman_z", "share_count_yoy", "ps_ratio", "pb_ratio", "pfcf_ratio",
}

_SEC_CONCEPTS = {
    "assets": ("Assets",),
    "liabilities": ("Liabilities",),
    "equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "retained_earnings": ("RetainedEarningsAccumulatedDeficit", "RetainedEarnings"),
    "revenue": (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ),
    "gross_profit": ("GrossProfit",),
    "cogs": (
        "CostOfGoodsAndServicesSold",
        "CostOfRevenue",
        "CostOfGoodsSoldExcludingDepreciationDepletionAndAmortization",
    ),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "debt": (
        "DebtCurrent",
        "LongTermDebtCurrent",
        "ShortTermBorrowings",
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "LongTermDebtAndCapitalLeaseObligations",
    ),
    "shares": (
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasic",
        "CommonStockSharesOutstanding",
    ),
    "cfo": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
}


def _num(cur: dict, k: str):
    v = cur.get(k, {})
    v = v.get("value") if isinstance(v, dict) else v
    return v if isinstance(v, (int, float)) else None


def _ma(vals: list[float], w: int):
    return sum(vals[-w:]) / w if len(vals) >= w else None


def _iso(d: str) -> str:
    return d[:10]


def _entries(facts: dict, metric_key: str) -> list[dict]:
    """SEC companyfacts entries for a metric, deduped by period end + filed date.

    We keep both 10-Q and 10-K style filings. The backtest later uses ``filed``
    as the availability date, which is the important anti-look-ahead guard.
    """
    out = []
    concepts = _SEC_CONCEPTS.get(metric_key, ())
    for concept in concepts:
        try:
            units = facts["facts"]["us-gaap"][concept]["units"]
        except KeyError:
            continue
        for unit in ("USD", "shares"):
            for e in units.get(unit, []):
                if e.get("form") not in {"10-Q", "10-K", "20-F", "40-F"}:
                    continue
                if not e.get("filed") or not e.get("end") or e.get("val") is None:
                    continue
                out.append({
                    "filed": _iso(e["filed"]),
                    "end": _iso(e["end"]),
                    "start": _iso(e.get("start", e["end"])),
                    "form": e.get("form"),
                    "fp": e.get("fp"),
                    "val": float(e["val"]),
                    "concept": concept,
                })
        if out:
            break
    seen = {}
    for e in out:
        key = (e["end"], e["filed"], e["form"], e["fp"])
        if key not in seen or e["concept"] < seen[key]["concept"]:
            seen[key] = e
    return sorted(seen.values(), key=lambda x: (x["filed"], x["end"]))


def _latest(entries: list[dict], asof: str):
    vals = [e for e in entries if e["filed"] <= asof]
    if not vals:
        return None
    return sorted(vals, key=lambda x: (x["filed"], x["end"]))[-1]


def _price_asof(closes: list[tuple[str, float]], asof: str):
    vals = [p for d, p in closes if d <= asof and p and p > 0]
    return vals[-1] if vals else None


def _safe_pct(num, den):
    return round((num / den) * 100, 2) if den and den != 0 and num is not None else None


def _altman(metrics: dict) -> float | None:
    assets = metrics.get("_assets")
    liabilities = metrics.get("_liabilities")
    current_assets = metrics.get("_current_assets")
    current_liabilities = metrics.get("_current_liabilities")
    retained = metrics.get("_retained_earnings")
    op = metrics.get("_operating_income")
    revenue = metrics.get("_revenue")
    market_cap = metrics.get("market_cap")
    if not assets or assets <= 0:
        return None
    wc = ((current_assets or 0) - (current_liabilities or 0)) if (
        current_assets is not None and current_liabilities is not None) else 0
    tl = liabilities or max(assets - (metrics.get("_equity") or 0), 1)
    if tl <= 0:
        tl = 1
    z = (
        1.2 * (wc / assets)
        + 1.4 * ((retained or 0) / assets)
        + 3.3 * ((op or 0) / assets)
        + 0.6 * ((market_cap or 0) / tl)
        + 1.0 * ((revenue or 0) / assets)
    )
    return round(z, 2)


def sec_fundamental_snapshots(facts: dict, closes: list[tuple[str, float]],
                              start_date: str | None = None) -> list[tuple[str, dict]]:
    """Reconstruct SEC-derived metric snapshots keyed by filing date.

    This is intentionally conservative: if a field cannot be computed from
    filed SEC facts plus a historical price, it is omitted rather than guessed.
    """
    if not facts:
        return []
    by = {k: _entries(facts, k) for k in _SEC_CONCEPTS}
    filed_dates = sorted({
        e["filed"]
        for k in ("revenue", "assets", "operating_income", "current_assets")
        for e in by.get(k, [])
    })
    if start_date:
        cutoff = (date.fromisoformat(_iso(start_date)) - timedelta(days=500)).isoformat()
        filed_dates = [d for d in filed_dates if d >= cutoff]
    out = []
    for asof in filed_dates:
        cur = {k: _latest(v, asof) for k, v in by.items()}
        revenue_e = cur.get("revenue")
        revenue = revenue_e["val"] if revenue_e else None
        gross = cur["gross_profit"]["val"] if cur.get("gross_profit") else None
        if gross is None and revenue is not None and cur.get("cogs"):
            gross = revenue - cur["cogs"]["val"]
        op = cur["operating_income"]["val"] if cur.get("operating_income") else None
        net = cur["net_income"]["val"] if cur.get("net_income") else None
        assets = cur["assets"]["val"] if cur.get("assets") else None
        liabilities = cur["liabilities"]["val"] if cur.get("liabilities") else None
        equity = cur["equity"]["val"] if cur.get("equity") else None
        current_assets = cur["current_assets"]["val"] if cur.get("current_assets") else None
        current_liabilities = cur["current_liabilities"]["val"] if cur.get("current_liabilities") else None
        retained = cur["retained_earnings"]["val"] if cur.get("retained_earnings") else None
        shares = cur["shares"]["val"] if cur.get("shares") else None
        cfo = cur["cfo"]["val"] if cur.get("cfo") else None
        capex = abs(cur["capex"]["val"]) if cur.get("capex") else None
        debt_vals = [e["val"] for e in by.get("debt", []) if e["filed"] <= asof]
        debt = sum(v for v in debt_vals[-3:] if v > 0) if debt_vals else None
        price = _price_asof(closes, asof)
        market_cap = price * shares if price and shares and shares > 0 else None

        snap = {
            "_assets": assets, "_liabilities": liabilities, "_equity": equity,
            "_current_assets": current_assets, "_current_liabilities": current_liabilities,
            "_retained_earnings": retained, "_operating_income": op, "_revenue": revenue,
            "market_cap": market_cap,
            "gross_margin": _safe_pct(gross, revenue),
            "oper_margin": _safe_pct(op, revenue),
            "profit_margin": _safe_pct(net, revenue),
            "roe": _safe_pct(net, equity),
            "current_ratio": round(current_assets / current_liabilities, 3)
            if current_assets is not None and current_liabilities else None,
            "debt_eq": round(debt / equity, 3) if debt is not None and equity else None,
            "ps_ratio": round(market_cap / revenue, 3) if market_cap and revenue and revenue > 0 else None,
            "pb_ratio": round(market_cap / equity, 3) if market_cap and equity and equity > 0 else None,
        }
        if op is not None and assets:
            invested = (debt or liabilities or 0) + (equity or 0)
            if invested > 0:
                snap["roic"] = round((op * 0.79 / invested) * 100, 2)
        if cfo is not None and capex is not None and market_cap:
            fcf = cfo - capex
            if fcf > 0:
                snap["pfcf_ratio"] = round(market_cap / fcf, 3)
        if revenue_e:
            prior = [e for e in by["revenue"] if e["end"] < revenue_e["end"] and e["val"] > 0]
            if prior:
                prev = sorted(prior, key=lambda x: x["end"])[-1]
                snap["rev_growth_qoq"] = round((revenue / prev["val"] - 1) * 100, 2)
        share_prior = [e for e in by.get("shares", []) if cur.get("shares") and e["end"] < cur["shares"]["end"] and e["val"] > 0]
        if cur.get("shares") and share_prior:
            prev = sorted(share_prior, key=lambda x: x["end"])[-1]
            snap["share_count_yoy"] = round((cur["shares"]["val"] / prev["val"] - 1) * 100, 2)
        snap["altman_z"] = _altman(snap)
        clean = {k: round(v, 4) if isinstance(v, float) else v
                 for k, v in snap.items()
                 if not k.startswith("_") and isinstance(v, (int, float))}
        if clean:
            out.append((asof, clean))
    return out


def snapshots_for(closes: list[tuple[str, float]], price_now: float,
                  fundamentals: dict) -> tuple[dict, list[tuple[str, dict]]]:
    """PURE (no network): turn a weekly close series into point-in-time snapshots.

    closes: [(date_str 'YYYY-MM-DD', close_float)] ascending by date.
    price_now: the current price the stored ratios/fundamentals correspond to.
    fundamentals: {field_id: current_value} from the live store.

    Returns (constants, per_date) where:
      constants = {field_id: value} to write ONCE at the earliest date, and
      per_date  = [(date_str, {field_id: value})] price/valuation/momentum each week.
    """
    prices = [p for _, p in closes]
    per_date: list[tuple[str, dict]] = []
    for i, (d, px) in enumerate(closes):
        if not px or px <= 0:
            continue
        snap: dict = {"price": round(px, 4)}
        # valuation ratios scale with price (E/S/B/FCF held constant)
        if price_now and price_now > 0:
            scale = px / price_now
            for fid in _PRICE_SCALED:
                v = _num(fundamentals, fid)
                if v is not None and v > 0:
                    snap[fid] = round(v * scale, 4)
        # momentum / trend, computed from the price path itself
        window = prices[: i + 1]
        ma10, ma40, ma200 = _ma(window, 10), _ma(window, 40), _ma(window, 200)
        if len(window) >= 52 and window[-52] > 0:
            snap["perf_year"] = round((px / window[-52] - 1) * 100, 2)
        if ma10:
            snap["sma50"] = round((px / ma10 - 1) * 100, 2)
        if ma40:
            snap["sma200"] = round((px / ma40 - 1) * 100, 2)
        if ma200:
            snap["dist_200wma_pct"] = round((px / ma200 - 1) * 100, 2)
        per_date.append((d, snap))

    constants = {}
    for fid in _CONSTANT:
        v = _num(fundamentals, fid)
        if v is not None:
            constants[fid] = v
    return constants, per_date


def weekly_closes(ticker: str, weeks: int = 208) -> list[tuple[str, float]]:
    """Fetch weekly closes from yfinance (network). Returns [] on any failure."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period=f"{weeks}wk", interval="1wk", auto_adjust=True)
        if hist is None or hist.empty:
            return []
        closes = hist["Close"].dropna()
        return [(idx.strftime("%Y-%m-%d"), float(v)) for idx, v in closes.items() if v == v]
    except Exception:
        return []


def seed(src_db: Path | str, dst_db: Path | str = None, tickers=None,
         weeks: int = 208, limit: int | None = None, verbose: bool = True,
         use_sec_history: bool = False) -> dict:
    """Read the live store's current metrics, fetch price history, and write a
    point-in-time metrics_history into a fresh backtest store.

    Returns a summary dict. Requires yfinance + network for the price history.
    """
    from ..store.db import DEFAULT_DB
    dst_db = Path(dst_db) if dst_db else DEFAULT_DB.parent / "backtest.db"
    if Path(dst_db).exists():
        Path(dst_db).unlink()   # fresh seed each run

    seeded = 0
    sec_seeded = 0
    cikm = {}
    if use_sec_history:
        from ..adapters.sec_edgar import _cik_map
        cikm = _cik_map()
    with Store(src_db) as src, Store(dst_db) as dst:
        secs = src.securities()
        if tickers:
            want = {t.upper() for t in tickers}
            secs = [s for s in secs if s["ticker"].upper() in want]
        if limit:
            # prefer the largest names (most liquid history) when capping
            def cap(s):
                return _num(src.metrics_for(s["ticker"]), "market_cap") or 0
            secs = sorted(secs, key=cap, reverse=True)[:limit]

        for s in secs:
            t = s["ticker"]
            cur = src.metrics_for(t)
            price_now = _num(cur, "price")
            if not price_now:
                continue
            closes = weekly_closes(t, weeks)
            if len(closes) < 30:          # need a couple years for MAs + a forward window
                continue
            dst.upsert_security(t, s.get("company", ""), s.get("sector", ""),
                                s.get("industry", ""), s.get("country", ""))
            constants, per_date = snapshots_for(closes, price_now, cur)
            sec_per_date = []
            if use_sec_history and cikm.get(t.upper()):
                from ..lib.red_flags import fetch_company_facts
                facts = fetch_company_facts(cikm[t.upper()])
                sec_per_date = sec_fundamental_snapshots(facts, closes, start_date=closes[0][0])
                if sec_per_date:
                    sec_seeded += 1
                    for fid in list(constants):
                        if fid in _SEC_HIST_FIELDS:
                            constants.pop(fid, None)
            earliest = per_date[0][0]
            for fid, v in constants.items():
                dst.set_metric(t, fid, v, "seed_const", earliest)
            for d, snap in sec_per_date:
                for fid, v in snap.items():
                    dst.set_metric(t, fid, v, "sec_hist", d)
            for d, snap in per_date:
                for fid, v in snap.items():
                    if use_sec_history and fid in {"ps_ratio", "pb_ratio", "pfcf_ratio"} and sec_per_date:
                        continue
                    dst.set_metric(t, fid, v, "seed_hist", d)
            seeded += 1
            if verbose and seeded % 25 == 0:
                note = f", {sec_seeded} with SEC history" if use_sec_history else ""
                print(f"  seeded {seeded} tickers{note}…")
        dst.commit()
    if verbose:
        note = f" ({sec_seeded} with SEC filing fundamentals)" if use_sec_history else ""
        print(f"Seeded {seeded} tickers{note} -> {dst_db}")
    return {"seeded": seeded, "sec_seeded": sec_seeded, "db": str(dst_db)}
