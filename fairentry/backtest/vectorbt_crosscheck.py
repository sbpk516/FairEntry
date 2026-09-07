"""Independent VectorBT verification of FairEntry's recorded trade outcomes.

FairEntry supplies signal dates and reported results. This module independently
looks up executable dates and adjusted prices in the raw SFA warehouse, then
delegates fee-aware return calculation to VectorBT Community.
"""
from __future__ import annotations

from collections import Counter
from importlib.metadata import version
from pathlib import Path
from typing import Iterable

import ijson
import numpy as np
import pandas as pd


def artifact_metadata(path: str | Path) -> dict:
    """Read small scalar identity fields without loading a multi-GB artifact."""
    wanted = {
        "run_id", "snapshot_id", "implementation_fingerprint",
        "hold_days", "step_days", "cohorts",
    }
    output = {}
    with Path(path).open("rb") as stream:
        for prefix, event, value in ijson.parse(stream):
            if prefix == "observations" and event == "start_array":
                break
            if prefix in wanted and event in {
                "string", "number", "boolean", "null",
            }:
                output[prefix] = value
    return output


def selected_observations(path: str | Path, verdicts: set[str],
                          maximum: int | None = None) -> list[dict]:
    """Stream and retain only the fields required by the execution audit."""
    selected = []
    with Path(path).open("rb") as stream:
        for row in ijson.items(stream, "observations.item"):
            if row.get("verdict") not in verdicts:
                continue
            selected.append({
                "observation_id": row.get("observation_id"),
                "ticker": row.get("ticker"),
                "decision_date": row.get("decision_date"),
                "entry_date": row.get("entry_date"),
                "verdict": row.get("verdict"),
                "execution": row.get("execution") or {},
                "horizons": _reported_horizons(row),
            })
            if maximum and len(selected) >= maximum:
                break
    return selected


def _reported_horizons(row: dict) -> dict:
    return row.get("horizons") or (
        row.get("outcome") or {}
    ).get("horizons") or {}


def comparison_requests(observations: Iterable[dict],
                        horizons: set[int] | None = None) -> pd.DataFrame:
    """Flatten every recorded fixed-horizon result into an audit request."""
    requests = []
    for row in observations:
        execution = row.get("execution") or {}
        for horizon_text, result in _reported_horizons(row).items():
            try:
                horizon = int(horizon_text)
            except (TypeError, ValueError):
                continue
            if horizons is not None and horizon not in horizons:
                continue
            if not isinstance(result, dict) or result.get("return_pct") is None:
                continue
            requests.append({
                "comparison_id": f"{row.get('observation_id')}|{horizon}",
                "observation_id": row.get("observation_id"),
                "ticker": row.get("ticker"),
                "decision_date": row.get("decision_date"),
                "reported_entry_date": row.get("entry_date"),
                "horizon_days": horizon,
                "reported_exit_date": result.get("date"),
                "reported_return_pct": float(result["return_pct"]),
                "reported_status": result.get("status") or "fixed_horizon",
                "terminal_action": result.get("terminal_action"),
                "entry_cost_bps": float(execution.get("entry_cost_bps") or 0),
                "exit_cost_bps": float(execution.get("exit_cost_bps") or 0),
            })
    return pd.DataFrame(requests)


