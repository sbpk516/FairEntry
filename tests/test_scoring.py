import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.config import load_config
from fairentry.scoring.rules import apply_rule
from fairentry.scoring.engine import score_ticker


def test_higher_better():
    s, _ = apply_rule({"type": "higher_better", "full_at": 20, "floor_at": 0}, 20)
    assert s == 100
    s, _ = apply_rule({"type": "higher_better", "full_at": 20, "floor_at": 0}, 0)
    assert s == 0
    s, _ = apply_rule({"type": "higher_better", "full_at": 20, "floor_at": 0}, 10)
    assert 49 <= s <= 51


def test_lower_better_and_band():
    s, _ = apply_rule({"type": "lower_better", "full_at": 12, "floor_at": 40}, 12)
    assert s == 100
    s, _ = apply_rule({"type": "band", "bands": [{"min": 2.6, "score": 90},
                       {"min": 1.8, "score": 55}, {"min": 0, "score": 20}]}, 3.0)
    assert s == 90


def test_missing_metric_is_na():
    s, why = apply_rule({"type": "higher_better", "full_at": 20, "floor_at": 0}, None)
    assert s is None and why == "no data"


def test_reproducible():
    """Same inputs -> identical score (deterministic core)."""
    cfg = load_config()
    sec = {"ticker": "T", "company": "Test", "sector": "Technology"}
    metrics = {"price": {"value": 100, "source": "x", "fetched_at": "2026-07-12"},
               "target_price": {"value": 130, "source": "x", "fetched_at": "2026-07-12"},
               "gross_margin": {"value": 50, "source": "x", "fetched_at": "2026-07-12"},
               "debt_eq": {"value": 0.3, "source": "x", "fetched_at": "2026-07-12"},
               "oper_margin": {"value": 25, "source": "x", "fetched_at": "2026-07-12"}}
    med = {"Technology": {"gross_margin": 40}}
    s = {"margin_of_safety_pct": 15, "target_upside_pct": 30}
    r1 = score_ticker(cfg, sec, metrics, med, s)
    r2 = score_ticker(cfg, sec, metrics, med, s)
    assert r1["preliminary"] == r2["preliminary"]
    assert r1["verdict"] == r2["verdict"]
    assert r1["base_score"] > 0
