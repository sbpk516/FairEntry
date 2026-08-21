from fairentry.qualitative import attention_level, normalize_observation


def test_high_confidence_high_impact_negative_requires_action_without_points():
    row = normalize_observation({
        "direction": "negative", "impact": "high", "confidence": "high",
        "event_status": "confirmed", "affected_area": "operations",
    }, category="risk", subcategory="operations")
    assert row["attention_level"] == "action_required"
    assert row["score_effect"] == 0
    assert row["verdict_effect"] == "none"
    assert row["quantifiable"] is False


def test_unknown_materiality_is_not_silently_given_a_weight():
    row = normalize_observation({"status": "unknown"}, category="catalysts", subcategory="policy")
    assert row["direction"] == "uncertain"
    assert row["impact"] == "unknown"
    assert row["attention_level"] == "note"
    assert row["score_effect"] == 0


def test_attention_matrix_keeps_low_confidence_high_impact_as_monitoring():
    assert attention_level("negative", "high", "low") == "closely_monitor"
    assert attention_level("negative", "critical", "low") == "immediate_review"
    assert attention_level("positive", "critical", "high") == "note"
