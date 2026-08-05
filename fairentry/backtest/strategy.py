"""Versioned, serialisable backtest strategy contract."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

import yaml


@dataclass(frozen=True)
class BacktestStrategy:
    version: int = 1
    name: str = "fairentry_evidence_v1"
    screened_only: bool = True
    entry: str = "snapshot_close"
    target_hit: str = "close"
    slippage_bps: float = 10
    transaction_cost_bps: float = 5
    benchmark: str = "cohort_mean"
    horizons_days: tuple[int, ...] = (30, 60, 90, 180, 365)
    target_models: tuple[str, ...] = ("fundamental", "technical", "blended")
    primary_target: str = "fundamental"
    target_expiry_days: int = 365
    minimum_upside_pct: float = 10
    maximum_upside_pct: float = 100
    data_quality_mode: str = "mostly_point_in_time"
    strict_excluded_sources: tuple[str, ...] = ("seed_const",)
    tuning_promotion: str = "manual"
    challenger_holds_days: tuple[int, ...] = (20, 30, 60, 90, 180)
    metadata: dict = field(default_factory=dict)

    @property
    def strategy_id(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return f"{self.name}-{hashlib.sha256(payload.encode()).hexdigest()[:10]}"

    def to_dict(self) -> dict:
        return {**asdict(self), "strategy_id": self.strategy_id}


def load_strategy(path: str | Path | None = None) -> BacktestStrategy:
    path = Path(path) if path else Path(__file__).resolve().parents[2] / "config" / "backtest.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return BacktestStrategy(
        version=int(raw.get("version", 1)), name=raw.get("name", "fairentry_evidence_v1"),
        screened_only=raw.get("universe", {}).get("screened_only", True),
        entry=raw.get("execution", {}).get("entry", "snapshot_close"),
        target_hit=raw.get("execution", {}).get("target_hit", "close"),
        slippage_bps=float(raw.get("execution", {}).get("slippage_bps", 10)),
        transaction_cost_bps=float(raw.get("execution", {}).get("transaction_cost_bps", 5)),
        benchmark=raw.get("benchmark", {}).get("mode", "cohort_mean"),
        horizons_days=tuple(raw.get("horizons_days", [30, 60, 90, 180, 365])),
        target_models=tuple(raw.get("target", {}).get("models", ["fundamental"])),
        primary_target=raw.get("target", {}).get("primary", "fundamental"),
        target_expiry_days=int(raw.get("target", {}).get("expiry_days", 365)),
        minimum_upside_pct=float(raw.get("target", {}).get("minimum_upside_pct", 10)),
        maximum_upside_pct=float(raw.get("target", {}).get("maximum_upside_pct", 100)),
        data_quality_mode=raw.get("data_quality", {}).get("mode", "mostly_point_in_time"),
        strict_excluded_sources=tuple(raw.get("data_quality", {}).get("strict_excluded_sources", ["seed_const"])),
        tuning_promotion=raw.get("tuning", {}).get("promotion", "manual"),
        challenger_holds_days=tuple(raw.get("tuning", {}).get("challenger_holds_days", [20, 30, 60])),
        metadata={"source": str(path)},
    )

