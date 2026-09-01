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


EXIT_POLICY_CHALLENGERS = [
    {
        **EXIT_POLICY_V1,
        "version": "exit_v2_balanced_research",
        "catastrophic_loss_pct": -35.0,
        "avoid_confirmations": 3,
        "first_profit_pct": 50.0,
        "first_profit_fraction": 0.25,
        "trailing_drawdown_pct": 25.0,
        "stagnation_days": 548,
        "practical_target_exit": False,
    },
    {
        **EXIT_POLICY_V1,
        "version": "exit_v2_patient_research",
        "catastrophic_loss_pct": -40.0,
        "avoid_confirmations": 4,
        "first_profit_pct": 75.0,
        "first_profit_fraction": 0.20,
        "trailing_drawdown_pct": 30.0,
        "stagnation_days": 730,
        "practical_target_exit": False,
    },
    {
        **EXIT_POLICY_V1,
        "version": "exit_v2_tail_guard_research",
        "catastrophic_loss_pct": -45.0,
        "avoid_confirmations": 99,
        "first_profit_pct": 10_000.0,
        "first_profit_fraction": 0.0,
        "trailing_drawdown_pct": 100.0,
        "stagnation_days": 730,
        "hard_veto_exit": False,
        "practical_target_exit": False,
    },
    {
        **EXIT_POLICY_V1,
        "version": "exit_v2_confirmed_thesis_research",
        "catastrophic_loss_pct": -100.0,
        "avoid_confirmations": 3,
        "first_profit_pct": 10_000.0,
        "first_profit_fraction": 0.0,
        "trailing_drawdown_pct": 100.0,
        "stagnation_days": 730,
        "hard_veto_exit": True,
        "practical_target_exit": False,
    },
]


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
                    exit_cost: float, *, policy: dict) -> dict:
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
        if elapsed >= policy["maximum_holding_days"]:
            chosen, reason = point, "maximum_holding_period"
            break
    if chosen is None:
        chosen = path[-1]
    value = (
        0.0 if terminal and terminal.get("terminal_return_policy") == "zero"
        and reason == "terminal_event"
        else chosen["closeadj"] * (1 - exit_cost) / entry_basis
    )
    curve = []
    for point in path:
        if point["date"] > chosen["date"]:
            break
        terminal_zero = (
            terminal and terminal.get("terminal_return_policy") == "zero"
            and reason == "terminal_event" and point["date"] == chosen["date"]
        )
        factor = 0.0 if terminal_zero else point["closeadj"] / entry_basis
        closed_here = point["date"] == chosen["date"] and reason != "active"
        if closed_here:
            factor *= 1 - exit_cost
        curve.append({
            "date": point["date"],
            "value_factor": factor,
            "open_value_factor": 0.0 if closed_here else factor,
            "realized_factor": factor if closed_here else 0.0,
        })
    return {
        "status": "closed" if reason != "active" else "active",
        "exit_reason": reason,
        "exit_date": chosen["date"],
        "return_pct": round((value - 1) * 100, 4),
        "days_held": (date.fromisoformat(chosen["date"]) - start).days,
        "_value_curve": curve,
    }


def _candidate_trade(root: dict, path: list[dict], timeline: list[dict],
                     entry_basis: float, entry_closeadj: float,
                     exit_cost: float, *, policy: dict) -> dict:
    if len(path) < 2 or not entry_closeadj:
        return {"status": "unavailable"}
    start_text = str(root["entry_date"])[:10]
    start = date.fromisoformat(start_text)
    state = new_position_state(entry_date=start_text, entry_price=100, policy=policy)
    target = (((root.get("outcome") or {}).get("targets") or {})
              .get("practical") or {}).get("price")
    target = _number(target)
    signal_index = _latest_signal_index(timeline, str(root.get("decision_date") or start_text)[:10])
    realised = 0.0
    values = []
    curve = []
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
            curve.append({
                "date": point["date"],
                "value_factor": realised,
                "open_value_factor": 0.0,
                "realized_factor": realised,
            })
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
            policy=policy,
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
        curve.append({
            "date": point["date"],
            "value_factor": values[-1],
            "open_value_factor": open_value,
            "realized_factor": realised,
        })
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
        "_value_curve": curve,
    }