def recover_raw_trades(connection, requests: pd.DataFrame) -> pd.DataFrame:
    """Independently recover next-session entry and fixed-horizon exit rows."""
    if requests.empty:
        return requests.copy()
    connection.register("vectorbt_audit_requests", requests)
    try:
        return connection.execute("""
          SELECT r.*,
                 CAST(e.entry_date AS VARCHAR) independent_entry_date,
                 e.entry_closeadj,
                 CAST(CASE WHEN t.exit_date IS NOT NULL
                                  AND (x.exit_date IS NULL OR t.exit_date<x.exit_date)
                           THEN t.exit_date ELSE x.exit_date END AS VARCHAR)
                   independent_exit_date,
                 CASE WHEN t.exit_date IS NOT NULL
                                AND (x.exit_date IS NULL OR t.exit_date<x.exit_date)
                      THEN CASE WHEN t.terminal_action='bankruptcyliquidation'
                                THEN 0.000000000001 ELSE t.exit_closeadj END
                      ELSE x.exit_closeadj END exit_closeadj,
                 CASE WHEN t.exit_date IS NOT NULL
                                AND (x.exit_date IS NULL OR t.exit_date<x.exit_date)
                      THEN t.terminal_action ELSE NULL END independent_terminal_action,
                 CASE WHEN t.exit_date IS NOT NULL
                                AND (x.exit_date IS NULL OR t.exit_date<x.exit_date)
                      THEN 'terminal_event'
                      WHEN x.exit_date IS NOT NULL THEN 'fixed_horizon'
                      ELSE NULL END exit_mode
          FROM vectorbt_audit_requests r
          LEFT JOIN LATERAL (
            SELECT p.date entry_date,p.closeadj entry_closeadj
            FROM sfa_prices p
            WHERE p.ticker=r.ticker
              AND p.date>CAST(r.decision_date AS DATE)
            ORDER BY p.date LIMIT 1
          ) e ON true
          LEFT JOIN LATERAL (
            SELECT p.date exit_date,p.closeadj exit_closeadj
            FROM sfa_prices p
            WHERE p.ticker=r.ticker
              AND p.date>=e.entry_date
                         + CAST(r.horizon_days AS INTEGER) * INTERVAL 1 DAY
            ORDER BY p.date LIMIT 1
          ) x ON true
          LEFT JOIN LATERAL (
            SELECT a.date exit_date,p.closeadj exit_closeadj,
                   a.action terminal_action
            FROM sfa_actions a
            LEFT JOIN sfa_prices p ON p.ticker=a.ticker AND p.date=a.date
            WHERE a.ticker=r.ticker
              AND a.date>=e.entry_date
              AND a.date<=e.entry_date
                         + CAST(r.horizon_days AS INTEGER) * INTERVAL 1 DAY
              AND a.action IN (
                'delisted','regulatorydelisting','voluntarydelisting',
                'bankruptcyliquidation','acquisitionby','mergerto'
              )
            ORDER BY CASE WHEN a.action='delisted' THEN 1 ELSE 0 END,
                     a.date,
                     CASE a.action
                       WHEN 'bankruptcyliquidation' THEN 1
                       WHEN 'acquisitionby' THEN 2
                       WHEN 'mergerto' THEN 3
                       WHEN 'regulatorydelisting' THEN 4
                       WHEN 'voluntarydelisting' THEN 5
                       ELSE 9
                     END
            LIMIT 1
          ) t ON true
          ORDER BY r.comparison_id
        """).fetchdf()
    finally:
        connection.unregister("vectorbt_audit_requests")


def _vectorbt_returns(rows: pd.DataFrame, batch_size: int = 10_000) -> np.ndarray:
    try:
        import vectorbt as vbt
    except (ImportError, ValueError) as exc:
        raise RuntimeError(
            "VectorBT research dependencies are unavailable or incompatible; "
            "install requirements-research.txt"
        ) from exc

    results = []
    for start in range(0, len(rows), batch_size):
        batch = rows.iloc[start:start + batch_size]
        columns = batch["comparison_id"].tolist()
        prices = pd.DataFrame(
            np.vstack([
                batch["entry_closeadj"].astype(float).to_numpy(),
                batch["exit_closeadj"].astype(float).to_numpy(),
            ]),
            index=pd.RangeIndex(2), columns=columns,
        )
        entries = pd.DataFrame(False, index=prices.index, columns=columns)
        exits = entries.copy()
        entries.iloc[0] = True
        exits.iloc[1] = True
        fees = np.vstack([
            batch["entry_cost_bps"].astype(float).to_numpy() / 10_000,
            batch["exit_cost_bps"].astype(float).to_numpy() / 10_000,
        ])
        portfolio = vbt.Portfolio.from_signals(
            prices,
            entries=entries,
            exits=exits,
            init_cash=100.0,
            fees=fees,
        )
        results.append(portfolio.total_return().to_numpy(dtype=float) * 100)
    return np.concatenate(results) if results else np.asarray([], dtype=float)


