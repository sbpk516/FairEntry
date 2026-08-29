"""Point-in-time replay for the shared Emerging Research v2 policy."""
from __future__ import annotations

import math
import statistics
from datetime import date, timedelta

from fairentry.analytics.entry_exit_evidence import build_entry_exit_evidence
from fairentry.emerging import VARIANT_ORDER, evaluate_emerging_candidate, liquidity_band
from fairentry.backtest.valuation_research import POLICIES, evaluate_policy


SECTOR_ETF = {
    "Technology": "XLK",
    "Communication Services": "XLC",
    "Consumer Cyclical": "XLY",
}
AGREEMENT_POLICY = next(policy for policy in POLICIES if policy["id"] == "method_agreement")


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _category(row, category_id):
    for category in row.get("categories") or []:
        if category.get("id") == category_id:
            return _number(category.get("score"))
    return None


def observation_inputs(row: dict, *, entry_alignment=None) -> dict:
    agreement = evaluate_policy(row, AGREEMENT_POLICY)
    return {
        "avg_dollar_volume": row.get("avg_dollar_volume"),
        "business_quality": _category(row, "quality"),
        "financial_strength": _category(row, "survival"),
        "growth": _category(row, "growth"),
        "valuation_upside_pct": agreement.get("upside_pct"),
        "valuation_method_count": agreement.get("method_count"),
        "valuation_agreement": "pass" if agreement.get("passes") else (
            "caution" if agreement.get("method_count", 0) >= 2 else "insufficient"),
        "entry_alignment": entry_alignment,
        "shadow_verdict": row.get("verdict"),
        "no_hard_veto": not bool(row.get("vetoes")),
    }


def attach_historical_entry_evidence(observations: list[dict], connection,
                                     policy: dict) -> dict:
    """Attach exact as-of trend/momentum/volume/volatility alignment.

    To control replay cost, OHLCV is fetched only for observations that pass the
    Broad rule's non-market checks. Broad itself does not require alignment.
    """
    candidates = []
    for row in observations:
        result = evaluate_emerging_candidate(observation_inputs(row), policy)
        if result["variants"]["broad"]["passes"]:
            candidates.append(row)
    by_date = {}
    for row in candidates:
        by_date.setdefault(row["decision_date"], []).append(row)
    attached = 0
    import pandas as pd

    for decision_date, rows in sorted(by_date.items()):
        tickers = sorted({row["ticker"] for row in rows})
        start = (date.fromisoformat(decision_date) - timedelta(days=550)).isoformat()
        connection.register("emerging_requested", pd.DataFrame({"ticker": tickers}))
        stock_rows = connection.execute("""
          SELECT p.ticker,p.date,p.close,p.closeadj,p.high,p.low,p.volume
          FROM sfa_prices p JOIN emerging_requested r USING(ticker)
          WHERE p.date BETWEEN ? AND ? ORDER BY p.ticker,p.date
        """, [start, decision_date]).fetchall()
        connection.unregister("emerging_requested")
        stock_history = {}
        for ticker, observed, close, closeadj, high, low, volume in stock_rows:
            adjusted = float(closeadj or close) if (closeadj or close) else None
            ratio = adjusted / float(close) if adjusted and close else 1.0
            stock_history.setdefault(ticker, []).append({
                "date": observed.isoformat(), "close": adjusted,
                "high": float(high) * ratio if high else adjusted,
                "low": float(low) * ratio if low else adjusted,
                "volume": float(volume or 0),
            })
        fund_tickers = sorted({"SPY"} | {
            SECTOR_ETF.get(row.get("sector")) for row in rows
            if SECTOR_ETF.get(row.get("sector"))})
        placeholders = ",".join("?" for _ in fund_tickers)
        fund_rows = connection.execute(f"""
          SELECT ticker,date,closeadj FROM sfa_fund_prices
          WHERE ticker IN ({placeholders}) AND date BETWEEN ? AND ?
          ORDER BY ticker,date
        """, [*fund_tickers, start, decision_date]).fetchall()
        funds = {}
        for ticker, _observed, close in fund_rows:
            if close:
                funds.setdefault(ticker, []).append(float(close))

        for row in rows:
            history = stock_history.get(row["ticker"], [])
            if not history:
                continue
            sector_etf = SECTOR_ETF.get(row.get("sector"))
            evidence = build_entry_exit_evidence(
                [point["close"] for point in history],
                [point["volume"] for point in history],
                funds.get(sector_etf, []), funds.get("SPY", []),
                highs=[point["high"] for point in history],
                lows=[point["low"] for point in history],
                observed_at=decision_date,
            )
            row["_emerging_entry_evidence"] = evidence
            attached += 1
    return {"eligible_for_attachment": len(candidates), "attached": attached,
            "version": "entry_exit_evidence_v1", "future_data_used": False}


