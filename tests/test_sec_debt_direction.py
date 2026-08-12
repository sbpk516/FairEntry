from fairentry.adapters.sec_edgar import debt_direction_from_facts


def _fact(concept, current, previous):
    return {
        "label": concept,
        "units": {"USD": [
            {"form": "10-K", "fp": "FY", "end": "2025-12-31",
             "filed": "2026-02-15", "val": current},
            {"form": "10-K", "fp": "FY", "end": "2024-12-31",
             "filed": "2025-02-15", "val": previous},
        ]},
    }


def test_debt_direction_compares_debt_burden_not_raw_debt_only():
    facts = {"facts": {"us-gaap": {
        "LongTermDebtNoncurrent": _fact("Debt", 20, 30),
        "Assets": _fact("Assets", 100, 100),
    }}}
    result = debt_direction_from_facts(facts)
    assert result == {
        "debt_to_assets_pct": 20.0,
        "debt_to_assets_yago_pct": 30.0,
        "debt_to_assets_change_yoy_pp": -10.0,
    }


def test_debt_direction_needs_two_matching_periods():
    assert debt_direction_from_facts({"facts": {"us-gaap": {}}}) == {}
