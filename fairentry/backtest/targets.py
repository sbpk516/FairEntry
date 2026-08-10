"""Compatibility wrapper around the shared live/backtest target engine."""
from ..scoring.targets import build_target_plan


def targets_for(record: dict, metrics: dict, strategy) -> dict:
    targets = build_target_plan(
        record, metrics, minimum_upside_pct=strategy.minimum_upside_pct,
        maximum_upside_pct=strategy.maximum_upside_pct,
        expiry_days=strategy.target_expiry_days, historical=True,
        practical_policy=strategy.practical_target_policy)["targets"]
    return {name: target for name, target in targets.items()
            if name in strategy.target_models}
