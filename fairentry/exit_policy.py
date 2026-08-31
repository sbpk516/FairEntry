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
) -> dict:
    """Create the auditable state needed to evaluate every v1 exit rule."""
    return {
        "policy_version": EXIT_POLICY_VERSION,
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


def _action(code: str, label: str, fraction: float, *, loss_exit: bool = False) -> dict:
    return {
        "code": code,
        "label": label,
        "fraction_of_original_position": round(float(fraction), 6),
        "loss_exit": bool(loss_exit),
        "policy_version": EXIT_POLICY_VERSION,
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
        )
    elif vetoes:
        instruction = _action(
            "hard_veto", "A hard veto invalidated the investment thesis", remaining,
            loss_exit=gain_pct < 0,
        )
    elif gain_pct <= EXIT_POLICY_V1["catastrophic_loss_pct"]:
        instruction = _action(
            "catastrophic_loss",
            f"Closing return reached {gain_pct:.1f}%",
            remaining,
            loss_exit=True,
        )
    elif state["avoid_confirmations"] >= EXIT_POLICY_V1["avoid_confirmations"]:
        instruction = _action(
            "confirmed_avoid", "Avoid verdict was confirmed twice", remaining,
            loss_exit=gain_pct < 0,
        )
    elif (not state.get("profit_taken")
          and gain_pct >= EXIT_POLICY_V1["first_profit_pct"]):
        instruction = _action(
            "first_profit", "Net return reached +30%",
            min(remaining, EXIT_POLICY_V1["first_profit_fraction"]),
        )
    elif state.get("profit_taken") and remaining > 0:
        trail_floor = float(state["peak_price"]) * (
            1 - EXIT_POLICY_V1["trailing_drawdown_pct"] / 100
        )
        if float(price) <= trail_floor:
            instruction = _action(
                "trailing_profit",
                "Price closed 15% below the highest close after profit-taking",
                remaining,
                loss_exit=gain_pct < 0,
            )
    if instruction is None:
        target = state.get("practical_target")
        reached = (
            practical_target_reached
            if practical_target_reached is not None
            else isinstance(target, (int, float)) and float(price) >= float(target)
        )
        if reached:
            instruction = _action(
                "practical_target", "Frozen Practical Target was reached", remaining,
            )
    if (instruction is None and held_days >= EXIT_POLICY_V1["stagnation_days"]
            and gain_pct < EXIT_POLICY_V1["stagnation_max_return_pct"]
            and verdict != "Buy"):
        instruction = _action(
            "stagnation", "Below +10% after one year and no longer Buy", remaining,
            loss_exit=gain_pct < 0,
        )
    if instruction is None and held_days >= EXIT_POLICY_V1["maximum_holding_days"]:
        instruction = _action(
            "maximum_holding_period", "Maximum 730-day holding period reached", remaining,
            loss_exit=gain_pct < 0,
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
