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
    universe_mode: str = "current_survivors"
    universe_sectors_mode: str = "live_enabled"
    screened_only: bool = True
    entry: str = "snapshot_close"
    target_hit: str = "close"
    slippage_bps: float = 10
    transaction_cost_bps: float = 5
    exit_slippage_bps: float = 10
    exit_transaction_cost_bps: float = 5
    benchmark: str = "cohort_mean"
    universe_top_n: int = 1000
    market_cap_min_usd: float = 300_000_000
    price_min_usd: float = 1
    avg_dollar_volume_min_usd: float = 10_000_000
    minimum_coverage_pct: float = 50.0
    horizons_days: tuple[int, ...] = (30, 60, 90, 180, 365)
    target_models: tuple[str, ...] = ("practical", "fundamental", "technical", "blended",
                                          "fcf", "book", "peer_pe", "peer_ps", "analyst", "lynch")
    primary_target: str = "practical"
    target_expiry_days: int = 365
    minimum_upside_pct: float = 30
    maximum_upside_pct: float = 100
    practical_target_policy: str = "nearest_credible"
    data_quality_mode: str = "mostly_point_in_time"
    strict_excluded_sources: tuple[str, ...] = ("seed_const",)
    tuning_promotion: str = "manual"
    challenger_holds_days: tuple[int, ...] = (20, 30, 60, 90, 180)
    tuning_objective: str = "one_year_buy_episode_targets"
    tuning_lower_gain_pct: float = 25
    tuning_primary_gain_pct: float = 30
    tuning_upper_gain_pct: float = 35
    tuning_horizon_days: int = 365
    tuning_large_loss_pct: float = -20
    tuning_severe_drawdown_pct: float = -30
    tuning_minimum_completed_episodes: int = 50
    tuning_minimum_unique_issuers: int = 30
    tuning_minimum_split_episodes: int = 10
    tuning_candidate_count: int = 1000
    tuning_weight_ranges: dict = field(default_factory=lambda: {
        "quality": (15, 30),
        "survival": (3, 10),
        "growth": (25, 45),
        "valuation": (12, 30),
        "confirmation": (10, 25),
    })
    portfolio_max_positions: int = 20
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        supported = {
            "universe_mode": ({"current_survivors", "seeded", "sharadar_point_in_time"}, self.universe_mode),
            "universe_sectors_mode": ({"live_enabled", "all"}, self.universe_sectors_mode),
            "entry": ({"snapshot_close", "next_close"}, self.entry),
            "target_hit": ({"close"}, self.target_hit),
            "benchmark": ({"cohort_mean", "spy_total_return"}, self.benchmark),
            "data_quality_mode": ({"strict", "mostly_point_in_time", "experimental"},
                                  self.data_quality_mode),
            "practical_target_policy": ({"nearest_credible", "fundamental_first"},
                                         self.practical_target_policy),
        }
        for field_name, (allowed, value) in supported.items():
            if value not in allowed:
                raise ValueError(f"unsupported backtest {field_name}={value!r}; supported: {sorted(allowed)}")
        if not self.target_models:
            raise ValueError("backtest target_models must not be empty")
        if self.primary_target not in self.target_models:
            raise ValueError("backtest primary_target must be included in target_models")
        if min(self.slippage_bps, self.transaction_cost_bps,
               self.exit_slippage_bps, self.exit_transaction_cost_bps) < 0:
            raise ValueError("backtest execution costs must be non-negative")
        if self.target_expiry_days <= 0 or any(h <= 0 for h in self.horizons_days):
            raise ValueError("backtest horizons and target expiry must be positive")
        if self.universe_top_n <= 0 or not 0 <= self.minimum_coverage_pct <= 100:
            raise ValueError("backtest universe_top_n/coverage must be in range")
        if min(self.market_cap_min_usd, self.price_min_usd,
               self.avg_dollar_volume_min_usd) < 0:
            raise ValueError("backtest universe floors must be non-negative")
        if self.portfolio_max_positions <= 0:
            raise ValueError("backtest portfolio_max_positions must be positive")
        if self.tuning_objective != "one_year_buy_episode_targets":
            raise ValueError(f"unsupported tuning objective={self.tuning_objective!r}")
        if not (0 < self.tuning_lower_gain_pct <= self.tuning_primary_gain_pct
                <= self.tuning_upper_gain_pct):
            raise ValueError("tuning lower, primary and upper gain targets must be positive and ordered")
        if self.tuning_horizon_days <= 0 or min(
            self.tuning_minimum_completed_episodes,
            self.tuning_minimum_unique_issuers,
            self.tuning_minimum_split_episodes,
            self.tuning_candidate_count,
        ) <= 0:
            raise ValueError("tuning horizon, sample minimums and candidate count must be positive")
        if not self.tuning_weight_ranges:
            raise ValueError("tuning weight ranges must not be empty")
        for category, bounds in self.tuning_weight_ranges.items():
            if len(bounds) != 2 or bounds[0] < 0 or bounds[0] > bounds[1]:
                raise ValueError(f"invalid tuning weight range for {category}")

    @property
    def strategy_id(self) -> str:
        contract = asdict(self)
        # The absolute source path is provenance, not strategy behavior; excluding
        # it keeps the same contract stable across machines.
        contract["metadata"] = {key: value for key, value in contract["metadata"].items()
                                if key != "source"}
        payload = json.dumps(contract, sort_keys=True, separators=(",", ":"))
        return f"{self.name}-{hashlib.sha256(payload.encode()).hexdigest()[:10]}"

    def to_dict(self) -> dict:
        return {**asdict(self), "strategy_id": self.strategy_id}