def evaluate_observations(observations: list[dict], policy: dict) -> list[dict]:
    evaluated = []
    for row in observations:
        alignment = (row.get("_emerging_entry_evidence") or {}).get("entry_alignment")
        result = evaluate_emerging_candidate(
            observation_inputs(row, entry_alignment=alignment), policy)
        copy = dict(row)
        copy["_emerging_evaluation"] = result
        evaluated.append(copy)
    return evaluated


def _qualifies(row, group):
    evaluation = row.get("_emerging_evaluation") or {}
    if group == "screened":
        return _number(row.get("avg_dollar_volume")) is not None
    if group == "shadow_buy_watch":
        return row.get("verdict") in {"Buy", "Watch"}
    return bool(((evaluation.get("variants") or {}).get(group) or {}).get("passes"))


def _in_scope(row, scope):
    band = liquidity_band(row.get("avg_dollar_volume"))
    if scope == "5m_to_20m":
        return band in {"5m_to_10m", "10m_to_20m"}
    if scope == "all":
        return band in {"5m_to_10m", "10m_to_20m", "20m_plus"}
    return band == scope


def collapse_episodes(observations: list[dict], group: str, scope: str,
                      *, step_days=30) -> list[dict]:
    """Collapse consecutive qualifying observations into one issuer episode."""
    max_gap = max(step_days + 1, int(math.ceil(step_days * 1.5)))
    grouped = {}
    for row in observations:
        key = row.get("issuer_key") or row.get("security_id") or row.get("ticker")
        if key and row.get("entry_date"):
            grouped.setdefault(str(key), []).append(row)
    episodes = []

    def finish(active):
        if not active:
            return
        root = dict(active[0])
        root["_episode"] = {"started": active[0]["entry_date"],
                            "last_qualified": active[-1]["entry_date"],
                            "signals": len(active), "group": group, "scope": scope}
        episodes.append(root)

    for rows in grouped.values():
        active, previous = [], None
        for row in sorted(rows, key=lambda item: item["entry_date"]):
            current = date.fromisoformat(row["entry_date"])
            if previous is not None and (current - previous).days > max_gap:
                finish(active); active = []
            if _in_scope(row, scope) and _qualifies(row, group):
                active.append(row)
            else:
                finish(active); active = []
            previous = current
        finish(active)
    return sorted(episodes, key=lambda row: (row["entry_date"], row.get("ticker", "")))


def attach_graduations(episodes: list[dict], observations: list[dict]) -> None:
    """Attach first $10M, $20M and official-Buy milestones after detection."""
    by_issuer = {}
    for row in observations:
        key = row.get("issuer_key") or row.get("security_id") or row.get("ticker")
        by_issuer.setdefault(str(key), []).append(row)
    for root in episodes:
        key = str(root.get("issuer_key") or root.get("security_id") or root.get("ticker"))
        started = date.fromisoformat(root["entry_date"])
        later = [row for row in by_issuer.get(key, [])
                 if row.get("entry_date") and date.fromisoformat(row["entry_date"]) >= started]

        def milestone(predicate):
            row = next((item for item in sorted(later, key=lambda x: x["entry_date"])
                        if predicate(item)), None)
            if not row:
                return None
            days = (date.fromisoformat(row["entry_date"]) - started).days
            root_price, later_price = _number(root.get("entry_price")), _number(row.get("entry_price"))
            return {"date": row["entry_date"], "days": days,
                    "price_change_pct": round((later_price / root_price - 1) * 100, 1)
                    if root_price and later_price else None}

        root["_emerging_graduation"] = {
            "official_liquidity_10m": milestone(
                lambda row: (_number(row.get("avg_dollar_volume")) or 0) >= 10_000_000),
            "positive_liquidity_20m": milestone(
                lambda row: (_number(row.get("avg_dollar_volume")) or 0) >= 20_000_000),
            "official_buy": milestone(
                lambda row: (_number(row.get("avg_dollar_volume")) or 0) >= 10_000_000
                and row.get("verdict") == "Buy"),
        }


def _complete(outcome, horizon=365):
    last, terminal = outcome.get("last_observed_days"), outcome.get("terminal_days")
    return ((isinstance(last, (int, float)) and last >= horizon)
            or (isinstance(terminal, (int, float)) and terminal <= horizon))


def _wilson(successes, total):
    if not total:
        return None
    z, rate = 1.6448536269514722, successes / total
    den = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / den
    margin = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total)) / den
    return [round((center - margin) * 100, 1), round((center + margin) * 100, 1)]


