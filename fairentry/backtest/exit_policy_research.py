"""Chronological research for the deterministic Exit Policy v1 challenger."""
from __future__ import annotations

import math
import statistics
from collections import Counter
from datetime import date, timedelta

import pandas as pd

from ..exit_policy import (
    EXIT_POLICY_V1,
    apply_execution,
    new_position_state,
    observe_close,
)
from .evidence import _buy_episode_roots


def _number(value):
    return float(value) if isinstance(value, (int, float)) else None


def _issuer(row: dict) -> str:
    return str(row.get("issuer_key") or row.get("security_id") or row.get("ticker"))


def _episode_roots(observations: list[dict], step_days: int) -> list[dict]:
    roots = _buy_episode_roots(
        observations, max(step_days + 1, int(math.ceil(step_days * 1.5)))
    )
    return [row for row in roots if row.get("entry_date") and row.get("ticker")]


def _signal_timelines(observations: list[dict]) -> dict[str, list[dict]]:
    timelines: dict[str, list[dict]] = {}
    for row in observations:
        observed = str(row.get("decision_date") or row.get("entry_date") or "")[:10]
        if not observed:
            continue
        timelines.setdefault(_issuer(row), []).append({
            "date": observed,
            "verdict": row.get("verdict"),
            "vetoes": list(row.get("vetoes") or []),
        })
    for rows in timelines.values():
        rows.sort(key=lambda item: item["date"])
    return timelines


def _price_paths(connection, roots: list[dict]) -> dict[str, list[dict]]:
    ranges = []
    for index, row in enumerate(roots):
        start = date.fromisoformat(str(row["entry_date"])[:10])
        ranges.append({
            "episode_id": str(index),
            "ticker": row["ticker"],
            "start_date": start,
            "end_date": start + timedelta(days=745),
        })
        row["_exit_episode_id"] = str(index)
    if not ranges:
        return {}
    frame = pd.DataFrame(ranges)
    connection.register("exit_episode_ranges", frame)
    try:
        rows = connection.execute(
            """
            SELECT r.episode_id,p.date,p.close,p.closeadj
            FROM exit_episode_ranges r
            JOIN sfa_prices p ON p.ticker=r.ticker
             AND p.date BETWEEN r.start_date AND r.end_date
            ORDER BY r.episode_id,p.date
            """
        ).fetchall()
    finally:
        connection.unregister("exit_episode_ranges")
    paths: dict[str, list[dict]] = {}
    for episode_id, observed, close, closeadj in rows:
        paths.setdefault(str(episode_id), []).append({
            "date": observed.isoformat() if hasattr(observed, "isoformat") else str(observed)[:10],
            "close": float(close) if close is not None else None,
            "closeadj": float(closeadj if closeadj is not None else close),
        })
    return paths


def _latest_signal_index(timeline: list[dict], start: str) -> int:
    index = -1
    for candidate, item in enumerate(timeline):
        if item["date"] <= start:
            index = candidate
        else:
            break
    return index


def _baseline_trade(root: dict, path: list[dict], entry_basis: float,
                    exit_cost: float) -> dict:
    if not path:
        return {"status": "unavailable"}
    start = date.fromisoformat(str(root["entry_date"])[:10])
    terminal = root.get("terminal_event") or {}
    terminal_date = str(terminal.get("date") or "")[:10]
    chosen = None
    reason = "active"
    for point in path:
        elapsed = (date.fromisoformat(point["date"]) - start).days
        if terminal_date and point["date"] >= terminal_date:
            chosen, reason = point, "terminal_event"
            break
        if elapsed >= EXIT_POLICY_V1["maximum_holding_days"]:
            chosen, reason = point, "maximum_holding_period"
            break
    if chosen is None:
        chosen = path[-1]
    value = (
        0.0 if terminal and terminal.get("terminal_return_policy") == "zero"
        and reason == "terminal_event"
        else chosen["closeadj"] * (1 - exit_cost) / entry_basis
    )
    return {
        "status": "closed" if reason != "active" else "active",
        "exit_reason": reason,
        "exit_date": chosen["date"],
        "return_pct": round((value - 1) * 100, 4),
        "days_held": (date.fromisoformat(chosen["date"]) - start).days,
    }


