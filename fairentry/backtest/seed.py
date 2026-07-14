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
  * every other fundamental (margins, growth, ROIC, debt, Altman-Z, target
        price, analyst rec, beta, short interest, red flags, insider) — HELD
        CONSTANT at today's value, written once at the earliest date.

So this is a *valuation- and momentum-accurate* backtest with fundamentals held
constant. It validates the **entry / valuation / timing** discipline well; it
cannot catch a name whose fundamentals rotted before its price did. Read the
results as a check on entry timing, not on the full fundamental screen.

Seeded data is written to a SEPARATE store (default ``data/backtest.db``) so it
never pollutes the live accumulating history the CI cache persists.
"""
from __future__ import annotations

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


def _num(cur: dict, k: str):
    v = cur.get(k, {})
    v = v.get("value") if isinstance(v, dict) else v
    return v if isinstance(v, (int, float)) else None


def _ma(vals: list[float], w: int):
    return sum(vals[-w:]) / w if len(vals) >= w else None


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


def weekly_closes(ticker: str, weeks: int = 156) -> list[tuple[str, float]]:
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
         weeks: int = 156, limit: int | None = None, verbose: bool = True) -> dict:
    """Read the live store's current metrics, fetch price history, and write a
    point-in-time metrics_history into a fresh backtest store.

    Returns a summary dict. Requires yfinance + network for the price history.
    """
    from ..store.db import DEFAULT_DB
    dst_db = Path(dst_db) if dst_db else DEFAULT_DB.parent / "backtest.db"
    if Path(dst_db).exists():
        Path(dst_db).unlink()   # fresh seed each run

    seeded = 0
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
            earliest = per_date[0][0]
            for fid, v in constants.items():
                dst.set_metric(t, fid, v, "seed_const", earliest)
            for d, snap in per_date:
                for fid, v in snap.items():
                    dst.set_metric(t, fid, v, "seed_hist", d)
            seeded += 1
            if verbose and seeded % 25 == 0:
                print(f"  seeded {seeded} tickers…")
        dst.commit()
    if verbose:
        print(f"Seeded {seeded} tickers -> {dst_db}")
    return {"seeded": seeded, "db": str(dst_db)}
