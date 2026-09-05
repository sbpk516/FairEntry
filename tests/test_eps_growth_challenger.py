from fairentry.backtest.eps_growth_challenger import (
    eps_cagr_3y,
    eps_growth_score,
    score_challenger,
    ttm_eps_growth,
    verdict,
)


def _row(baseline="Buy", growth=10):
    factors = {} if growth is None else {"eps_cagr_3y_pct": growth}
    return {"verdict": baseline, "research_factors": factors}


def test_eps_gate_preserves_buy_only_when_reported_growth_clears_threshold():
    assert verdict(_row(growth=10), 5) == "Buy"
    assert verdict(_row(growth=4.9), 5) == "Watch"
    assert verdict(_row(growth=None), 5) == "Watch"


def test_eps_gate_never_promotes_or_mutates_production_verdict():
    row = _row(baseline="Avoid", growth=50)
    assert verdict(row, 5) == "Avoid"
    assert row["verdict"] == "Avoid"


def test_eps_cagr_rejects_non_numeric_values():
    assert eps_cagr_3y(_row(growth=None)) is None
    assert eps_cagr_3y({"research_factors": {"eps_cagr_3y_pct": True}}) is None


def test_ttm_eps_growth_handles_recovery_and_deterioration_without_fake_cagr():
    assert ttm_eps_growth(2, -1) == {"cagr_pct": None, "state": "recovery"}
    assert ttm_eps_growth(-1, 2) == {"cagr_pct": None, "state": "deterioration"}
    assert ttm_eps_growth(1.331, 1)["cagr_pct"] == 10


def test_eps_growth_score_uses_agreed_bands_and_does_not_fake_recovery():
    assert eps_growth_score(_row(growth=20)) == 100
    assert eps_growth_score(_row(growth=10)) == 75
    assert eps_growth_score(_row(growth=5)) == 50
    assert eps_growth_score(_row(growth=0)) == 25
    assert eps_growth_score(_row(growth=-1)) == 0
    recovery = _row(growth=None)
    recovery["research_factors"]["eps_growth_state"] = "recovery"
    assert eps_growth_score(recovery) is None


def test_score_challenger_adds_small_eps_component_and_preserves_gates():
    row = _row(growth=20)
    row.update({
        "categories": [
            {"id": "quality", "score": 70},
            {"id": "survival", "score": 70},
            {"id": "growth", "score": 70},
            {"id": "valuation", "score": 70},
            {"id": "confirmation", "score": 70},
        ],
        "weights": {"quality": 22.5, "survival": 6.25, "growth": 33.75,
                    "valuation": 18.75, "confirmation": 18.75},
        "vetoes": [],
        "soft_gates": [{"id": "no_confirmation"}],
    })
    result = score_challenger(row, 20)
    assert result["challenger_growth_score"] == 76
    assert result["score"] == 72
    assert result["verdict"] == "Watch"
    assert row["verdict"] == "Buy"
