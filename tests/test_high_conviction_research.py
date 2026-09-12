from fairentry.analytics.business_durability import build_business_durability
from fairentry.analytics.high_conviction import build_high_conviction_research
from fairentry.analytics.stress_resilience import build_stress_resilience
from fairentry.backtest.high_conviction_research import evaluate_replayable_subset


def _metrics(**values):
    return {key: {"value": value} for key, value in values.items()}


def test_business_durability_is_transparent_and_never_scored():
    result = build_business_durability(
        _metrics(pfcf_ratio=16, roic=18, oper_margin=15, debt_eq=.3,
                 debt_to_assets_change_yoy_pp=-2, current_ratio=1.8,
                 altman_z=4, share_count_yoy=0),
        {"rev_growth_qoq": [12, 13, 14, 15], "gross_margin": [40, 41, 42],
         "oper_margin": [12, 13, 15], "pfcf_ratio": [20, 18, 16]},
    )

    assert result["status"] == "strong"
    assert result["score_effect"] == 0
    assert result["verdict_effect"] == "none"
    assert result["validated_for_score"] is False
    assert all(row["rule"] and row["source"] and row["backtestable"]
               for row in result["checks"])


def test_business_durability_missing_history_is_not_silently_positive():
    result = build_business_durability({}, {})

    assert result["status"] == "insufficient_data"
    assert result["agreement"]["available"] == 0
    assert all(row["state"] == "unavailable" for row in result["checks"])


def _stress_series():
    benchmark = [100.0] * 900
    stock = [100.0] * 900
    for start in (180, 520):
        for offset in range(64):
            benchmark[start + offset] = 100 - 20 * offset / 63
            stock[start + offset] = 100 - 10 * offset / 63
        for offset in range(1, 91):
            benchmark[start + 63 + offset] = 80 + 20 * offset / 90
            stock[start + 63 + offset] = 90 + 10 * offset / 90
    return stock, benchmark


def test_stress_resilience_reports_protection_and_recovery_without_score():
    stock, benchmark = _stress_series()
    result = build_stress_resilience(stock, benchmark, benchmark,
                                     observed_at="2026-08-25")

    assert result["event_count"] >= 2
    assert result["status"] in {"strong", "acceptable"}
    assert result["summary"]["median_relative_protection_pp"] >= 0
    assert result["score_effect"] == 0
    assert result["verdict_effect"] == "none"
    assert result["validated_for_score"] is False


def _qualitative(*, complete=True, negative=False):
    factors = []
    for factor_id in ("management_execution", "policy_impact"):
        factors.append({
            "id": factor_id,
            "label": factor_id.replace("_", " "),
            "status": "failed" if negative and factor_id == "policy_impact" else "satisfied",
            "direction": "negative" if negative and factor_id == "policy_impact" else "positive",
            "impact": "high" if negative else "medium",
            "confidence": "high",
            "source": "SEC filing" if complete else "AI review pending",
            "observed_at": "2026-08-20" if complete else "",
        })
    if not complete:
        factors[0]["status"] = "unknown"
    return {"categories": {"catalysts": factors}}


def _high_conviction(**overrides):
    inputs = {
        "verdict": "Buy", "price": 25.0, "price_is_fresh": True, "vetoes": [],
        "valuation_agreement": {"passes": True, "status": "pass", "explanation": "Two methods agree."},
        "business_durability": {"status": "strong", "label": "Strong durability evidence"},
        "stress_resilience": {"status": "acceptable", "label": "Acceptable prior stress recovery"},
        "entry_exit_evidence": {"entry_alignment": "constructive"},
        "qualitative_context": _qualitative(),
    }
    inputs.update(overrides)
    return build_high_conviction_research(**inputs)


def test_full_candidate_is_an_additional_zero_point_label():
    result = _high_conviction()

    assert result["status"] == "candidate"
    assert result["quantitative_core_passes"] is True
    assert result["score_effect"] == 0
    assert result["verdict_effect"] == "none"
    assert result["automatic_trade_effect"] == "none"
    assert result["validated_for_score"] is False


