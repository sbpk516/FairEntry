"""Point-in-time FairEntry replay directly over the private SFA warehouse."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import statistics
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import numpy as np

from ..analytics.demand_momentum import _SECTOR_ETF
from ..analytics.breakout_setup import (breakout_price_metric,
    breakout_volume_metric, relative_strength_metric, trend_regime_metric)
from ..scoring.engine import medians_from, score_ticker
from .evidence import (
    evaluate_path,
    fixed_return_milestones,
    metrics_for_policy,
    quality_for,
    summarize_buy_return_achievement,
    summarize_methods,
    summarize_targets,
)
from .harness import (
    _compact_categories,
    _compact_valuation,
    _live_primary_strategy,
    _screen_memberships,
    _settings_for_strategy,
)
from .strategy import load_strategy
from .targets import targets_for
from .universe import deduplicate_issuers, issuer_key


def _implementation_fingerprint() -> str:
    """Fingerprint replay/scoring code and live model config for reproducibility."""
    repo = Path(__file__).resolve().parents[2]
    paths = [
        Path(__file__).resolve(),
        Path(__file__).with_name("evidence.py"),
        Path(__file__).with_name("harness.py"),
        Path(__file__).with_name("strategy.py"),
        Path(__file__).with_name("targets.py"),
        Path(__file__).with_name("universe.py"),
        repo / "config" / "catalog.yaml",
        repo / "config" / "defaults.yaml",
        repo / "config" / "scoring.yaml",
        repo / "config" / "sectors.yaml",
        repo / "config" / "backtest_sfa.yaml",
        repo / "requirements.txt",
        repo / "fairentry" / "analytics" / "breakout_setup.py",
        repo / "fairentry" / "analytics" / "demand_momentum.py",
        repo / "fairentry" / "pipeline" / "export.py",
        repo / "fairentry" / "backtest" / "seed.py",
        repo / "fairentry" / "backtest" / "sfa_tune.py",
        repo / "fairentry" / "sharadar" / "warehouse.py",
        repo / "scripts" / "build_all.py",
        repo / "scripts" / "build_sfa_features.py",
        repo / "scripts" / "sfa_backtest.py",
        *sorted((repo / "fairentry" / "scoring").glob("*.py")),
        *sorted((repo / "fairentry" / "screeners").glob("*.py")),
    ]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(repo).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def _two_way_bootstrap_spread(observations: list[dict], horizon: int,
                              samples: int = 1000, seed: int = 44):
    rows = [observation for observation in observations
            if observation.get("verdict") in {"Buy", "Avoid"}
            and isinstance(observation.get("horizons", {}).get(str(horizon), {}).get("alpha_pct"),
                           (int, float))]
    cohorts = sorted({row["decision_date"] for row in rows})
    securities = sorted({row.get("security_id") or row["ticker"] for row in rows})
    if len(cohorts) < 4 or len(securities) < 4:
        return None
    cohort_index = {key: index for index, key in enumerate(cohorts)}
    security_index = {key: index for index, key in enumerate(securities)}
    row_cohorts = np.asarray([cohort_index[row["decision_date"]] for row in rows])
    row_securities = np.asarray([
        security_index[row.get("security_id") or row["ticker"]] for row in rows
    ])
    values = np.asarray([row["horizons"][str(horizon)]["alpha_pct"] for row in rows])
    buys = np.asarray([row["verdict"] == "Buy" for row in rows])
    avoids = ~buys
    rng, spreads = np.random.default_rng(seed), []
    for _ in range(samples):
        cohort_weights = np.bincount(
            rng.integers(0, len(cohorts), len(cohorts)), minlength=len(cohorts)
        )
        security_weights = np.bincount(
            rng.integers(0, len(securities), len(securities)), minlength=len(securities)
        )
        weights = cohort_weights[row_cohorts] * security_weights[row_securities]
        buy_weight, avoid_weight = weights[buys].sum(), weights[avoids].sum()
        if buy_weight and avoid_weight:
            spreads.append((values[buys] * weights[buys]).sum() / buy_weight -
                           (values[avoids] * weights[avoids]).sum() / avoid_weight)
    spreads.sort()
    return ([round(spreads[int(.05 * len(spreads))], 2),
             round(spreads[int(.95 * len(spreads)) - 1], 2)] if spreads else None)


def _cohort_bootstrap_spread(observations: list[dict], horizon: int,
                             samples: int = 1000, seed: int = 42):
    cohorts = sorted({row["decision_date"] for row in observations})
    if len(cohorts) < 4:
        return None
    values = {key: {"Buy": [], "Avoid": []} for key in cohorts}
    for row in observations:
        verdict = row.get("verdict")
        alpha = row.get("horizons", {}).get(str(horizon), {}).get("alpha_pct")
        if verdict in {"Buy", "Avoid"} and isinstance(alpha, (int, float)):
            values[row["decision_date"]][verdict].append(alpha)
    buy_sum = np.asarray([sum(values[key]["Buy"]) for key in cohorts], dtype=float)
    buy_n = np.asarray([len(values[key]["Buy"]) for key in cohorts], dtype=float)
    avoid_sum = np.asarray([sum(values[key]["Avoid"]) for key in cohorts], dtype=float)
    avoid_n = np.asarray([len(values[key]["Avoid"]) for key in cohorts], dtype=float)
    rng = np.random.default_rng(seed)
    chosen = rng.integers(0, len(cohorts), size=(samples, len(cohorts)))
    total_buy_n, total_avoid_n = buy_n[chosen].sum(axis=1), avoid_n[chosen].sum(axis=1)
    valid = (total_buy_n > 0) & (total_avoid_n > 0)
    spreads = (buy_sum[chosen].sum(axis=1)[valid] / total_buy_n[valid] -
               avoid_sum[chosen].sum(axis=1)[valid] / total_avoid_n[valid])
    if not len(spreads):
        return None
    lo, hi = np.quantile(spreads, [.05, .95], method="nearest")
    return [round(float(lo), 2), round(float(hi), 2)]


def _metric(value, source, effective):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return {
        "value": float(value) if isinstance(value, (int, float)) else value,
        "source": source,
        "fetched_at": str(effective),
    }


def _pct(value):
    return None if value is None or pd.isna(value) else float(value) * 100


def _safe_ratio(num, den, scale=1.0):
    if num is None or den in (None, 0) or pd.isna(num) or pd.isna(den):
        return None
    return float(num) / float(den) * scale


def _altman(row):
    a = row.get("assets")
    if not a or pd.isna(a) or a <= 0:
        return None
    liabilities = row.get("liabilities")

    def ratio(n, d):
        return _safe_ratio(n, d, 1) or 0

    return (
        (
            1.2 * ratio(row.get("workingcapital"), a)
            + 1.4 * ratio(row.get("retearn"), a)
            + 3.3 * ratio(row.get("ebit"), a)
            + 0.6
            * (_safe_ratio(row.get("marketcap_daily"), liabilities, 1_000_000) or 0)
            + (_safe_ratio(row.get("revenue"), a, 1) or 0)
        )
        if liabilities
        else None
    )


def _trend_label(values: list[float | None]) -> str:
    values = [value for value in values if isinstance(value, (int, float))]
    if len(values) < 2:
        return "unknown"
    delta = values[-1] - values[0]
    return "improving" if delta >= 2 else "worsening" if delta <= -2 else "stable"


def _margin_trend_score(row: dict) -> float | None:
    current_oper = _safe_ratio(row.get("opinc_q"), row.get("revenue_q"), 100)
    previous_oper = _safe_ratio(row.get("opinc_prev_q"), row.get("revenue_prev_q"), 100)
    labels = [
        _trend_label([_pct(row.get("grossmargin_prev_q")), _pct(row.get("grossmargin_q"))]),
        _trend_label([previous_oper, current_oper]),
    ]
    known = [label for label in labels if label != "unknown"]
    if not known:
        return None
    improving, worsening = known.count("improving"), known.count("worsening")
    combined = ("improving" if improving >= 2 and not worsening else
                "worsening" if worsening >= 2 and not improving else
                "stabilizing" if improving > worsening else
                "deteriorating" if worsening > improving else "mixed")
    return {"improving": 90, "stabilizing": 72, "stable": 55, "mixed": 45,
            "deteriorating": 25, "worsening": 10}.get(combined)


_COUNTRY_CURRENCY = {
    "united states": "USD", "u.s.a": "USD", "us": "USD", "usa": "USD",
    "china": "CNY", "hong kong": "HKD", "japan": "JPY",
    "united kingdom": "GBP", "uk": "GBP", "canada": "CAD",
    "australia": "AUD", "switzerland": "CHF", "taiwan": "TWD",
    "south korea": "KRW", "india": "INR", "brazil": "BRL",
}
_EURO_COUNTRIES = {
    "austria", "belgium", "croatia", "cyprus", "estonia", "finland",
    "france", "germany", "greece", "ireland", "italy", "latvia",
    "lithuania", "luxembourg", "malta", "netherlands", "portugal",
    "slovakia", "slovenia", "spain",
}


def _fcf_currency_conversion(row: dict) -> dict:
    """Convert report-currency FCF using only that report's stored FX rate.

    Sharadar SF1 ``fxusd`` is local-currency units per US dollar. DAILY market
    capitalization is USD millions, so non-USD FCF must be divided by fxusd
    before the two values can be compared. Country is deliberately validation
    evidence only; it never supplies or changes the exchange rate.
    """
    def clean_text(value):
        if value is None or (isinstance(value, (int, float))
                             and not math.isfinite(float(value))):
            return None
        text = str(value).strip()
        return None if text.lower() in {"", "nan", "nat", "none"} else text

    currency_text = clean_text(row.get("reporting_currency"))
    currency = currency_text.upper() if currency_text else None
    country = clean_text(row.get("country")) or ""
    fcf = row.get("fcf")
    fxusd = row.get("fxusd")
    report_date = str(row.get("datekey") or "")[:10] or None
    country_name = country.lower().split(";")[-1].strip().rstrip(".")
    expected = _COUNTRY_CURRENCY.get(country_name)
    if country_name in _EURO_COUNTRIES:
        expected = "EUR"
    country_check = (
        "not_available" if not country or not expected or not currency else
        "matches" if currency == expected else "review"
    )
    numeric_fcf = isinstance(fcf, (int, float)) and math.isfinite(float(fcf))
    numeric_fx = isinstance(fxusd, (int, float)) and math.isfinite(float(fxusd))
    base = {
        "reporting_currency": currency,
        "historical_fxusd": float(fxusd) if numeric_fx else None,
        "financial_report_date": report_date,
        "country": country or None,
        "country_expected_currency": expected,
        "country_check": country_check,
        "reported_fcf": float(fcf) if numeric_fcf else None,
        "converted_fcf_usd": None,
        "calculation": None,
        "status": "unavailable",
        "reason": "The historical financial report has no FCF value.",
    }
    if not numeric_fcf:
        return base
    if not currency:
        return {
            **base,
            "reason": "The report currency is missing, so FCF was excluded from valuation.",
        }
    if not numeric_fx or fxusd <= 0:
        return {
            **base,
            "reason": (
                f"The {report_date or 'historical'} report has no valid {currency}-to-USD "
                "rate, so FCF was excluded from valuation."
            ),
        }
    converted = float(fcf) / float(fxusd)
    return {
        **base,
        "converted_fcf_usd": converted,
        "calculation": "Reported FCF ÷ the same report's fxusd rate = FCF in USD.",
        "status": "used",
        "reason": None,
    }


def _row_metrics(row: dict, asof: str, spy_3m: float | None,
                 sector_3m: float | None = None) -> dict:
    effective_f = row.get("datekey") or asof
    effective_p = row.get("price_date") or asof
    failed_support = bool((row.get("history_sessions") or 0) >= 80
                          and row.get("support126") and row.get("close")
                          and row["close"] <= row["support126"] * .97)
    breakout_price, _ = breakout_price_metric(
        row.get("close"), row.get("resistance50"), failed=failed_support
    )
    breakout_volume, _ = breakout_volume_metric(row.get("volume"), row.get("avgvol50"))
    conversion = _fcf_currency_conversion(row)
    values = {
        "price": (row.get("close"), "sharadar_sep"),
        "avg_dollar_volume": (
            float(row["close"]) * float(row["avgvol50"])
            if row.get("close") is not None and row.get("avgvol50") is not None else None,
            "sharadar_sep_50d_average",
        ),
        "gross_margin": (_pct(row.get("grossmargin")), "sharadar_sf1_art"),
        "oper_margin": (
            _pct(row.get("ros"))
            if row.get("ros") is not None
            else _safe_ratio(row.get("opinc"), row.get("revenue"), 100),
            "sharadar_sf1_art",
        ),
        "profit_margin": (_pct(row.get("netmargin")), "sharadar_sf1_art"),
        "roe": (_pct(row.get("roe")), "sharadar_sf1_art"),
        "roic": (_pct(row.get("roic")), "sharadar_sf1_art"),
        "debt_eq": (row.get("de"), "sharadar_sf1_art"),
        "debt_to_assets_pct": (
            _safe_ratio(row.get("debt_long_q"), row.get("assets_q"), 100),
            "sharadar_sf1_arq",
        ),
        "debt_to_assets_yago_pct": (
            _safe_ratio(row.get("debt_long_prev_y"), row.get("assets_prev_y"), 100),
            "sharadar_sf1_arq",
        ),
        "debt_to_assets_change_yoy_pp": (
            (
                _safe_ratio(row.get("debt_long_q"), row.get("assets_q"), 100)
                - _safe_ratio(row.get("debt_long_prev_y"), row.get("assets_prev_y"), 100)
            )
            if _safe_ratio(row.get("debt_long_q"), row.get("assets_q"), 100) is not None
            and _safe_ratio(row.get("debt_long_prev_y"), row.get("assets_prev_y"), 100) is not None
            else None,
            "sharadar_sf1_arq",
        ),
        "current_ratio": (row.get("currentratio"), "sharadar_sf1_art"),
        "pfcf_ratio": (
            # DAILY.marketcap is USD millions. SF1.fcf is report currency, so
            # compare it only after conversion with the same report's fxusd.
            _safe_ratio(
                row.get("marketcap_daily"),
                conversion.get("converted_fcf_usd"),
                1_000_000,
            ),
            "sharadar_sf1_art_daily",
        ),
        "ps_ratio": (row.get("ps_daily"), "sharadar_daily"),
        "pb_ratio": (row.get("pb_daily"), "sharadar_daily"),
        # FairEntry historically calls this fwd_pe. SFA DAILY is trailing; keep
        # the field for screener compatibility and expose the proxy in provenance.
        "fwd_pe": (row.get("pe_daily"), "sharadar_daily_trailing_pe_proxy"),
        "market_cap": (
            None
            if row.get("marketcap_daily") is None
            else float(row["marketcap_daily"]) * 1_000_000,
            "sharadar_daily",
        ),
        "rev_growth_qoq": (
            _safe_ratio(
                (row.get("revenue_q") - row.get("revenue_prev_y"))
                if row.get("revenue_q") is not None
                and row.get("revenue_prev_y") is not None
                else None,
                row.get("revenue_prev_y"),
                100,
            ),
            "sharadar_sf1_arq",
        ),
        "share_count_yoy": (
            _safe_ratio(
                (row.get("sharesbas_q") - row.get("shares_prev_y"))
                if row.get("sharesbas_q") is not None
                and row.get("shares_prev_y") is not None
                else None,
                row.get("shares_prev_y"),
                100,
            ),
            "sharadar_sf1_arq",
        ),
        "margin_trend_score": (_margin_trend_score(row), "sharadar_sf1_arq"),
        "altman_z": (_altman(row), "sharadar_sf1_art_daily"),
        "perf_year": (
            _safe_ratio(
                (row.get("close") - row.get("close_1y"))
                if row.get("close") is not None and row.get("close_1y") is not None
                else None,
                row.get("close_1y"),
                100,
            ),
            "sharadar_sep",
        ),
        "sma50": (
            row.get("sma50") if (row.get("history_sessions") or 0) >= 50 else None,
            "sharadar_sep",
        ),
        "sma200": (
            row.get("sma200") if (row.get("history_sessions") or 0) >= 200 else None,
            "sharadar_sep",
        ),
        "dist_200wma_pct": (
            _safe_ratio(
                (row.get("close") - row.get("wma200_proxy"))
                if (row.get("history_sessions") or 0) >= 900
                and row.get("close") is not None
                and row.get("wma200_proxy") is not None
                else None,
                row.get("wma200_proxy"),
                100,
            ),
            "sharadar_sep_200week_proxy",
        ),
        "breakout_price_score": (breakout_price, "sharadar_sep"),
        "breakout_volume_score": (breakout_volume, "sharadar_sep"),
    }
    trend_score, _ = trend_regime_metric(
        row.get("close"), row.get("sma50"), row.get("sma200"), row.get("sma50_1m_ago")
    )
    values["trend_regime_score"] = (
        trend_score,
        "sharadar_sep",
    )
    own_3m = _safe_ratio(
        (row.get("close") - row.get("close_3m"))
        if row.get("close") is not None and row.get("close_3m") is not None
        else None,
        row.get("close_3m"),
        100,
    )
    relative_score, _ = relative_strength_metric(own_3m, sector_3m, spy_3m)
    values["relative_strength_score"] = (
        relative_score,
        "sharadar_sep_vs_spy",
    )
    if row.get("beta") is not None:
        values["beta"] = (row.get("beta"), "sharadar_sep_vs_spy")
    out = {}
    for key, (value, source) in values.items():
        item = _metric(
            value,
            source,
            effective_p
            if "sep" in source or source == "sharadar_daily"
            else effective_f,
        )
        if item is not None:
            out[key] = item
    return out


class SFAReplay:
    def __init__(self, warehouse):
        self.warehouse = warehouse
        self.con = warehouse.con

    def _benchmark(self, asof: str, horizon_days: int = 63, ticker: str = "SPY"):
        rows = self.con.execute(
            """
          SELECT date, closeadj FROM sfa_fund_prices WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 253
        """,
            [ticker, asof],
        ).fetchall()
        if not rows:
            return None
        current = rows[0][1]
        prior = next(
            (r[1] for r in rows if (rows[0][0] - r[0]).days >= horizon_days), None
        )
        return (current / prior - 1) * 100 if current and prior else None

    def cohort_dates(
        self,
        start: str | None,
        end: str | None,
        step_days: int,
        warmup_days: int,
        hold_days: int,
    ) -> list[str]:
        bounds = self.con.execute(
            "SELECT min(date), max(date) FROM sfa_fund_prices WHERE ticker='SPY'"
        ).fetchone()
        lo = (
            max(date.fromisoformat(start), bounds[0] + timedelta(days=warmup_days))
            if start
            else bounds[0] + timedelta(days=warmup_days)
        )
        hi0 = min(date.fromisoformat(end), bounds[1]) if end else bounds[1]
        hi = hi0 - timedelta(days=hold_days)
        trading = [
            r[0]
            for r in self.con.execute(
                "SELECT date FROM sfa_fund_prices WHERE ticker='SPY' AND date BETWEEN ? AND ? ORDER BY date",
                [lo, hi],
            ).fetchall()
        ]
        selected, last = [], None
        for d in trading:
            if last is None or (d - last).days >= step_days:
                selected.append(d.isoformat())
                last = d
        return selected

    def snapshot(self, asof: str, strategy, cfg) -> tuple[list[dict], list[dict]]:
        enabled = ([sector["finviz"] for sector in cfg.enabled_sectors]
                   if strategy.universe_sectors_mode == "live_enabled" else [])
        sector_clause = (" AND sector IN (" + ",".join("?" for _ in enabled) + ")"
                         if enabled else "")
        frame = self.con.execute(
            f"""
        WITH u AS (
          SELECT * FROM canonical_securities
          WHERE firstpricedate<=? AND lastpricedate>=? AND sector IS NOT NULL{sector_clause}
        )
        SELECT u.security_id,u.ticker,u.company,u.sector,u.industry,u.country,u.category,u.isdelisted,u.lastpricedate,
               p.date price_date,p.close,p.closeadj,p.closeunadj,p.high,p.low,p.volume,
               p.history_sessions,p.sma50,p.sma200,p.wma200_proxy,p.avgvol50,p.resistance50,p.support126,p.close_3m,p.close_1y,p.sma50_1m_ago,
               art.datekey,art.reportperiod,art.fxusd,meta.currency reporting_currency,
               art.grossmargin,art.ros,art.opinc,art.revenue,
               art.netmargin,art.roe,art.roic,art.de,art.currentratio,art.fcf,art.assets,
               art.liabilities,art.workingcapital,art.retearn,art.ebit,
               arq.revenue revenue_q,arq.revenue_prev_q,arq.revenue_prev_y,arq.opinc opinc_q,
               arq.opinc_prev_q,arq.sharesbas sharesbas_q,
               arq.shares_prev_y,arq.grossmargin grossmargin_q,arq.grossmargin_prev_q,
               arq.debtnc debt_long_q,arq.assets assets_q,
               arq.debt_long_prev_y,arq.assets_prev_y,
               d.marketcap marketcap_daily,d.pe pe_daily,d.ps ps_daily,d.pb pb_daily
        FROM u
        JOIN LATERAL (
          SELECT * FROM sfa_price_features p0
          WHERE p0.ticker=u.ticker AND p0.date<=?
          ORDER BY p0.date DESC LIMIT 1
        ) p ON true
        LEFT JOIN LATERAL (
          SELECT * FROM sfa_art_features art0
          WHERE art0.ticker=u.ticker AND art0.datekey<=?
          ORDER BY art0.datekey DESC,art0.reportperiod DESC LIMIT 1
        ) art ON true
        LEFT JOIN LATERAL (
          SELECT t0.currency FROM sfa_tickers t0
          WHERE t0.ticker=u.ticker AND t0."table"='SF1'
          ORDER BY t0.lastupdated DESC NULLS LAST LIMIT 1
        ) meta ON true
        LEFT JOIN LATERAL (
          SELECT * FROM sfa_arq_features arq0
          WHERE arq0.ticker=u.ticker AND arq0.datekey<=?
          ORDER BY arq0.datekey DESC,arq0.reportperiod DESC LIMIT 1
        ) arq ON true
        LEFT JOIN LATERAL (
          SELECT * FROM sfa_daily d0
          WHERE d0.ticker=u.ticker AND d0.date<=?
          ORDER BY d0.date DESC LIMIT 1
        ) d ON true
         WHERE coalesce(d.marketcap * 1000000,art.marketcap)>=?
           AND p.close>=?
           AND p.close * coalesce(p.avgvol50,0)>=?
         ORDER BY coalesce(d.marketcap * 1000000,art.marketcap) DESC
        """,
            [asof, asof, *enabled, asof, asof, asof, asof,
             strategy.market_cap_min_usd, strategy.price_min_usd,
             strategy.avg_dollar_volume_min_usd],
        ).fetchdf()
        spy_3m = self._benchmark(asof, 63)
        sector_returns = {sector: self._benchmark(asof, 63, ticker)
                          for sector, ticker in _SECTOR_ETF.items()
                          if not enabled or sector in enabled}
        out = []
        for row in frame.to_dict("records"):
            out.append(
                {
                    "sec": {
                        "ticker": row["ticker"],
                        "company": row["company"],
                        "sector": row["sector"],
                        "industry": row["industry"],
                        "country": row["country"],
                        "reporting_currency": row.get("reporting_currency"),
                        "category": row["category"],
                        "security_id": row["security_id"],
                        "isdelisted": str(row["isdelisted"]).lower() in {"y", "true", "1"},
                        "lastpricedate": str(row["lastpricedate"]),
                    },
                    "metrics": _row_metrics(
                        row, asof, spy_3m, sector_returns.get(row["sector"])
                    ),
                    "currency_conversion": _fcf_currency_conversion(row),
                    "raw": row,
                }
            )
        out, _ = deduplicate_issuers(out)
        candidates = out[:strategy.universe_top_n]
        self._enrich_point_in_time(candidates, asof)
        return candidates, out

    def _enrich_point_in_time(self, items: list[dict], asof: str) -> None:
        """Add historical flow and beta factors available in the licensed bundle."""
        tickers = [item["sec"]["ticker"] for item in items]
        if not tickers:
            return
        frame = pd.DataFrame({"ticker": sorted(set(tickers))})
        self.con.register("requested_tickers", frame)
        start_400 = (date.fromisoformat(asof) - timedelta(days=400)).isoformat()
        betas = dict(self.con.execute("""
          WITH stock0 AS (
            SELECT p.ticker,p.date,p.closeadj,
                   lag(p.closeadj) OVER (PARTITION BY p.ticker ORDER BY p.date) prev_close
            FROM sfa_prices p JOIN requested_tickers r USING(ticker)
            WHERE p.date BETWEEN ? AND ?
          ), stock AS (
            SELECT ticker,date,closeadj/prev_close-1 ret FROM stock0 WHERE prev_close>0
          ), spy0 AS (
            SELECT date,closeadj,lag(closeadj) OVER (ORDER BY date) prev_close
            FROM sfa_fund_prices WHERE ticker='SPY' AND date BETWEEN ? AND ?
          ), spy AS (SELECT date,closeadj/prev_close-1 ret FROM spy0 WHERE prev_close>0)
          SELECT ticker,covar_samp(stock.ret,spy.ret)/nullif(var_samp(spy.ret),0) beta
          FROM stock JOIN spy USING(date) GROUP BY ticker HAVING count(*)>=126
        """, [start_400, asof, start_400, asof]).fetchall())
        insider_start = (date.fromisoformat(asof) - timedelta(days=180)).isoformat()
        insider_rows = self.con.execute("""
          SELECT i.ticker,count(*) FILTER (WHERE transactioncode='P') buy_count,
                 coalesce(sum(abs(transactionvalue)) FILTER (WHERE transactioncode='P'),0) total_buy,
                 count(DISTINCT ownername) FILTER (WHERE transactioncode='P' AND transactiondate>=?::DATE-INTERVAL 90 DAY) buyers_90,
                 coalesce(sum(abs(transactionvalue)) FILTER (
                   WHERE transactioncode='P' AND (lower(coalesce(officertitle,'')) SIMILAR TO '%(ceo|cfo|coo|chief|president|chairman)%')),0) top_exec_buy,
                 max(transactiondate) FILTER (WHERE transactioncode='P') last_buy
          FROM sfa_insiders i JOIN requested_tickers r USING(ticker)
          WHERE filingdate BETWEEN ? AND ? GROUP BY i.ticker
        """, [asof, insider_start, asof]).fetchall()
        insiders = {}
        caps = {item["sec"]["ticker"]: item["metrics"].get("market_cap", {}).get("value")
                for item in items}
        for ticker, buy_count, total_buy, buyers_90, top_exec, last_buy in insider_rows:
            cap = caps.get(ticker) or 0
            pct = total_buy / cap * 100 if cap else 0
            rel = 12 if pct >= 1 else 9 if pct >= .25 else 6 if pct >= .05 else 3 if pct >= .01 else 1
            absolute = 12 if total_buy >= 1e6 else 9 if total_buy >= 250000 else 6 if total_buy >= 50000 else 3 if total_buy >= 10000 else 1
            materiality = max(rel, absolute) if buy_count else 0
            materiality += 5 if top_exec >= 1e6 else 3 if top_exec >= 250000 else 1 if top_exec > 0 else 0
            materiality += 4 if buyers_90 >= 3 else 0
            if last_buy:
                days = (date.fromisoformat(asof) - last_buy).days
                materiality += 3 if days <= 30 else 1 if days <= 90 else 0
            insiders[ticker] = 45 + min(materiality, 25) / 25 * 55
        holding_rows = self.con.execute("""
          SELECT ticker,date,shrunits,shrholders FROM sfa_holdings_by_ticker h
          JOIN requested_tickers r USING(ticker) WHERE date<=?
          QUALIFY row_number() OVER (PARTITION BY ticker ORDER BY date DESC)<=2
          ORDER BY ticker,date DESC
        """, [asof]).fetchall()
        holdings = {}
        for ticker, d, units, holders in holding_rows:
            holdings.setdefault(ticker, []).append((d, units, holders))
        self.con.unregister("requested_tickers")
        for item in items:
            ticker, metrics = item["sec"]["ticker"], item["metrics"]
            if ticker in betas:
                metrics["beta"] = _metric(betas[ticker], "sharadar_sep_vs_spy", asof)
            if ticker in insiders:
                metrics["insider_score"] = _metric(insiders[ticker], "sharadar_sf2", asof)
            history = holdings.get(ticker, [])
            if len(history) == 2 and history[1][1]:
                change = (history[0][1] / history[1][1] - 1) * 100
                metrics["inst_trans"] = _metric(change, "sharadar_sf3a", history[0][0])
                breadth_change = (history[0][2] or 0) - (history[1][2] or 0)
                proxy = max(0, min(100, 50 + change * 2 + breadth_change / 10))
                metrics["thirteenf_score"] = _metric(
                    proxy, "sharadar_sf3a_breadth_proxy", history[0][0]
                )

    def prices_after(
        self, tickers: list[str], start: str, end: str
    ) -> dict[str, list[dict]]:
        if not tickers:
            return {}
        frame = pd.DataFrame({"ticker": sorted(set(tickers))})
        self.con.register("requested_tickers", frame)
        rows = self.con.execute(
            """
          SELECT p.ticker,p.date,p.close,p.closeadj FROM sfa_prices p
          JOIN requested_tickers r USING(ticker) WHERE p.date BETWEEN ? AND ? ORDER BY p.ticker,p.date
        """,
            [start, end],
        ).fetchall()
        self.con.unregister("requested_tickers")
        out = {}
        for ticker, d, close, closeadj in rows:
            out.setdefault(ticker, []).append(
                {
                    "date": d.isoformat(),
                    "close": float(close),
                    "closeadj": float(closeadj) if closeadj else float(close),
                }
            )
        return out

    def fixed_outcomes(
        self,
        tickers: list[str],
        decision: str,
        horizons: tuple[int, ...],
        next_close: bool,
        *,
        tuning_horizon_days: int = 365,
        tuning_primary_gain_pct: float = 25,
        tuning_secondary_gain_pct: float = 30,
        entry_cost_bps: float = 0,
        exit_cost_bps: float = 0,
    ) -> dict[str, dict]:
        """Fetch entry and fixed-horizon outcomes in one grouped warehouse scan."""
        if not tickers:
            return {}
        frame = pd.DataFrame({"ticker": sorted(set(tickers))})
        self.con.register("requested_tickers", frame)
        op = ">" if next_close else ">="
        expressions = [
            "arg_max(p.date,p.date) last_date",
            "arg_max(p.close,p.date) last_close",
            "arg_max(p.closeadj,p.date) last_closeadj",
        ]
        entry_multiplier = 1 + entry_cost_bps / 10000
        exit_multiplier = 1 - exit_cost_bps / 10000
        net_return = (
            f"(((coalesce(p.closeadj,p.close)*{exit_multiplier:.12f})/"
            f"(coalesce(e.entry_closeadj,e.entry_close)*{entry_multiplier:.12f})-1)*100)"
        )
        tuning_end = f"e.entry_date+INTERVAL {int(tuning_horizon_days)} DAY"
        expressions.extend([
            f"min(p.date) FILTER (WHERE p.date<={tuning_end} AND {net_return}>={float(tuning_primary_gain_pct)}) tuning_hit_primary_date",
            f"min(p.date) FILTER (WHERE p.date<={tuning_end} AND {net_return}>={float(tuning_secondary_gain_pct)}) tuning_hit_secondary_date",
            f"max({net_return}) FILTER (WHERE p.date<={tuning_end}) tuning_max_return_pct",
            f"min({net_return}) FILTER (WHERE p.date<={tuning_end}) tuning_max_drawdown_pct",
        ])
        for horizon in horizons:
            expressions.extend(
                [
                    f"arg_min(p.date,p.date) FILTER (WHERE p.date>=e.entry_date+INTERVAL {int(horizon)} DAY) date_{horizon}",
                    f"arg_min(p.close,p.date) FILTER (WHERE p.date>=e.entry_date+INTERVAL {int(horizon)} DAY) close_{horizon}",
                    f"arg_min(p.closeadj,p.date) FILTER (WHERE p.date>=e.entry_date+INTERVAL {int(horizon)} DAY) closeadj_{horizon}",
                ]
            )
        maximum = max(max(horizons), int(tuning_horizon_days))
        entry_search_end = (date.fromisoformat(decision) + timedelta(days=14)).isoformat()
        end = (date.fromisoformat(decision) + timedelta(days=maximum + 28)).isoformat()
        query = f"""
          WITH e AS (
            SELECT p.ticker,arg_min(p.date,p.date) entry_date,
                   arg_min(p.close,p.date) entry_close,
                   arg_min(p.closeadj,p.date) entry_closeadj
            FROM sfa_prices p JOIN requested_tickers r USING(ticker)
            WHERE p.date{op}? AND p.date<=? GROUP BY p.ticker
          )
          SELECT e.ticker,e.entry_date,e.entry_close,e.entry_closeadj,{",".join(expressions)}
          FROM e JOIN sfa_prices p ON p.ticker=e.ticker
          WHERE p.date BETWEEN e.entry_date AND ?
          GROUP BY e.ticker,e.entry_date,e.entry_close,e.entry_closeadj
        """
        result = self.con.execute(query, [decision, entry_search_end, end])
        columns = [d[0] for d in result.description]
        rows = {row[0]: dict(zip(columns, row)) for row in result.fetchall()}
        self.con.unregister("requested_tickers")
        return rows

    def benchmark_return(self, entry: str, exit_: str) -> float | None:
        rows = self.con.execute(
            """
          SELECT arg_min(closeadj, date) FILTER (WHERE date>=? AND date<=?) p0,
                 arg_min(closeadj, date) FILTER (WHERE date>=?) p1
          FROM sfa_fund_prices WHERE ticker='SPY' AND date BETWEEN ? AND ?
        """,
            [
                entry,
                exit_,
                exit_,
                entry,
                (date.fromisoformat(exit_) + timedelta(days=7)).isoformat(),
            ],
        ).fetchone()
        return (rows[1] / rows[0] - 1) * 100 if rows and rows[0] and rows[1] else None

    def terminal_events(self, tickers: list[str], start: str, end: str) -> dict[str, dict]:
        if not tickers:
            return {}
        frame = pd.DataFrame({"ticker": sorted(set(tickers))})
        self.con.register("requested_tickers", frame)
        rows = self.con.execute("""
          SELECT a.ticker,a.date,a.action,a.value,a.contraticker
          FROM sfa_actions a JOIN requested_tickers r USING(ticker)
          WHERE a.date BETWEEN ? AND ? AND a.action IN (
            'delisted','regulatorydelisting','voluntarydelisting',
            'bankruptcyliquidation','acquisitionby','mergerto')
          QUALIFY row_number() OVER (
            PARTITION BY a.ticker
            ORDER BY
              CASE WHEN a.action='delisted' THEN 1 ELSE 0 END,
              a.date,
              CASE a.action
                WHEN 'bankruptcyliquidation' THEN 1
                WHEN 'acquisitionby' THEN 2
                WHEN 'mergerto' THEN 3
                WHEN 'regulatorydelisting' THEN 4
                WHEN 'voluntarydelisting' THEN 5
                ELSE 9
              END
          )=1
        """, [start, end]).fetchall()
        self.con.unregister("requested_tickers")
        return {ticker: {"date": d.isoformat(), "action": action, "value": value,
                         "successor": successor if successor not in {None, "N/A"} else None,
                         "terminal_return_policy": ("zero" if action == "bankruptcyliquidation"
                                                    else "last_total_return_close")}
                for ticker, d, action, value, successor in rows}

    def market_regime(self, asof: str) -> str:
        rows = self.con.execute(
            """
          SELECT date,closeadj FROM sfa_fund_prices WHERE ticker='SPY' AND date<=?
          ORDER BY date DESC LIMIT 253
        """,
            [asof],
        ).fetchall()
        if len(rows) < 200:
            return "insufficient_history"
        current = rows[0][1]
        sma200 = statistics.mean(r[1] for r in rows[:200] if r[1])
        one_year = rows[-1][1] if len(rows) >= 252 else rows[-1][1]
        ret = current / one_year - 1 if current and one_year else 0
        if current >= sma200 and ret >= 0:
            return "bull"
        if current < sma200 and ret < 0:
            return "bear"
        return "transition"


def run_sfa_rolling(
    warehouse,
    cfg,
    *,
    hold_days=30,
    step_days=30,
    warmup_days=300,
    min_names=20,
    start=None,
    end=None,
    settings=None,
    strategy=None,
    bootstrap=1000,
    include_evidence=True,
    progress=None,
) -> dict:
    strategy = strategy or load_strategy(
        Path(__file__).resolve().parents[2] / "config" / "backtest_sfa.yaml"
    )
    if settings is None:
        settings = {
            key: value.get("default")
            for key, value in cfg.defaults.get("settings", {}).items()
            if key in {"margin_of_safety_pct", "target_upside_pct"}
            and isinstance(value, dict) and value.get("default") is not None
        }
    replay = SFAReplay(warehouse)
    entries = replay.cohort_dates(start, end, step_days, warmup_days, hold_days)
    expected_fields = {
        item["metric"] for category in cfg.categories.values()
        for item in category["items"]
    } | {"price", "fwd_pe", "ps_ratio", "pb_ratio", "pfcf_ratio",
         "perf_year", "gross_margin", "debt_eq", "rev_growth_qoq"}
    alpha = {"Buy": [], "Watch": [], "Avoid": []}
    raw = {k: [] for k in alpha}
    per_obs, cohorts, observations = {}, [], []
    strategy_alpha: dict = {}
    strategy_raw: dict = {}
    rejected_coverage = 0
    benchmark_cache: dict[tuple[str, str], float | None] = {}
    universe_audit = {
        "eligible_issuers": 0,
        "top_n_issuers": 0,
        "screened_issuers": 0,
    }
    for entry in entries:
        decision = date.fromisoformat(entry)
        nominal_exit = (decision + timedelta(days=hold_days)).isoformat()
        snapshot, median_universe = replay.snapshot(entry, strategy, cfg)
        universe_audit["eligible_issuers"] += len(median_universe)
        universe_audit["top_n_issuers"] += len(snapshot)
        for item in median_universe:
            item["metrics"] = metrics_for_policy(item["metrics"], strategy)
        filtered = []
        for item in snapshot:
            item["metrics"] = metrics_for_policy(item["metrics"], strategy)
            memberships, screening = _screen_memberships(item["metrics"])
            if strategy.screened_only and not memberships:
                continue
            item.update({"memberships": memberships, "screening": screening})
            filtered.append(item)
        universe_audit["screened_issuers"] += len(filtered)
        medians = medians_from(
            cfg, [(x["sec"]["sector"], x["metrics"]) for x in median_universe]
        )
        fixed_horizons = tuple(sorted(set(strategy.horizons_days + (hold_days,))))
        fixed = replay.fixed_outcomes(
            [x["sec"]["ticker"] for x in filtered],
            entry,
            fixed_horizons,
            strategy.entry == "next_close",
            tuning_horizon_days=strategy.tuning_horizon_days,
            tuning_primary_gain_pct=strategy.tuning_primary_gain_pct,
            tuning_secondary_gain_pct=strategy.tuning_secondary_gain_pct,
            entry_cost_bps=strategy.slippage_bps + strategy.transaction_cost_bps,
            exit_cost_bps=strategy.exit_slippage_bps + strategy.exit_transaction_cost_bps,
        )
        evidence_end = (decision + timedelta(days=max(1853, max(strategy.horizons_days) + 28))).isoformat()
        terminals = replay.terminal_events(
            [x["sec"]["ticker"] for x in filtered], entry, evidence_end
        )
        cohort_rows, cohort_obs = [], []
        evidence_work = []
        regime = replay.market_regime(entry)
        for item in filtered:
            sec, metrics, memberships = (
                item["sec"],
                item["metrics"],
                item["memberships"],
            )
            prices = fixed.get(sec["ticker"])
            if not prices or not prices.get("entry_close"):
                continue
            p0row = {
                "date": prices["entry_date"].isoformat(),
                "close": float(prices["entry_close"]),
                "closeadj": float(prices["entry_closeadj"] or prices["entry_close"]),
            }
            terminal = terminals.get(sec["ticker"])
            if not terminal and sec.get("isdelisted") and sec.get("lastpricedate"):
                terminal = {"date": sec["lastpricedate"], "action": "delisted_inferred",
                            "value": None, "successor": None,
                            "terminal_return_policy": "last_total_return_close"}
            entry_nominal_exit = (
                date.fromisoformat(p0row["date"]) + timedelta(days=hold_days)
            ).isoformat()
            delisted_exit = False
            if prices.get(f"close_{hold_days}"):
                p1row = {
                    "date": prices[f"date_{hold_days}"].isoformat(),
                    "close": float(prices[f"close_{hold_days}"]),
                    "closeadj": float(
                        prices[f"closeadj_{hold_days}"] or prices[f"close_{hold_days}"]
                    ),
                }
            elif (
                sec.get("lastpricedate")
                and sec["lastpricedate"] <= entry_nominal_exit
                and prices.get("last_close")
            ):
                p1row = {
                    "date": prices["last_date"].isoformat(),
                    "close": float(prices["last_close"]),
                    "closeadj": float(prices["last_closeadj"] or prices["last_close"]),
                }
                delisted_exit = True
                if terminal and terminal["terminal_return_policy"] == "zero":
                    p1row.update({"close": 0.0, "closeadj": 0.0,
                                  "date": terminal["date"]})
            else:
                continue
            primary = _live_primary_strategy(memberships)
            scoring_settings, preset_name = _settings_for_strategy(
                cfg, settings, primary
            )
            rec = score_ticker(cfg, sec, metrics, medians, scoring_settings)
            if rec.get("factor_coverage_pct", 0) < strategy.minimum_coverage_pct:
                rejected_coverage += 1
                continue
            adjusted_entry = p0row["close"] * (
                1 + (strategy.slippage_bps + strategy.transaction_cost_bps) / 10000
            )
            entry_cost = (strategy.slippage_bps + strategy.transaction_cost_bps) / 10000
            exit_cost = (strategy.exit_slippage_bps + strategy.exit_transaction_cost_bps) / 10000
            raw_return = ((p1row["closeadj"] * (1 - exit_cost)) /
                          (p0row["closeadj"] * (1 + entry_cost)) - 1) * 100
            benchmark_key = (p0row["date"], p1row["date"])
            if benchmark_key not in benchmark_cache:
                benchmark_cache[benchmark_key] = replay.benchmark_return(*benchmark_key)
            observation_benchmark = benchmark_cache[benchmark_key]
            if observation_benchmark is None:
                continue
            targets = targets_for(rec, metrics, strategy) if include_evidence else {}
            horizon_results = {}
            for horizon in strategy.horizons_days:
                close = prices.get(f"close_{horizon}")
                closeadj = prices.get(f"closeadj_{horizon}")
                observed = prices.get(f"date_{horizon}")
                if close and closeadj and observed:
                    horizon_exit = observed.isoformat()
                    horizon_return = ((float(closeadj) * (1 - exit_cost)) /
                                      (p0row["closeadj"] * (1 + entry_cost)) - 1) * 100
                    key = (p0row["date"], horizon_exit)
                    if key not in benchmark_cache:
                        benchmark_cache[key] = replay.benchmark_return(*key)
                    horizon_benchmark = benchmark_cache[key]
                    horizon_results[str(horizon)] = {
                        "date": horizon_exit,
                        "price": round(float(close), 2),
                        "return_pct": round(horizon_return, 2),
                        "benchmark_return_pct": round(horizon_benchmark, 2) if horizon_benchmark is not None else None,
                        "alpha_pct": round(horizon_return - horizon_benchmark, 2) if horizon_benchmark is not None else None,
                    }
                elif terminal and terminal["date"] <= (
                    date.fromisoformat(p0row["date"]) + timedelta(days=horizon)
                ).isoformat():
                    terminal_return = (-100.0 if terminal["terminal_return_policy"] == "zero"
                                       else raw_return)
                    key = (p0row["date"], terminal["date"])
                    if key not in benchmark_cache:
                        benchmark_cache[key] = replay.benchmark_return(*key)
                    horizon_benchmark = benchmark_cache[key]
                    horizon_results[str(horizon)] = {
                        "date": terminal["date"],
                        "price": 0 if terminal["terminal_return_policy"] == "zero" else round(p1row["close"], 2),
                        "return_pct": round(terminal_return, 2),
                        "benchmark_return_pct": round(horizon_benchmark, 2) if horizon_benchmark is not None else None,
                        "alpha_pct": round(terminal_return - horizon_benchmark, 2) if horizon_benchmark is not None else None,
                        "status": "closed_at_terminal_event",
                        "terminal_action": terminal["action"],
                    }
                else:
                    horizon_results[str(horizon)] = {
                        "status": "insufficient_forward_history"
                    }
            cohort_rows.append({"verdict": rec["verdict"], "return": raw_return,
                                "benchmark": observation_benchmark, "strategy": primary,
                                "security_id": sec["security_id"], "ticker": sec["ticker"]})
            outcome = {
                "status": "fixed_horizons",
                "horizons": horizon_results,
                "targets": {},
            }
            observation = {
                "observation_id": f"{entry}:{sec['security_id']}",
                "ticker": sec["ticker"],
                "security_id": sec["security_id"],
                "issuer_key": item.get("issuer_key") or issuer_key(
                    sec.get("company"), sec.get("ticker")
                ),
                "excluded_share_classes": item.get("excluded_share_classes", []),
                "company": sec["company"],
                "sector": sec["sector"],
                "country": (item.get("currency_conversion") or {}).get("country"),
                "currency_conversion": item.get("currency_conversion"),
                "strategy_key": primary,
                "strategy_memberships": memberships,
                "preset_name": preset_name,
                "regime": regime,
                "decision_date": entry,
                "entry_date": p0row["date"],
                "entry_price": round(adjusted_entry, 2),
                "exit_date": p1row["date"],
                "delisted_exit": delisted_exit,
                "terminal_event": terminal,
                "raw_close": round(p0row["close"], 2),
                "avg_dollar_volume": round(
                    float(item["raw"]["close"]) * float(item["raw"]["avgvol50"]), 2
                ) if item["raw"].get("close") is not None
                and item["raw"].get("avgvol50") is not None else None,
                "_entry_closeadj": p0row["closeadj"],
                "verdict": rec["verdict"],
                "score": rec["score"],
                "coverage_pct": rec["factor_coverage_pct"],
                "category_coverage_pct": rec["coverage_pct"],
                "screening": item["screening"],
                "weights": scoring_settings.get("weights")
                or {cid: c["weight"] for cid, c in cfg.categories.items()},
                "thresholds": cfg.verdict_bands,
                "vetoes": rec["vetoes"],
                "context_warnings": rec.get("context_warnings", []),
                "soft_gates": rec["soft_gates"],
                "growth_qualification": rec.get("growth_qualification"),
                "debt_direction": rec.get("research_metrics"),
                "categories": _compact_categories(rec["categories"]),
                "valuation": _compact_valuation(rec["valuation"]),
                "targets": targets,
                "practical_target": outcome.get("targets", {}).get("practical"),
                "data_quality": quality_for(metrics, expected_fields),
                "execution": {
                    "entry_cost_bps": strategy.slippage_bps + strategy.transaction_cost_bps,
                    "exit_cost_bps": strategy.exit_slippage_bps + strategy.exit_transaction_cost_bps,
                    "benchmark_entry_date": p0row["date"],
                    "benchmark_exit_date": p1row["date"],
                },
                "outcome": outcome,
                "horizons": outcome.get("horizons", {}),
            }
            def elapsed_days(observed):
                return (
                    (observed - date.fromisoformat(p0row["date"])).days
                    if observed is not None else None
                )

            terminal_days = (
                (date.fromisoformat(str(terminal["date"])[:10])
                 - date.fromisoformat(p0row["date"])).days
                if terminal and terminal.get("date") else None
            )
            horizon_outcome = horizon_results.get(str(strategy.tuning_horizon_days), {})
            max_drawdown = prices.get("tuning_max_drawdown_pct")
            if (terminal and terminal.get("terminal_return_policy") == "zero"
                    and terminal_days is not None
                    and terminal_days <= strategy.tuning_horizon_days):
                max_drawdown = min(float(max_drawdown or 0), -100.0)
            observation["_tuning_outcome"] = {
                "horizon_days": strategy.tuning_horizon_days,
                "primary_gain_pct": strategy.tuning_primary_gain_pct,
                "secondary_gain_pct": strategy.tuning_secondary_gain_pct,
                "first_hit_primary_days": elapsed_days(prices.get("tuning_hit_primary_date")),
                "first_hit_secondary_days": elapsed_days(prices.get("tuning_hit_secondary_date")),
                "last_observed_days": elapsed_days(prices.get("last_date")),
                "terminal_days": terminal_days,
                "return_pct": horizon_outcome.get("return_pct"),
                "alpha_pct": horizon_outcome.get("alpha_pct"),
                "max_return_pct": round(float(prices["tuning_max_return_pct"]), 2)
                if prices.get("tuning_max_return_pct") is not None else None,
                "max_drawdown_pct": round(float(max_drawdown), 2)
                if max_drawdown is not None else None,
            }
            cohort_obs.append(observation)
            if include_evidence and rec["verdict"] == "Buy":
                evidence_work.append(
                    (observation, targets, adjusted_entry, p0row["date"], terminal)
                )
        if len(cohort_rows) < min_names:
            continue
        if include_evidence and evidence_work:
            buy_paths = replay.prices_after(
                [row[0]["ticker"] for row in evidence_work], entry, evidence_end
            )
            for (
                observation,
                targets,
                adjusted_entry,
                entry_date,
                terminal,
            ) in evidence_work:
                series = [
                    {"date": p["date"], "close": p["close"]}
                    for p in buy_paths.get(observation["ticker"], [])
                    if p["date"] >= entry_date
                ]
                portfolio_end = (
                    date.fromisoformat(entry_date) + timedelta(days=hold_days)
                ).isoformat()
                observation["_daily_returns"] = [
                    {
                        "date": point["date"],
                        "return_pct": round(
                            ((point["closeadj"] * (1 - exit_cost)) /
                             (observation["_entry_closeadj"] * (1 + entry_cost)) - 1) * 100,
                            4,
                        ),
                    }
                    for point in buy_paths.get(observation["ticker"], [])
                    if entry_date <= point["date"] <= portfolio_end
                ]
                target_outcome = evaluate_path(
                    series, adjusted_entry, targets, strategy.horizons_days, entry_date,
                    terminal_date=terminal.get("date") if terminal else None,
                    terminal_event=terminal,
                )
                target_outcome.pop("path", None)
                target_outcome["horizons"] = observation["horizons"]
                observation["return_milestones"] = fixed_return_milestones(
                    buy_paths.get(observation["ticker"], []),
                    observation["_entry_closeadj"],
                    entry_date,
                    entry_cost_bps=(strategy.slippage_bps + strategy.transaction_cost_bps),
                    exit_cost_bps=(strategy.exit_slippage_bps + strategy.exit_transaction_cost_bps),
                    terminal_date=terminal.get("date") if terminal else None,
                    terminal_event=terminal,
                )
                observation["outcome"] = target_outcome
                observation["practical_target"] = target_outcome.get("targets", {}).get(
                    "practical"
                )
        if strategy.benchmark == "cohort_mean":
            for horizon in strategy.horizons_days:
                available = [row.get("horizons", {}).get(str(horizon), {})
                             for row in cohort_obs]
                returns_at_horizon = [row.get("return_pct") for row in available
                                      if isinstance(row.get("return_pct"), (int, float))]
                if not returns_at_horizon:
                    continue
                cohort_benchmark = statistics.mean(returns_at_horizon)
                for result in available:
                    if isinstance(result.get("return_pct"), (int, float)):
                        result["benchmark_return_pct"] = round(cohort_benchmark, 2)
                        result["alpha_pct"] = round(result["return_pct"] - cohort_benchmark, 2)
        observations.extend(cohort_obs)
        per_obs[entry] = []
        c_alpha = {k: [] for k in alpha}
        for row in cohort_rows:
            verdict, ret, strategy_key = row["verdict"], row["return"], row["strategy"]
            base = (
                statistics.mean(candidate["return"] for candidate in cohort_rows)
                if strategy.benchmark == "cohort_mean"
                else row["benchmark"]
            )
            a = ret - base
            alpha[verdict].append(a)
            raw[verdict].append(ret)
            c_alpha[verdict].append(a)
            per_obs[entry].append((verdict, a))
            skey = strategy_key or "unscreened"
            strategy_alpha.setdefault(skey, {}).setdefault(verdict, []).append(a)
            strategy_raw.setdefault(skey, {}).setdefault(verdict, []).append(ret)
        cohorts.append(
            {
                "entry": entry,
                "exit": nominal_exit,
                "n": len(cohort_rows),
                "regime": regime,
                "mkt_return_pct": round(statistics.mean(
                    row["benchmark"] for row in cohort_rows
                    if row["benchmark"] is not None), 2),
                "buy_alpha_pct": round(statistics.mean(c_alpha["Buy"]), 2)
                if c_alpha["Buy"]
                else None,
                "buy_n": len(c_alpha["Buy"]),
            }
        )
        if progress:
            progress({"accepted_cohorts": len(cohorts), "decision_date": entry,
                      "accepted_observations": len(observations),
                      "scheduled_cohorts": len(entries)})
    if not cohorts:
        return {
            "ok": False,
            "reason": "no SFA cohort had sufficient covered candidates",
        }

    def stats(vals):
        return {
            "n": len(vals),
            "mean_alpha_pct": round(statistics.mean(vals), 2),
            "median_alpha_pct": round(statistics.median(vals), 2),
            "hit_rate_pct": round(sum(v > 0 for v in vals) / len(vals) * 100, 1),
        }

    by_verdict = {
        v: {**stats(a), "mean_raw_return_pct": round(statistics.mean(raw[v]), 2)}
        for v, a in alpha.items()
        if a
    }
    by_strategy = {}
    for skey, verdicts in strategy_alpha.items():
        summary = {
            v: {
                **stats(vals),
                "mean_raw_return_pct": round(statistics.mean(strategy_raw[skey][v]), 2),
            }
            for v, vals in verdicts.items()
            if vals
        }
        b = summary.get("Buy", {}).get("mean_alpha_pct")
        a = summary.get("Avoid", {}).get("mean_alpha_pct")
        by_strategy[skey] = {
            "preset": cfg.defaults.get("strategy_presets", {}).get(skey),
            "by_verdict": summary,
            "buy_minus_avoid_pct": round(b - a, 2)
            if b is not None and a is not None
            else None,
        }
    horizon_summary = {}
    for horizon in strategy.horizons_days:
        values = {verdict: [] for verdict in alpha}
        returns = {verdict: [] for verdict in alpha}
        blocks = {}
        strategy_values = {}
        for observation in observations:
            result = observation.get("horizons", {}).get(str(horizon), {})
            value = result.get("alpha_pct")
            raw_value = result.get("return_pct")
            verdict = observation.get("verdict")
            if verdict not in values or not isinstance(value, (int, float)):
                continue
            values[verdict].append(value)
            if isinstance(raw_value, (int, float)):
                returns[verdict].append(raw_value)
            blocks.setdefault(observation["decision_date"], []).append((verdict, value))
            skey = observation.get("strategy_key") or "unscreened"
            strategy_values.setdefault(skey, {}).setdefault(verdict, []).append(value)
        summary = {verdict: {**stats(vals),
                            "mean_raw_return_pct": round(statistics.mean(returns[verdict]), 2)
                            if returns[verdict] else None}
                   for verdict, vals in values.items() if vals}
        hbuy = summary.get("Buy", {}).get("mean_alpha_pct")
        hwatch = summary.get("Watch", {}).get("mean_alpha_pct")
        havoid = summary.get("Avoid", {}).get("mean_alpha_pct")
        hci = (_cohort_bootstrap_spread(observations, horizon, bootstrap)
               if bootstrap and blocks else None)
        horizon_summary[str(horizon)] = {
            "calendar_days": horizon,
            "approximately_months": round(horizon / 30.4375, 1),
            "by_verdict": summary,
            "buy_minus_avoid_pct": round(hbuy - havoid, 2)
            if hbuy is not None and havoid is not None else None,
            "spread_ci90_cohort": hci,
            "spread_ci90_two_way": _two_way_bootstrap_spread(
                observations, horizon, bootstrap
            ) if bootstrap else None,
            "significant": bool(hci and hci[0] > 0),
            "monotonic": (hbuy >= hwatch >= havoid)
            if None not in (hbuy, hwatch, havoid) else None,
            "by_strategy": {
                skey: {verdict: stats(vals) for verdict, vals in verdicts.items() if vals}
                for skey, verdicts in strategy_values.items()
            },
        }
    buy = by_verdict.get("Buy", {}).get("mean_alpha_pct")
    avoid = by_verdict.get("Avoid", {}).get("mean_alpha_pct")
    watch = by_verdict.get("Watch", {}).get("mean_alpha_pct")
    spread = round(buy - avoid, 2) if buy is not None and avoid is not None else None
    ci = _cohort_bootstrap_spread(observations, hold_days, bootstrap) if bootstrap else None
    manifest = dict(
        warehouse.con.execute("SELECT key,value FROM sfa_manifest").fetchall()
    )
    try:
        git_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2],
            text=True, stderr=subprocess.DEVNULL
        ).strip()
        git_dirty = bool(subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=Path(__file__).resolve().parents[2],
            text=True, stderr=subprocess.DEVNULL
        ).strip())
    except Exception:
        git_commit, git_dirty = None, None
    libraries = {}
    for package in ("duckdb", "numpy", "pandas", "PyYAML"):
        try:
            libraries[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            libraries[package] = "unknown"
    runtime = {
        "hold_days": hold_days, "step_days": step_days, "warmup_days": warmup_days,
        "minimum_cohort_size": min_names, "bootstrap_samples": bootstrap,
        "include_evidence": include_evidence, "settings": settings,
        "accepted_entries": [cohort["entry"] for cohort in cohorts],
        "python": sys.version.split()[0],
        "libraries": libraries,
        "git_commit": git_commit, "git_dirty": git_dirty,
    }
    run_basis = {
        "snapshot": manifest.get("snapshot_id"),
        "strategy": strategy.strategy_id,
        "implementation": _implementation_fingerprint(),
        "runtime": runtime,
    }
    years = {}
    for obs in observations:
        years.setdefault(obs["entry_date"][:4], 0)
        years[obs["entry_date"][:4]] += 1
    regime_summary = {}
    for cohort in cohorts:
        row = regime_summary.setdefault(
            cohort["regime"], {"cohorts": 0, "buy_alpha": []}
        )
        row["cohorts"] += 1
        if cohort["buy_alpha_pct"] is not None:
            row["buy_alpha"].append(cohort["buy_alpha_pct"])
    regime_summary = {
        k: {
            "cohorts": v["cohorts"],
            "mean_buy_alpha_pct": round(statistics.mean(v["buy_alpha"]), 2)
            if v["buy_alpha"]
            else None,
        }
        for k, v in regime_summary.items()
    }
    buy_cohort_returns = []
    no_buy_periods = positions_traded = 0
    wealth, peak, max_drawdown = 1.0, 1.0, 0.0
    for cohort in cohorts:
        matching = sorted([
            o
            for o in observations
            if o["decision_date"] == cohort["entry"] and o["verdict"] == "Buy"
        ], key=lambda row: (-row.get("score", 0), row["ticker"]))[:strategy.portfolio_max_positions]
        positions_traded += len(matching)
        vals = [
            o.get("horizons", {}).get(str(hold_days), {}).get("return_pct")
            for o in matching
        ]
        vals = [v for v in vals if isinstance(v, (int, float))]
        value = statistics.mean(vals) / 100 if vals else 0.0
        no_buy_periods += not vals
        buy_cohort_returns.append(value)
        cohort_start_wealth = wealth
        daily_dates = sorted({point["date"] for row in matching
                              for point in row.get("_daily_returns", [])})
        for current_date in daily_dates:
            cumulative = []
            for row in matching:
                eligible = [point["return_pct"] for point in row.get("_daily_returns", [])
                            if point["date"] <= current_date]
                if eligible:
                    cumulative.append(eligible[-1] / 100)
            if cumulative:
                daily_wealth = cohort_start_wealth * (1 + statistics.mean(cumulative))
                peak = max(peak, daily_wealth)
                max_drawdown = min(max_drawdown, daily_wealth / peak - 1)
        wealth *= 1 + value
        peak = max(peak, wealth)
        max_drawdown = min(max_drawdown, wealth / peak - 1)
    mean_r = statistics.mean(buy_cohort_returns) if buy_cohort_returns else None
    sd_r = statistics.stdev(buy_cohort_returns) if len(buy_cohort_returns) > 1 else None
    periods_per_year = 365 / step_days
    portfolio_summary = {
        "cohort_periods": len(buy_cohort_returns),
        "cash_periods": no_buy_periods,
        "positions_traded": positions_traded,
        "max_positions_per_cohort": strategy.portfolio_max_positions,
        "allocation": "equal_weight_top_score",
        "entry_and_exit_costs_included": True,
        "overlapping": step_days < hold_days,
        "compounded_return_pct": round((wealth - 1) * 100, 2)
        if buy_cohort_returns
        else None,
        "max_drawdown_pct": round(max_drawdown * 100, 2)
        if buy_cohort_returns
        else None,
        "annualized_sharpe_approx": round(
            mean_r / sd_r * math.sqrt(periods_per_year), 2
        )
        if sd_r
        else None,
        "drawdown_frequency": "daily_position_paths" if include_evidence else "cohort_endpoints",
    }
    return {
        "ok": True,
        "run_id": "sfa-"
        + hashlib.sha256(json.dumps(run_basis, sort_keys=True).encode()).hexdigest()[
            :12
        ],
        "strategy": strategy.to_dict(),
        "implementation_fingerprint": run_basis["implementation"],
        "runtime_contract": runtime,
        "provider": "Nasdaq Data Link / Sharadar SFA",
        "snapshot_id": manifest.get("snapshot_id"),
        "data_quality": {
            "warning": "Point-in-time SFA replay; analyst/news/forensic fields remain unavailable historically.",
            "universe_bias": "active_and_delisted_point_in_time",
            "source_policy": strategy.data_quality_mode,
            "minimum_coverage_pct": strategy.minimum_coverage_pct,
            "rejected_for_coverage": rejected_coverage,
            "expected_factor_count": len(expected_fields),
        },
        "universe_audit": universe_audit,
        "contract_capabilities": {
            "entry": ["snapshot_close", "next_close"],
            "target_hit": ["close"],
            "benchmark": ["cohort_mean", "spy_total_return"],
            "data_quality": ["strict"],
            "unsupported_values": "rejected_at_strategy_load",
        },
        "survivorship_control": {
            "seeded_universe_biased": False,
            "status": "historical active/delisted SFA universe",
        },
        "issuer_deduplication": {
            "enabled": True,
            "policy": "primary_class_then_liquidity",
            "count_basis": "one_representative_security_per_issuer_per_cohort",
        },
        "hold_days": hold_days,
        "step_days": step_days,
        "warmup_days": warmup_days,
        "execution": {
            "slippage_bps": strategy.slippage_bps,
            "transaction_cost_bps": strategy.transaction_cost_bps,
            "exit_slippage_bps": strategy.exit_slippage_bps,
            "exit_transaction_cost_bps": strategy.exit_transaction_cost_bps,
            "return_basis": "dividend_adjusted_return_with_entry_and_exit_costs",
            "stock_and_benchmark_dates_aligned": True,
        },
        "cohorts": len(cohorts),
        "window": [cohorts[0]["entry"], cohorts[-1]["exit"]],
        "screened_only": strategy.screened_only,
        "by_verdict": by_verdict,
        "buy_minus_avoid_pct": spread,
        "by_strategy": by_strategy,
        "horizon_summary": horizon_summary,
        "spread_ci90": list(ci) if ci else None,
        "spread_ci90_two_way": _two_way_bootstrap_spread(
            observations, hold_days, bootstrap
        ) if bootstrap else None,
        "significant": bool(ci and ci[0] > 0),
        "monotonic": buy is not None
        and watch is not None
        and avoid is not None
        and buy >= watch >= avoid,
        "per_cohort": cohorts,
        "per_year_observation_counts": years,
        "regime_summary": regime_summary,
        "portfolio_summary": portfolio_summary,
        "unique_stocks": len({o["security_id"] for o in observations}),
        "unique_issuers": len({o["issuer_key"] for o in observations}),
        "target_summary": summarize_targets(observations, strategy.primary_target)
        if include_evidence
        else {},
        "target_method_summary": summarize_methods(observations)
        if include_evidence
        else {},
        "buy_return_achievement": summarize_buy_return_achievement(
            observations, step_days
        ) if include_evidence else {},
        "observations": observations,
    }
