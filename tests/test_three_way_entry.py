from fairentry.backtest.three_way_entry import (
    fundamentals_strong,
    near_average,
    valuation_aligned,
)


def _row():
    return {
        "raw_close": 90,
        "categories": [{"id": key, "score": 75} for key in (
            "quality", "survival", "growth"
        )],
        "valuation": {"fair_low": 80, "fair_base": 100, "method_count": 1,
                      "fair_high": 120, "buy_zone": 85},
        "research_factors": {"ema_40week_distance_pct": 2.5},
        "vetoes": [],
    }


def test_three_way_predicates_are_independent_and_research_only():
    row = _row()
    assert fundamentals_strong(row)
    assert valuation_aligned(row, "inside_fair_range")
    assert valuation_aligned(row, "at_or_below_fair_base")
    assert not valuation_aligned(row, "at_or_below_buy_zone")
    assert near_average(row, "ema_40week_distance_pct", 3)
    assert not near_average(row, "ema_40week_distance_pct", 2)


def test_veto_or_weak_category_prevents_fundamental_alignment():
    row = _row()
    row["vetoes"] = [{"id": "distress"}]
    assert not fundamentals_strong(row)


def test_valuation_alignment_requires_a_replayable_fair_value_method():
    row = _row()
    row["valuation"]["method_count"] = 0
    assert not valuation_aligned(row, "at_or_below_fair_base")
    row["vetoes"] = []
    row["categories"][0]["score"] = 69
    assert not fundamentals_strong(row)