def test_price_check_requires_an_explicit_freshness_result():
    result = _high_conviction(price_is_fresh=None)

    row = next(row for row in result["requirements"] if row["id"] == "fresh_price")
    assert row["state"] == "unknown"
    assert "timestamp was not checked" in row["reason"]


def test_price_check_explains_the_actual_age_limit():
    result = _high_conviction(price_freshness_limit_hours=8)

    row = next(row for row in result["requirements"] if row["id"] == "fresh_price")
    assert row["state"] == "pass"
    assert "not older than 8 hours" in row["reason"]


def test_durability_and_stress_requirements_explain_the_evidence_in_numbers():
    result = _high_conviction(
        business_durability={
            "status": "stable",
            "agreement": {"supportive": 3, "cautionary": 0, "available": 5},
        },
        stress_resilience={
            "status": "strong",
            "event_count": 2,
            "summary": {
                "recovered_within_one_year_pct": 100,
                "median_recovery_days": 48,
                "median_relative_protection_pp": 6.2,
            },
        },
    )

    durability = next(row for row in result["requirements"]
                      if row["id"] == "business_durability")
    stress = next(row for row in result["requirements"]
                  if row["id"] == "stress_resilience")
    assert durability["reason"] == (
        "3 of 5 available business-health groups are supportive and 0 are cautionary."
    )
    assert "Across 2 past sector declines, 100% recovered within one year" in stress["reason"]
    assert "Past results do not guarantee the next recovery" in stress["reason"]


def test_missing_management_or_policy_research_keeps_label_incomplete():
    result = _high_conviction(qualitative_context=_qualitative(complete=False))

    assert result["status"] == "quantitative_core_pass_research_incomplete"
    row = next(row for row in result["requirements"]
               if row["id"] == "critical_research_complete")
    assert row["backtestable"] is False
    assert row["state"] == "unknown"


def test_pending_qualitative_review_is_not_mistaken_for_evidence_of_safety():
    pending = {"categories": {"catalysts": [{
        "id": "management_execution", "status": "unknown", "direction": "uncertain",
        "impact": "unknown", "confidence": "low", "source": "AI review pending",
        "observed_at": "",
    }]}}
    result = _high_conviction(qualitative_context=pending)

    row = next(row for row in result["requirements"]
               if row["id"] == "no_high_impact_qualitative_negative")
    assert row["state"] == "unknown"
    assert "missing research is not treated as a Pass" in row["reason"]


def test_high_impact_policy_negative_prevents_candidate_but_not_official_buy():
    result = _high_conviction(qualitative_context=_qualitative(negative=True))

    assert result["status"] == "not_qualified"
    assert result["verdict_effect"] == "none"
    assert result["score_effect"] == 0


def test_historical_subset_uses_only_attached_buy_date_factors():
    row = {
        "entry_price": 100,
        "vetoes": [],
        "sector": "Technology",
        "industry": "Software",
        "valuation": {"methods": [
            {"key": "peer_ps", "fair": 135, "decision_status": "tested"},
            {"key": "fcf", "fair": 145, "decision_status": "tested"},
        ]},
        "research_factors": {
            "revenue_growth_positive_quarters_pct": 100,
            "revenue_growth_change_pp": 2,
            "revenue_growth_volatility_pct": 5,
            "positive_eps_quarters_pct": 100,
            "operating_margin_change_qoq_pp": 1,
            "fcf_margin_pct": 15,
            "share_count_change_yoy_pct": 0,
            "debt_to_assets_change_yoy_pp": -2,
            "price_above_sma200_pct": 8,
            "sector_trend_score": 100,
            "market_trend_score": 100,
        },
    }

    result = evaluate_replayable_subset(row)

    assert result["passes"] is True
    assert result["stress_resilience_included"] is False
    assert result["qualitative_research_included"] is False
