"""Controlled scoring-model ablation over identical point-in-time cohorts."""
from __future__ import annotations

from .harness import run_rolling


VARIANTS = {
    "original": {
        "ps_direction_fix": False,
        "coverage_gates": False,
        "pb_applicability": False,
        "valuation_weights": False,
    },
    "ps_fix_only": {
        "ps_direction_fix": True,
        "coverage_gates": False,
        "pb_applicability": False,
        "valuation_weights": False,
    },
    "ps_fix_plus_coverage": {
        "ps_direction_fix": True,
        "coverage_gates": True,
        "pb_applicability": False,
        "valuation_weights": False,
    },
    "ps_fix_plus_pb_applicability": {
        "ps_direction_fix": True,
        "coverage_gates": False,
        "pb_applicability": True,
        "valuation_weights": False,
    },
    "all_changes": {
        "ps_direction_fix": True,
        "coverage_gates": True,
        "pb_applicability": True,
        "valuation_weights": True,
    },
}


def run_ablation(store, cfg, hold_days=30, step_days=14, min_names=20,
                 screened_only=True, warmup_days=300, bootstrap=500):
    results = {}
    for name, features in VARIANTS.items():
        settings = {
            "margin_of_safety_pct": 15,
            "target_upside_pct": 30,
            "model_features": features,
        }
        results[name] = run_rolling(
            store, cfg, hold_days=hold_days, step_days=step_days,
            min_names=min_names, settings=settings,
            screened_only=screened_only, warmup_days=warmup_days,
            bootstrap=bootstrap,
        )
    return {
        "variants": VARIANTS,
        "hold_days": hold_days,
        "step_days": step_days,
        "screened_only": screened_only,
        "results": results,
    }