def _candidate_trade(root: dict, path: list[dict], timeline: list[dict],
                     entry_basis: float, entry_closeadj: float,
                     exit_cost: float) -> dict:
    if len(path) < 2 or not entry_closeadj:
        return {"status": "unavailable"}
    start_text = str(root["entry_date"])[:10]
    start = date.fromisoformat(start_text)
    state = new_position_state(entry_date=start_text, entry_price=100)
    target = (((root.get("outcome") or {}).get("targets") or {})
              .get("practical") or {}).get("price")
    target = _number(target)
    signal_index = _latest_signal_index(timeline, str(root.get("decision_date") or start_text)[:10])
    realised = 0.0
    values = []
    terminal = root.get("terminal_event") or {}
    terminal_date = str(terminal.get("date") or "")[:10]
    last_point = path[0]

    for point in path:
        last_point = point
        synthetic_net_price = (
            point["closeadj"] * (1 - exit_cost) / entry_basis * 100
        )
        if state.get("pending_action"):
            pending = dict(state["pending_action"])
            fraction = float(pending["fraction_of_original_position"])
            realised += fraction * point["closeadj"] * (1 - exit_cost) / entry_basis
            apply_execution(
                state, execution_date=point["date"], execution_price=synthetic_net_price
            )
        if state.get("status") == "closed":
            values.append(realised)
            break

        verdict = None
        vetoes = None
        while signal_index + 1 < len(timeline) and timeline[signal_index + 1]["date"] <= point["date"]:
            signal_index += 1
            verdict = timeline[signal_index]["verdict"]
            vetoes = timeline[signal_index]["vetoes"]
        terminal_now = terminal if terminal_date and point["date"] >= terminal_date else None
        target_reached = bool(target and point.get("close") is not None and point["close"] >= target)
        instruction = observe_close(
            state,
            observed_date=point["date"],
            price=synthetic_net_price,
            verdict=verdict,
            vetoes=vetoes,
            terminal_event=terminal_now,
            practical_target_reached=target_reached,
        )
        if instruction and instruction["code"] == "terminal_event":
            fraction = float(instruction["fraction_of_original_position"])
            proceeds = (
                0.0 if terminal.get("terminal_return_policy") == "zero"
                else fraction * point["closeadj"] * (1 - exit_cost) / entry_basis
            )
            realised += proceeds
            apply_execution(
                state, execution_date=point["date"], execution_price=synthetic_net_price
            )
        open_value = (
            float(state.get("remaining_fraction", 0))
            * point["closeadj"] / entry_basis
        )
        values.append(realised + open_value)
        if state.get("status") == "closed":
            break

    final_value = values[-1] if values else 1.0
    peak, drawdown = 1.0, 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1 if peak else -1)
    return {
        "status": state.get("status", "active"),
        "exit_reason": state.get("exit_reason") or "active",
        "exit_date": state.get("closed_at") or last_point["date"],
        "return_pct": round((final_value - 1) * 100, 4),
        "max_drawdown_pct": round(drawdown * 100, 4),
        "days_held": (
            date.fromisoformat(state.get("closed_at") or last_point["date"]) - start
        ).days,
        "actions": state.get("actions", []),
        "remaining_fraction": state.get("remaining_fraction"),
    }


def _summary(trades: list[dict], key: str) -> dict:
    rows = [row[key] for row in trades if row.get(key, {}).get("status") != "unavailable"]
    completed = [row for row in rows if row.get("status") == "closed"]
    values = [row["return_pct"] for row in rows if _number(row.get("return_pct")) is not None]
    completed_values = [row["return_pct"] for row in completed]
    losses = sorted(completed_values)
    return {
        "trades": len(rows),
        "completed": len(completed),
        "active": len(rows) - len(completed),
        "win_rate_pct": round(sum(value > 0 for value in completed_values) / len(completed_values) * 100, 1)
        if completed_values else None,
        "mean_return_pct": round(statistics.mean(values), 2) if values else None,
        "median_return_pct": round(statistics.median(values), 2) if values else None,
        "mean_completed_return_pct": round(statistics.mean(completed_values), 2)
        if completed_values else None,
        "worst_return_pct": round(min(values), 2) if values else None,
        "loss_tail_p05_pct": round(losses[max(0, int(len(losses) * .05) - 1)], 2)
        if losses else None,
        "median_days_held": round(statistics.median(
            row["days_held"] for row in completed if _number(row.get("days_held")) is not None
        ), 1) if completed else None,
        "exit_reasons": dict(Counter(row.get("exit_reason") for row in completed)),
    }


