"""Deterministic, versioned position-exit policy shared by tracking and research.

The policy produces an instruction after an observed close.  Execution is a
separate step so historical research can fill at the next available close and
live integrations can submit an order without pretending the trigger price is
guaranteed.
"""
from __future__ import annotations

from datetime import date, datetime


EXIT_POLICY_VERSION = "exit_v1_research"

EXIT_POLICY_V1 = {
    "version": EXIT_POLICY_VERSION,
    "production_effect": "paper_tracking_and_research_only",
    "trigger_frequency": "after_close",
    "execution": "next_available_session",
    "catastrophic_loss_pct": -25.0,
    "avoid_confirmations": 2,
    "first_profit_pct": 30.0,
    "first_profit_fraction": 0.5,
    "trailing_drawdown_pct": 15.0,
    "stagnation_days": 365,
    "stagnation_max_return_pct": 10.0,
    "maximum_holding_days": 730,
    "loss_cooldown_trading_days": 30,
    "precedence": [
        "terminal_event",
        "hard_veto",
        "catastrophic_loss",
        "confirmed_avoid",
        "first_profit",
        "trailing_profit",
        "practical_target",
        "stagnation",
        "maximum_holding_period",
    ],
    "hard_veto_exit": True,
    "practical_target_exit": True,
}