def load_strategy(path: str | Path | None = None) -> BacktestStrategy:
    path = Path(path) if path else Path(__file__).resolve().parents[2] / "config" / "backtest.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return BacktestStrategy(
        version=int(raw.get("version", 1)), name=raw.get("name", "fairentry_evidence_v1"),
        universe_mode=raw.get("universe", {}).get("mode", "current_survivors"),
        universe_sectors_mode=raw.get("universe", {}).get("sectors", "live_enabled"),
        screened_only=raw.get("universe", {}).get("screened_only", True),
        entry=raw.get("execution", {}).get("entry", "snapshot_close"),
        target_hit=raw.get("execution", {}).get("target_hit", "close"),
        slippage_bps=float(raw.get("execution", {}).get("slippage_bps", 10)),
        transaction_cost_bps=float(raw.get("execution", {}).get("transaction_cost_bps", 5)),
        exit_slippage_bps=float(raw.get("execution", {}).get("exit_slippage_bps", 10)),
        exit_transaction_cost_bps=float(raw.get("execution", {}).get("exit_transaction_cost_bps", 5)),
        benchmark=raw.get("benchmark", {}).get("mode", "cohort_mean"),
        universe_top_n=int(raw.get("universe", {}).get("top_n", 1000)),
        market_cap_min_usd=float(raw.get("universe", {}).get("market_cap_min_usd", 300_000_000)),
        price_min_usd=float(raw.get("universe", {}).get("price_min_usd", 1)),
        avg_dollar_volume_min_usd=float(raw.get("universe", {}).get("avg_dollar_volume_min_usd", 10_000_000)),
        minimum_coverage_pct=float(raw.get("data_quality", {}).get("minimum_coverage_pct", 50)),
        horizons_days=tuple(raw.get("horizons_days", [30, 60, 90, 180, 365])),
        target_models=tuple(raw.get("target", {}).get("models", ["practical", "fundamental", "technical", "blended",
                                                               "fcf", "book", "peer_pe", "peer_ps", "analyst", "lynch"])),
        primary_target=raw.get("target", {}).get("primary", "practical"),
        target_expiry_days=int(raw.get("target", {}).get("expiry_days", 365)),
        minimum_upside_pct=float(raw.get("target", {}).get("minimum_upside_pct", 30)),
        maximum_upside_pct=float(raw.get("target", {}).get("maximum_upside_pct", 100)),
        practical_target_policy=raw.get("target", {}).get("practical_policy", "nearest_credible"),
        data_quality_mode=raw.get("data_quality", {}).get("mode", "mostly_point_in_time"),
        strict_excluded_sources=tuple(raw.get("data_quality", {}).get("strict_excluded_sources", ["seed_const"])),
        tuning_promotion=raw.get("tuning", {}).get("promotion", "manual"),
        challenger_holds_days=tuple(raw.get("tuning", {}).get("challenger_holds_days", [20, 30, 60])),
        tuning_objective=raw.get("tuning", {}).get("objective", "one_year_buy_episode_targets"),
        tuning_lower_gain_pct=float(raw.get("tuning", {}).get("lower_gain_pct", 25)),
        tuning_primary_gain_pct=float(raw.get("tuning", {}).get("primary_gain_pct", 30)),
        tuning_upper_gain_pct=float(raw.get("tuning", {}).get(
            "upper_gain_pct", raw.get("tuning", {}).get("secondary_gain_pct", 35)
        )),
        tuning_horizon_days=int(raw.get("tuning", {}).get("horizon_days", 365)),
        tuning_large_loss_pct=float(raw.get("tuning", {}).get("large_loss_pct", -20)),
        tuning_severe_drawdown_pct=float(raw.get("tuning", {}).get("severe_drawdown_pct", -30)),
        tuning_minimum_completed_episodes=int(raw.get("tuning", {}).get("minimum_completed_episodes", 50)),
        tuning_minimum_unique_issuers=int(raw.get("tuning", {}).get("minimum_unique_issuers", 30)),
        tuning_minimum_split_episodes=int(raw.get("tuning", {}).get("minimum_split_episodes", 10)),
        tuning_candidate_count=int(raw.get("tuning", {}).get("candidate_count", 1000)),
        tuning_weight_ranges={
            key: tuple(value)
            for key, value in raw.get("tuning", {}).get("weight_ranges", {
                "quality": [15, 30], "survival": [3, 10], "growth": [25, 45],
                "valuation": [12, 30], "confirmation": [10, 25],
            }).items()
        },
        portfolio_max_positions=int(raw.get("portfolio", {}).get("max_positions", 20)),
        metadata={"source": str(path)},
    )
