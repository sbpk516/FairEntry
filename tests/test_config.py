import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.config import load_config, ConfigError, _validate_scoring


def test_config_loads_and_validates():
    c = load_config()
    assert len(c.fields) > 10
    assert abs(sum(x["weight"] for x in c.categories.values()) - 100) < 0.5
    assert c.verdict_bands["buy"] > c.verdict_bands["watch"]
    assert c.enabled_sectors


def test_bad_weights_rejected():
    bad = {"categories": {"a": {"weight": 50, "items": [
        {"id": "x", "weight": 1, "metric": "price", "rule": {"type": "passthrough"}}]}},
        "verdict_bands": {"buy": 72, "watch": 50}}
    errs = _validate_scoring(bad, {"price"})
    assert any("sum to 50" in e for e in errs)


def test_unknown_metric_rejected():
    bad = {"categories": {"a": {"weight": 100, "items": [
        {"id": "x", "weight": 1, "metric": "does_not_exist", "rule": {"type": "passthrough"}}]}},
        "verdict_bands": {"buy": 72, "watch": 50}}
    errs = _validate_scoring(bad, {"price"})
    assert any("not in catalog" in e for e in errs)
