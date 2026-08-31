from fairentry.exit_policy import (
    EXIT_POLICY_VERSION,
    apply_execution,
    new_position_state,
    observe_close,
)


def test_profit_is_partial_then_trailing_exit_closes_remainder():
    state = new_position_state(entry_date="2024-01-02", entry_price=100)
    action = observe_close(
        state, observed_date="2024-04-01", price=131, verdict="Buy"
    )
    assert action["code"] == "first_profit"
    assert state["remaining_fraction"] == 1
    fill = apply_execution(state, execution_date="2024-04-02", execution_price=129)
    assert fill["fraction_of_original_position"] == .5
    assert state["remaining_fraction"] == .5
    assert state["profit_taken"] is True

    assert observe_close(
        state, observed_date="2024-04-03", price=140, verdict="Buy"
    ) is None
    action = observe_close(
        state, observed_date="2024-05-01", price=119, verdict="Buy"
    )
    assert action["code"] == "trailing_profit"
    apply_execution(state, execution_date="2024-05-02", execution_price=117)
    assert state["status"] == "closed"
    assert state["exit_reason"] == "trailing_profit"


def test_hard_veto_has_precedence_over_loss_and_avoid():
    state = new_position_state(entry_date="2024-01-02", entry_price=100)
    state["avoid_confirmations"] = 1
    action = observe_close(
        state,
        observed_date="2024-02-01",
        price=70,
        verdict="Avoid",
        vetoes=["accounting_veto"],
    )
    assert action["code"] == "hard_veto"
    assert action["loss_exit"] is True


def test_catastrophic_loss_uses_close_signal_and_separate_execution_price():
    state = new_position_state(entry_date="2024-01-02", entry_price=100)
    action = observe_close(
        state, observed_date="2024-02-01", price=75, verdict="Buy"
    )
    assert action["code"] == "catastrophic_loss"
    fill = apply_execution(state, execution_date="2024-02-02", execution_price=68)
    assert fill["signal_price"] == 75
    assert fill["execution_price"] == 68
    assert state["status"] == "closed"


def test_avoid_requires_two_consecutive_observations():
    state = new_position_state(entry_date="2024-01-02", entry_price=100)
    assert observe_close(
        state, observed_date="2024-02-01", price=101, verdict="Avoid"
    ) is None
    assert observe_close(
        state, observed_date="2024-03-01", price=102, verdict="Watch"
    ) is None
    assert observe_close(
        state, observed_date="2024-04-01", price=103, verdict="Avoid"
    ) is None
    action = observe_close(
        state, observed_date="2024-05-01", price=104, verdict="Avoid"
    )
    assert action["code"] == "confirmed_avoid"


def test_stagnation_and_maximum_hold_are_deterministic():
    stagnant = new_position_state(entry_date="2024-01-01", entry_price=100)
    action = observe_close(
        stagnant, observed_date="2025-01-01", price=108, verdict="Watch"
    )
    assert action["code"] == "stagnation"

    maximum = new_position_state(entry_date="2024-01-01", entry_price=100)
    action = observe_close(
        maximum, observed_date="2025-12-31", price=150, verdict="Buy"
    )
    assert action["code"] == "first_profit"
    apply_execution(maximum, execution_date="2026-01-02", execution_price=149)
    action = observe_close(
        maximum, observed_date="2026-01-03", price=149, verdict="Buy"
    )
    assert action["code"] == "maximum_holding_period"
    assert action["policy_version"] == EXIT_POLICY_VERSION


def test_frozen_practical_target_is_not_lowered_after_entry():
    state = new_position_state(
        entry_date="2024-01-02", entry_price=100, practical_target=120
    )
    assert observe_close(
        state, observed_date="2024-02-01", price=119, verdict="Buy"
    ) is None
    action = observe_close(
        state, observed_date="2024-02-02", price=120, verdict="Buy"
    )
    assert action["code"] == "practical_target"