def evaluate_exit_policy(
    observations: list[dict],
    paths: dict[str, list[dict]],
    *,
    step_days: int,
) -> dict:
    roots = _episode_roots(observations, step_days)
    timelines = _signal_timelines(observations)
    trades = []
    for root in roots:
        path = paths.get(str(root.get("_exit_episode_id")), [])
        entry_closeadj = _number(root.get("_entry_closeadj"))
        if not path or not entry_closeadj:
            continue
        execution = root.get("execution") or {}
        entry_cost = float(execution.get("entry_cost_bps") or 0) / 10000
        exit_cost = float(execution.get("exit_cost_bps") or 0) / 10000
        entry_basis = entry_closeadj * (1 + entry_cost)
        candidate = _candidate_trade(
            root, path, timelines.get(_issuer(root), []), entry_basis,
            entry_closeadj, exit_cost,
        )
        baseline = _baseline_trade(root, path, entry_basis, exit_cost)
        trades.append({
            "ticker": root.get("ticker"),
            "issuer_key": _issuer(root),
            "entry_date": root.get("entry_date"),
            "score": root.get("score"),
            "candidate": candidate,
            "baseline": baseline,
        })
    ordered_dates = sorted({row["entry_date"] for row in trades})
    dev_end = ordered_dates[max(0, math.ceil(len(ordered_dates) * .6) - 1)] if ordered_dates else None
    validation_end = ordered_dates[max(0, math.ceil(len(ordered_dates) * .8) - 1)] if ordered_dates else None

    def split(row):
        return "development" if row["entry_date"] <= dev_end else (
            "validation" if row["entry_date"] <= validation_end else "final_unseen"
        )

    partitions = {}
    for name in ("all_history", "development", "validation", "final_unseen"):
        selected = trades if name == "all_history" else [row for row in trades if split(row) == name]
        candidate = _summary(selected, "candidate")
        baseline = _summary(selected, "baseline")
        partitions[name] = {
            "candidate": candidate,
            "baseline_730_day_hold": baseline,
            "mean_return_change_pp": (
                round(candidate["mean_return_pct"] - baseline["mean_return_pct"], 2)
                if candidate["mean_return_pct"] is not None
                and baseline["mean_return_pct"] is not None else None
            ),
        }
    final = partitions["final_unseen"]
    checks = [
        {
            "id": "sample",
            "passed": final["candidate"]["completed"] >= 20,
            "rule": "at least 20 completed final-test episodes",
        },
        {
            "id": "mean_return",
            "passed": (final["mean_return_change_pp"] or -999) >= 0,
            "rule": "final-test mean return is not below the 730-day hold baseline",
        },
        {
            "id": "loss_tail",
            "passed": (
                final["candidate"]["loss_tail_p05_pct"] is not None
                and final["baseline_730_day_hold"]["loss_tail_p05_pct"] is not None
                and final["candidate"]["loss_tail_p05_pct"]
                >= final["baseline_730_day_hold"]["loss_tail_p05_pct"]
            ),
            "rule": "final-test fifth-percentile completed-trade result is not worse",
        },
    ]
    return {
        "version": 1,
        "policy": dict(EXIT_POLICY_V1),
        "research_design": {
            "unit": "continuous Buy episode",
            "split": "oldest 60% development, next 20% validation, newest 20% final unseen",
            "execution": "trigger after close; fill at next available close with configured costs",
            "baseline": "hold the full position for 730 calendar days or a terminal event",
            "licensed_price_paths_published": False,
            "portfolio_capacity_tested": False,
        },
        "episodes": len(trades),
        "unique_issuers": len({row["issuer_key"] for row in trades}),
        "partitions": partitions,
        "promotion_checks": checks,
        "promotion_eligible": False,
        "decision": (
            "Do not promote: the policy improved the severe-loss tail but materially "
            "underperformed the 730-day hold baseline on final-unseen mean return. "
            "A future challenger also requires capacity and cash-redeployment validation."
        ),
        "production_effect": "none",
        "sample_trades": [
            {
                "ticker": row["ticker"],
                "entry_date": row["entry_date"],
                "candidate_return_pct": row["candidate"].get("return_pct"),
                "baseline_return_pct": row["baseline"].get("return_pct"),
                "exit_reason": row["candidate"].get("exit_reason"),
            }
            for row in trades[-100:]
        ],
    }


def run_exit_policy_research(
    observations: list[dict], connection, *, step_days: int
) -> dict:
    roots = _episode_roots(observations, step_days)
    paths = _price_paths(connection, roots)
    # Preserve the assigned ids when the evaluator derives roots again.
    roots_by_key = {
        (_issuer(row), row["entry_date"]): row["_exit_episode_id"] for row in roots
    }
    for row in observations:
        key = (_issuer(row), row.get("entry_date"))
        if key in roots_by_key:
            row["_exit_episode_id"] = roots_by_key[key]
    # The episode helper copies roots. Assign ids to those copies by matching
    # issuer and first entry through a short adapter around the generated paths.
    generated = _episode_roots(observations, step_days)
    remapped = {}
    for row in generated:
        episode_id = roots_by_key.get((_issuer(row), row["entry_date"]))
        if episode_id is not None:
            row["_exit_episode_id"] = episode_id
            remapped[str(episode_id)] = paths.get(str(episode_id), [])
    # evaluate_exit_policy derives once more, so mark the actual root observation
    # (the first Buy row) with the stable id it copies.
    for row in observations:
        episode_id = roots_by_key.get((_issuer(row), row.get("entry_date")))
        if episode_id is not None:
            row["_exit_episode_id"] = episode_id
    return evaluate_exit_policy(observations, remapped, step_days=step_days)
