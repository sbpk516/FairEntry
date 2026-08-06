"""Compatibility wrapper around the shared live/backtest target engine."""
from ..scoring.targets import build_target_plan


def targets_for(record: dict, metrics: dict, strategy) -> dict:
    return build_target_plan(
        record, metrics, minimum_upside_pct=strategy.minimum_upside_pct,
        maximum_upside_pct=strategy.maximum_upside_pct,
        expiry_days=strategy.target_expiry_days, historical=True)["targets"]