def _day(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def new_position_state(
    *,
    entry_date: str,
    entry_price: float,
    practical_target: float | None = None,
    policy: dict | None = None,
) -> dict:
    """Create the auditable state needed to evaluate every v1 exit rule."""
    selected = policy or EXIT_POLICY_V1
    return {
        "policy_version": str(selected.get("version") or EXIT_POLICY_VERSION),
        "entry_date": str(entry_date)[:10],
        "entry_price": float(entry_price),
        "practical_target": (
            float(practical_target)
            if isinstance(practical_target, (int, float)) and practical_target > entry_price
            else None
        ),
        "peak_price": float(entry_price),
        "remaining_fraction": 1.0,
        "profit_taken": False,
        "avoid_confirmations": 0,
        "pending_action": None,
        "status": "open",
        "last_observed_date": None,
        "last_observed_price": None,
        "actions": [],
    }


def _action(
    code: str,
    label: str,
    fraction: float,
    *,
    loss_exit: bool = False,
    policy_version: str = EXIT_POLICY_VERSION,
) -> dict:
    return {
        "code": code,
        "label": label,
        "fraction_of_original_position": round(float(fraction), 6),
        "loss_exit": bool(loss_exit),
        "policy_version": policy_version,
    }


def observe_close(
    state: dict,
    *,
    observed_date: str,
    price: float,
    verdict: str | None = None,
    vetoes: list | tuple | None = None,
    terminal_event: dict | None = None,
    practical_target_reached: bool | None = None,
    policy: dict | None = None,
) -> dict | None:
    """Evaluate one close and return at most one instruction by precedence.

    A returned instruction is stored as pending and must be passed to
    :func:`apply_execution`.  No second trigger can fire before that happens.
    """
    if state.get("status") != "open" or state.get("pending_action"):
        return None
    if not isinstance(price, (int, float)) or price < 0:
        return None
    entry_price = float(state["entry_price"])
    selected = policy or EXIT_POLICY_V1
    policy_version = str(selected.get("version") or EXIT_POLICY_VERSION)
    remaining = float(state.get("remaining_fraction", 1.0))
    held_days = max(0, (_day(observed_date) - _day(state["entry_date"])).days)
    gain_pct = (float(price) / entry_price - 1) * 100 if entry_price else -100.0
    state["last_observed_date"] = str(observed_date)[:10]
    state["last_observed_price"] = float(price)
    state["peak_price"] = max(float(state.get("peak_price") or price), float(price))
    # ``None`` means no new recommendation evaluation occurred on this close;
    # it must not accidentally confirm or reset a monthly verdict.
    if verdict is not None:
        state["avoid_confirmations"] = (
            int(state.get("avoid_confirmations", 0)) + 1
            if verdict == "Avoid" else 0
        )

    instruction = None
    if terminal_event:
        instruction = _action(
            "terminal_event", "Trading ended or the security changed", remaining,
            loss_exit=terminal_event.get("terminal_return_policy") == "zero",
            policy_version=policy_version,
        )
    elif vetoes and selected.get("hard_veto_exit", True):
        instruction = _action(
            "hard_veto", "A hard veto invalidated the investment thesis", remaining,
            loss_exit=gain_pct < 0,
            policy_version=policy_version,
        )
    elif gain_pct <= selected["catastrophic_loss_pct"]:
        instruction = _action(
            "catastrophic_loss",
            f"Closing return reached {gain_pct:.1f}%",
            remaining,
            loss_exit=True,
            policy_version=policy_version,
        )
    elif state["avoid_confirmations"] >= selected["avoid_confirmations"]:
        instruction = _action(
            "confirmed_avoid",
            f"Avoid verdict was confirmed {selected['avoid_confirmations']} times",
            remaining,
            loss_exit=gain_pct < 0,
            policy_version=policy_version,
        )
    elif (not state.get("profit_taken")
          and gain_pct >= selected["first_profit_pct"]):
        instruction = _action(
            "first_profit", f"Net return reached +{selected['first_profit_pct']:g}%",
            min(remaining, selected["first_profit_fraction"]),
            policy_version=policy_version,
        )
    elif state.get("profit_taken") and remaining > 0:
        trail_floor = float(state["peak_price"]) * (
            1 - selected["trailing_drawdown_pct"] / 100
        )
        if float(price) <= trail_floor:
            instruction = _action(
                "trailing_profit",
                f"Price closed {selected['trailing_drawdown_pct']:g}% below the "
                "highest close after profit-taking",
                remaining,
                loss_exit=gain_pct < 0,
                policy_version=policy_version,
            )
    if instruction is None:
        target = state.get("practical_target")
        reached = (
            practical_target_reached
            if practical_target_reached is not None
            else isinstance(target, (int, float)) and float(price) >= float(target)
        )
        if reached and selected.get("practical_target_exit", True):
            instruction = _action(
                "practical_target", "Frozen Practical Target was reached", remaining,
                policy_version=policy_version,
            )
    if (instruction is None and held_days >= selected["stagnation_days"]
            and gain_pct < selected["stagnation_max_return_pct"]
            and verdict != "Buy"):
        instruction = _action(
            "stagnation",
            f"Below +{selected['stagnation_max_return_pct']:g}% after "
            f"{selected['stagnation_days']} days and no longer Buy",
            remaining,
            loss_exit=gain_pct < 0,
            policy_version=policy_version,
        )
    if instruction is None and held_days >= selected["maximum_holding_days"]:
        instruction = _action(
            "maximum_holding_period",
            f"Maximum {selected['maximum_holding_days']}-day holding period reached",
            remaining,
            loss_exit=gain_pct < 0,
            policy_version=policy_version,
        )
    if instruction:
        instruction.update({
            "signal_date": str(observed_date)[:10],
            "signal_price": round(float(price), 6),
            "gain_at_signal_pct": round(gain_pct, 4),
            "held_days_at_signal": held_days,
        })
        state["pending_action"] = instruction
    return instruction


def apply_execution(
    state: dict,
    *,
    execution_date: str,
    execution_price: float,
) -> dict | None:
    """Apply the pending instruction at an actual, separately supplied price."""
    instruction = state.get("pending_action")
    if not instruction:
        return None
    fill = dict(instruction)
    fill["execution_date"] = str(execution_date)[:10]
    fill["execution_price"] = round(float(execution_price), 6)
    fraction = min(
        float(state.get("remaining_fraction", 1.0)),
        float(fill["fraction_of_original_position"]),
    )
    state["remaining_fraction"] = round(
        max(0.0, float(state.get("remaining_fraction", 1.0)) - fraction), 6
    )
    if fill["code"] == "first_profit" and state["remaining_fraction"] > 0:
        state["profit_taken"] = True
        state["peak_price"] = max(
            float(state.get("peak_price") or execution_price), float(execution_price)
        )
    if state["remaining_fraction"] <= 0:
        state["status"] = "closed"
        state["closed_at"] = fill["execution_date"]
        state["exit_reason"] = fill["code"]
    state.setdefault("actions", []).append(fill)
    state["pending_action"] = None
    return fill