def crosscheck(observations: Iterable[dict], connection, *,
               horizons: set[int] | None = None,
               tolerance_pct: float = 0.02) -> dict:
    """Return an auditable comparison report; never trust stored pass flags."""
    requests = comparison_requests(observations, horizons)
    recovered = recover_raw_trades(connection, requests)
    exclusions = Counter()
    comparable_mask = pd.Series(True, index=recovered.index)
    for column, reason in (
        ("entry_closeadj", "missing_raw_entry"),
        ("exit_closeadj", "missing_raw_exit"),
    ):
        missing = recovered[column].isna() if column in recovered else comparable_mask
        exclusions[reason] = int(missing.sum())
        comparable_mask &= ~missing
    comparable = recovered.loc[comparable_mask].copy()
    exclusion_examples = []
    for row in recovered.loc[~comparable_mask].head(20).to_dict("records"):
        reasons = []
        if pd.isna(row.get("entry_closeadj")):
            reasons.append("missing_raw_entry")
        if pd.isna(row.get("exit_closeadj")):
            reasons.append("missing_raw_exit")
        exclusion_examples.append({
            key: row.get(key) for key in (
                "comparison_id", "ticker", "horizon_days",
                "reported_entry_date", "independent_entry_date",
                "reported_exit_date", "independent_exit_date",
                "reported_status", "terminal_action",
                "independent_terminal_action",
            )
        } | {"reasons": reasons})
    mismatches = []
    if not comparable.empty:
        comparable["vectorbt_return_pct"] = _vectorbt_returns(comparable)
        comparable["absolute_return_difference_pct"] = (
            comparable["vectorbt_return_pct"]
            - comparable["reported_return_pct"].astype(float)
        ).abs()
        for row in comparable.to_dict("records"):
            reasons = []
            if row["independent_entry_date"] != row["reported_entry_date"]:
                reasons.append("entry_date")
            if row["independent_exit_date"] != row["reported_exit_date"]:
                reasons.append("exit_date")
            if (
                row.get("exit_mode") == "terminal_event"
                and row.get("independent_terminal_action")
                != row.get("terminal_action")
            ):
                reasons.append("terminal_action")
            if row["absolute_return_difference_pct"] > tolerance_pct:
                reasons.append("return_pct")
            if reasons:
                mismatches.append({
                    key: row.get(key) for key in (
                        "comparison_id", "ticker", "horizon_days",
                        "reported_entry_date", "independent_entry_date",
                        "reported_exit_date", "independent_exit_date",
                        "terminal_action", "independent_terminal_action",
                        "reported_return_pct", "vectorbt_return_pct",
                        "absolute_return_difference_pct",
                    )
                } | {"reasons": reasons})

    request_count = len(requests)
    compared_count = len(comparable)
    maximum_difference = (
        float(comparable["absolute_return_difference_pct"].max())
        if compared_count else None
    )
    exit_modes = (
        comparable["exit_mode"].value_counts().to_dict()
        if compared_count and "exit_mode" in comparable else {}
    )
    by_horizon = {}
    for horizon, group in comparable.groupby("horizon_days", sort=True):
        fairentry_returns = group["reported_return_pct"].astype(float)
        vectorbt_returns = group["vectorbt_return_pct"].astype(float)
        fixed_date_hits = {}
        for threshold in (25, 30, 40, 50):
            fixed_date_hits[str(threshold)] = {
                "fairentry_pct": round(
                    float((fairentry_returns >= threshold).mean() * 100), 2
                ),
                "vectorbt_pct": round(
                    float((vectorbt_returns >= threshold).mean() * 100), 2
                ),
            }
        by_horizon[str(int(horizon))] = {
            "comparisons": int(len(group)),
            "fixed_horizon_exits": int((group["exit_mode"] == "fixed_horizon").sum()),
            "terminal_event_exits": int((group["exit_mode"] == "terminal_event").sum()),
            "fairentry_mean_return_pct": round(float(fairentry_returns.mean()), 4),
            "vectorbt_mean_return_pct": round(float(vectorbt_returns.mean()), 4),
            "fairentry_median_return_pct": round(float(fairentry_returns.median()), 4),
            "vectorbt_median_return_pct": round(float(vectorbt_returns.median()), 4),
            "maximum_absolute_difference_pct": round(
                float(group["absolute_return_difference_pct"].max()), 8
            ),
            "fixed_date_hit_rates": fixed_date_hits,
        }
    return {
        "ok": compared_count > 0 and not mismatches,
        "engine": "vectorbt-community",
        "engine_version": version("vectorbt"),
        "independence_boundary": (
            "VectorBT independently calculates fee-aware trade returns from raw "
            "warehouse prices. FairEntry still supplies signal dates; this does "
            "not independently validate fundamental signal generation or vendor data."
        ),
        "tolerance_percentage_points": tolerance_pct,
        "requested_comparisons": request_count,
        "compared": compared_count,
        "coverage_pct": round(compared_count / request_count * 100, 2)
        if request_count else 0.0,
        "excluded": request_count - compared_count,
        "exit_modes": {str(key): int(value) for key, value in exit_modes.items()},
        "by_horizon": by_horizon,
        "exclusion_reasons": {
            key: value for key, value in exclusions.items() if value
        },
        "exclusion_examples": exclusion_examples,
        "mismatch_count": len(mismatches),
        "maximum_absolute_return_difference_pct": (
            round(maximum_difference, 8) if maximum_difference is not None else None
        ),
        "mismatch_examples": mismatches[:20],
    }