def summarize(episodes: list[dict]) -> dict:
    targets = {}
    for target in (20, 25, 30):
        reached, failed, active, days = 0, 0, 0, []
        for row in episodes:
            outcome = row.get("_tuning_outcome") or {}
            hit = (outcome.get("first_hit_days_by_target") or {}).get(str(target))
            if isinstance(hit, (int, float)) and hit <= 365:
                reached += 1; days.append(float(hit))
            elif _complete(outcome):
                failed += 1
            else:
                active += 1
        completed = reached + failed
        targets[str(target)] = {
            "completed_episodes": completed, "reached": reached, "failed": failed,
            "active": active,
            "success_rate_pct": round(reached / completed * 100, 1) if completed else None,
            "success_rate_ci90_pct": _wilson(reached, completed),
            "median_days_to_hit": round(statistics.median(days), 1) if days else None,
        }
    mature = [row for row in episodes if _complete(row.get("_tuning_outcome") or {})]
    drawdowns = [_number((row.get("_tuning_outcome") or {}).get("max_drawdown_pct"))
                 for row in mature]
    drawdowns = [value for value in drawdowns if value is not None]
    graduations = {}
    for milestone in ("official_liquidity_10m", "positive_liquidity_20m", "official_buy"):
        rows = [(row.get("_emerging_graduation") or {}).get(milestone) for row in episodes]
        rows = [row for row in rows if row]
        graduations[milestone] = {
            "reached": len(rows), "episodes": len(episodes),
            "rate_pct": round(len(rows) / len(episodes) * 100, 1) if episodes else None,
            "median_days": round(statistics.median(row["days"] for row in rows), 1)
            if rows else None,
            "median_price_change_pct": round(statistics.median(
                row["price_change_pct"] for row in rows
                if row.get("price_change_pct") is not None), 1)
            if any(row.get("price_change_pct") is not None for row in rows) else None,
        }
    return {
        "episodes": len(episodes),
        "unique_issuers": len({row.get("issuer_key") or row.get("ticker") for row in episodes}),
        "targets_within_365_days": targets,
        "median_max_drawdown_pct": round(statistics.median(drawdowns), 1) if drawdowns else None,
        "worst_max_drawdown_pct": round(min(drawdowns), 1) if drawdowns else None,
        "graduation": graduations,
    }


def run_emerging_candidate_backtest(observations: list[dict], policy: dict,
                                    *, step_days=30) -> dict:
    evaluated = evaluate_observations(observations, policy)
    scopes = ("5m_to_10m", "10m_to_20m", "5m_to_20m", "20m_plus", "all")
    groups = ("screened", "shadow_buy_watch", *VARIANT_ORDER)
    all_episodes = {}
    for scope in scopes:
        all_episodes[scope] = {}
        for group in groups:
            episodes = collapse_episodes(evaluated, group, scope, step_days=step_days)
            attach_graduations(episodes, evaluated)
            all_episodes[scope][group] = episodes

    dates = sorted({row["decision_date"] for row in all_episodes["5m_to_20m"]["screened"]})
    dev_end = max(1, int(len(dates) * .60)) if dates else 0
    val_end = max(dev_end + 1, int(len(dates) * .80)) if len(dates) >= 3 else len(dates)
    split_dates = {"development": set(dates[:dev_end]),
                   "validation": set(dates[dev_end:val_end]),
                   "test": set(dates[val_end:])}

    def split_summary(episodes):
        result = {name: summarize([row for row in episodes
                                   if row.get("decision_date") in selected])
                  for name, selected in split_dates.items()}
        result["all"] = summarize(episodes)
        return result

    results = {scope: {group: split_summary(all_episodes[scope][group])
                       for group in groups}
               for scope in scopes}
    baseline_test = results["5m_to_20m"]["screened"]["test"][
        "targets_within_365_days"]["30"]
    validation = {}
    for variant in VARIANT_ORDER:
        candidate = results["5m_to_20m"][variant]
        test = candidate["test"]["targets_within_365_days"]["30"]
        all_result = candidate["all"]["targets_within_365_days"]["30"]
        improvement = (round(test["success_rate_pct"] - baseline_test["success_rate_pct"], 1)
                       if None not in (test.get("success_rate_pct"),
                                       baseline_test.get("success_rate_pct")) else None)
        validation[variant] = {
            "validated": bool(
                all_result["completed_episodes"] >= 100
                and test["completed_episodes"] >= 20
                and improvement is not None and improvement >= 5),
            "final_test_improvement_pp": improvement,
            "requirements": {
                "minimum_completed_episodes": 100,
                "minimum_final_test_episodes": 20,
                "minimum_final_test_improvement_pp": 5,
            },
        }
    return {
        "ok": True, "version": policy.get("policy_version", "emerging_research_v2"),
        "policy": policy,
        "rule_source": "shared fairentry.emerging.evaluate_emerging_candidate",
        "point_in_time": True, "future_information_used_for_selection": False,
        "score_effect": 0, "verdict_effect": "none",
        "split": {name: {"first": min(values) if values else None,
                           "last": max(values) if values else None,
                           "cohorts": len(values)}
                  for name, values in split_dates.items()},
        "results": results, "validation": validation,
        "interpretation": (
            "Fixed v2 variants are reported without threshold tuning. Development, "
            "validation and final test are chronological; no result changes production."
        ),
    }
