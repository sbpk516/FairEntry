from fairentry.analytics.valuation_context import build_valuation_context


def test_unvalidated_valuation_context_is_separate_and_never_scored():
    result = build_valuation_context(
        {"industry": "Software - Application"},
        {"beta": {"value": 1.0}, "debt_eq": {"value": 0.3}},
    )

    assert result["backtestable"] is False
    assert result["score_effect"] == 0
    assert result["verdict_effect"] == "none"
    assert isinstance(result["experimental_context_score"], float)
    full_wacc = next(row for row in result["factors"] if row["id"] == "full_wacc")
    recurring = next(row for row in result["factors"]
                     if row["id"] == "recurring_revenue_quality")
    assert full_wacc["experimental_score"] is None
    assert full_wacc["status"] == "not backtestable"
    assert recurring["experimental_score"] == 80.0