def _portfolio_replay(trades: list[dict], key: str, *, max_positions: int) -> dict:
    """Replay ranked Buy episodes through a finite-slot, cash-funded portfolio."""
    candidates = [
        row for row in trades
        if row.get(key, {}).get("_value_curve")
    ]
    entries: dict[str, list[dict]] = {}
    event_dates = set()
    for row in candidates:
        entries.setdefault(str(row["entry_date"])[:10], []).append(row)
        event_dates.update(point["date"] for point in row[key]["_value_curve"])
    cash = 1.0
    positions: list[dict] = []
    accepted = skipped_capacity = skipped_duplicate = 0
    equity_curve = []
    peak = 1.0
    max_drawdown = 0.0

    for observed in sorted(event_dates):
        # Update marks and return proceeds from completed positions before new entries.
        retained = []
        for position in positions:
            points = position["trade"][key]["_value_curve"]
            while (position["curve_index"] + 1 < len(points)
                   and points[position["curve_index"] + 1]["date"] <= observed):
                position["curve_index"] += 1
            point = points[position["curve_index"]]
            new_realized = point.get("realized_factor", 0.0)
            cash += position["capital"] * (
                new_realized - position["realized_factor"]
            )
            position["realized_factor"] = new_realized
            position["factor"] = point.get(
                "open_value_factor", point["value_factor"]
            )
            trade_result = position["trade"][key]
            if (trade_result.get("status") == "closed"
                    and observed >= points[-1]["date"]):
                # Realized proceeds were transferred above; no closed value
                # remains inside the position.
                pass
            else:
                retained.append(position)
        positions = retained

        marked_equity = cash + sum(
            position["capital"] * position["factor"] for position in positions
        )
        active_issuers = {position["trade"]["issuer_key"] for position in positions}
        for row in sorted(
            entries.get(observed, []),
            key=lambda item: (-(item.get("score") or 0), item.get("issuer_key") or ""),
        ):
            if row["issuer_key"] in active_issuers:
                skipped_duplicate += 1
                continue
            if len(positions) >= max_positions or cash <= 1e-12:
                skipped_capacity += 1
                continue
            allocation = min(cash, marked_equity / max_positions)
            if allocation <= 1e-12:
                skipped_capacity += 1
                continue
            points = row[key]["_value_curve"]
            first_index = next(
                (index for index, point in enumerate(points) if point["date"] >= observed),
                None,
            )
            if first_index is None:
                continue
            cash -= allocation
            first_point = points[first_index]
            initial_realized = first_point.get("realized_factor", 0.0)
            cash += allocation * initial_realized
            positions.append({
                "trade": row,
                "capital": allocation,
                "curve_index": first_index,
                "factor": first_point.get(
                    "open_value_factor", first_point["value_factor"]
                ),
                "realized_factor": initial_realized,
            })
            active_issuers.add(row["issuer_key"])
            accepted += 1

        equity = cash + sum(
            position["capital"] * position["factor"] for position in positions
        )
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1 if peak else -1)
        equity_curve.append((observed, equity))

    final_equity = equity_curve[-1][1] if equity_curve else 1.0
    return {
        "signals": len(candidates),
        "accepted_entries": accepted,
        "skipped_capacity": skipped_capacity,
        "skipped_duplicate_issuer": skipped_duplicate,
        "max_positions": max_positions,
        "open_positions_at_end": len(positions),
        "ending_cash_pct": round(cash / final_equity * 100, 2) if final_equity else None,
        "total_return_pct": round((final_equity - 1) * 100, 2),
        "max_drawdown_pct": round(max_drawdown * 100, 2),
        "daily_mark_to_market": True,
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
    max_positions: int = 20,
    policy: dict | None = None,
) -> dict:
    selected_policy = dict(policy or EXIT_POLICY_V1)
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
            entry_closeadj, exit_cost, policy=selected_policy,
        )
        baseline = _baseline_trade(
            root, path, entry_basis, exit_cost, policy=selected_policy
        )
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
        candidate_portfolio = _portfolio_replay(
            selected, "candidate", max_positions=max_positions
        )
        baseline_portfolio = _portfolio_replay(
            selected, "baseline", max_positions=max_positions
        )
        partitions[name]["portfolio"] = {
            "candidate": candidate_portfolio,
            "baseline_730_day_hold": baseline_portfolio,
            "return_change_pp": round(
                candidate_portfolio["total_return_pct"]
                - baseline_portfolio["total_return_pct"], 2
            ),
            "drawdown_change_pp": round(
                candidate_portfolio["max_drawdown_pct"]
                - baseline_portfolio["max_drawdown_pct"], 2
            ),
        }
    validation = partitions["validation"]
    final = partitions["final_unseen"]
    checks = [
        {
            "id": "sample",
            "passed": final["candidate"]["completed"] >= 20,
            "rule": "at least 20 completed final-test episodes",
        },
        {
            "id": "validation_mean_return",
            "passed": (validation["mean_return_change_pp"] or -999) >= 0,
            "rule": "validation episode mean return is not below hold",
        },
        {
            "id": "validation_loss_tail",
            "passed": (
                validation["candidate"]["loss_tail_p05_pct"] is not None
                and validation["baseline_730_day_hold"]["loss_tail_p05_pct"] is not None
                and validation["candidate"]["loss_tail_p05_pct"]
                >= validation["baseline_730_day_hold"]["loss_tail_p05_pct"]
            ),
            "rule": "validation fifth-percentile loss boundary is not worse",
        },
        {
            "id": "validation_portfolio_return",
            "passed": validation["portfolio"]["return_change_pp"] >= 0,
            "rule": "validation capacity-aware return is not below hold",
        },
        {
            "id": "validation_portfolio_drawdown",
            "passed": validation["portfolio"]["drawdown_change_pp"] >= 0,
            "rule": "validation capacity-aware maximum drawdown is not worse",
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
        {
            "id": "portfolio_return",
            "passed": final["portfolio"]["return_change_pp"] >= 0,
            "rule": "final-test capacity-aware return is not below the 730-day hold baseline",
        },
        {
            "id": "portfolio_drawdown",
            "passed": final["portfolio"]["drawdown_change_pp"] >= 0,
            "rule": "final-test capacity-aware maximum drawdown is not worse",
        },
    ]
    eligible = all(check["passed"] for check in checks)
    return {
        "version": 2,
        "policy": selected_policy,
        "research_design": {
            "unit": "continuous Buy episode",
            "split": "oldest 60% development, next 20% validation, newest 20% final unseen",
            "execution": "trigger after close; fill at next available close with configured costs",
            "baseline": "hold the full position for 730 calendar days or a terminal event",
            "licensed_price_paths_published": False,
            "portfolio_capacity_tested": True,
            "portfolio_method": (
                f"daily mark-to-market, {max_positions} equal target slots, "
                "score-ranked entries, issuer de-duplication and cash redeployment"
            ),
        },
        "episodes": len(trades),
        "unique_issuers": len({row["issuer_key"] for row in trades}),
        "partitions": partitions,
        "promotion_checks": checks,
        "promotion_eligible": eligible,
        "decision": (
            "Ready for manual promotion review: every predeclared episode and "
            "capacity-aware portfolio gate passed."
            if eligible else
            "Do not promote: one or more predeclared episode or capacity-aware "
            "portfolio gates failed on the final unseen period."
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
    observations: list[dict], connection, *, step_days: int,
    max_positions: int = 20, policy: dict | None = None,
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
    if policy is not None:
        return evaluate_exit_policy(
            observations, remapped, step_days=step_days,
            max_positions=max_positions, policy=policy,
        )

    incumbent = evaluate_exit_policy(
        observations, remapped, step_days=step_days,
        max_positions=max_positions, policy=EXIT_POLICY_V1,
    )
    evaluated = [
        evaluate_exit_policy(
            observations, remapped, step_days=step_days,
            max_positions=max_positions, policy=candidate,
        )
        for candidate in EXIT_POLICY_CHALLENGERS
    ]

    def development_rank(report: dict) -> tuple:
        development = report["partitions"]["development"]
        portfolio = development["portfolio"]
        candidate_tail = development["candidate"]["loss_tail_p05_pct"]
        baseline_tail = development["baseline_730_day_hold"]["loss_tail_p05_pct"]
        loss_improvement = (
            candidate_tail - baseline_tail
            if candidate_tail is not None and baseline_tail is not None else -999
        )
        drawdown_improvement = portfolio["drawdown_change_pp"]
        risk_guardrails = loss_improvement >= 5 and drawdown_improvement >= 5
        return (
            risk_guardrails,
            portfolio["return_change_pp"],
            development["mean_return_change_pp"],
        )

    selected = max(evaluated, key=development_rank)
    selection_rows = []
    for report in evaluated:
        development = report["partitions"]["development"]
        candidate_tail = development["candidate"]["loss_tail_p05_pct"]
        baseline_tail = development["baseline_730_day_hold"]["loss_tail_p05_pct"]
        selection_rows.append({
            "version": report["policy"]["version"],
            "episode_mean_return_change_pp": development["mean_return_change_pp"],
            "loss_tail_change_pp": (
                round(candidate_tail - baseline_tail, 2)
                if candidate_tail is not None and baseline_tail is not None else None
            ),
            "portfolio_return_change_pp": development["portfolio"]["return_change_pp"],
            "portfolio_drawdown_change_pp": development["portfolio"]["drawdown_change_pp"],
            "passed_development_risk_guardrails": development_rank(report)[0],
            "selected": report is selected,
        })
    selected["challenger_selection"] = {
        "selection_partition": "development only",
        "selection_rule": (
            "Require at least 5 percentage points of development loss-tail and "
            "portfolio-drawdown improvement, then maximize capacity-aware return "
            "change and episode mean-return change."
        ),
        "candidates": selection_rows,
    }
    selected["incumbent_v1"] = {
        "version": incumbent["policy"]["version"],
        "promotion_eligible": incumbent["promotion_eligible"],
        "decision": incumbent["decision"],
        "partitions": incumbent["partitions"],
        "promotion_checks": incumbent["promotion_checks"],
    }
    return selected
