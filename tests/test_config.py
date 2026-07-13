import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.config import (load_config, ConfigError, _validate_scoring,
                              _validate_when_expressions)


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


def test_when_expression_yaml_true_rejected():
    """A YAML `true` (not Python `True`) in a veto `when` is a NameError at
    runtime that the engine swallows — the veto silently never fires. It must be
    caught at load time instead (regression: the going-concern veto)."""
    sc = {"vetoes": [{"id": "gc", "when": "going_concern == true", "reason": "x"}],
          "soft_gates": []}
    errs = _validate_when_expressions(sc, {"survival": {}}, {"going_concern"})
    assert any("unknown name" in e and "gc" in e for e in errs)


def test_when_expression_metric_typo_rejected():
    sc = {"vetoes": [{"id": "t", "when": "red_flag_critical > 0", "reason": "x"}],
          "soft_gates": []}
    errs = _validate_when_expressions(sc, {}, {"red_flags_critical"})
    assert any("red_flag_critical" in e for e in errs)


def test_when_expression_valid_passes():
    sc = {"vetoes": [{"id": "ok", "when": "altman_z < 1.8 and debt_eq > 2", "reason": "x"},
                     {"id": "gc", "when": "going_concern", "reason": "x"}],
          "soft_gates": [{"id": "g", "when": "category_survival < 30", "reason": "x"}]}
    errs = _validate_when_expressions(sc, {"survival": {}}, {"altman_z", "debt_eq", "going_concern"})
    assert errs == []


def test_live_config_has_valid_when_expressions():
    """The shipped config/scoring.yaml must have no broken veto/gate expressions."""
    c = load_config()
    names = {f["id"] for f in c.fields}
    errs = _validate_when_expressions(c.scoring, c.categories, names)
    assert errs == [], errs
