from fairentry.analytics.entry_exit_evidence import build_entry_exit_evidence


def _series(direction=1, length=280):
    closes = [100 + direction * index * .15 + direction * (index % 5) * .03
              for index in range(length)]
    highs = [value * 1.01 for value in closes]
    lows = [value * .99 for value in closes]
    return closes, highs, lows


def test_entry_exit_evidence_groups_correlated_metrics_without_scoring():
    closes, highs, lows = _series()
    benchmark = [100 + index * .03 for index in range(len(closes))]
    volumes = [1_000_000 + (100_000 if index % 2 else 0)
               for index in range(len(closes))]

    result = build_entry_exit_evidence(
        closes, volumes, benchmark, benchmark, highs=highs, lows=lows,
        observed_at="2026-08-24",
    )

    assert result["entry_alignment"] == "constructive"
    assert result["exit_pressure"] == "low"
    assert result["score_effect"] == 0
    assert result["verdict_effect"] == "none"
    assert result["automatic_trade_effect"] == "none"
    assert [family["id"] for family in result["families"]] == [
        "trend", "momentum", "volume", "volatility",
    ]
    assert all(family["vote_count"] in {0, 1} for family in result["families"])
    trend = result["families"][0]
    indicator = trend["observations"][0]
    assert trend["decision_rule"]
    assert trend["available_observations"] == 5
    assert indicator["reading"] in {"supportive", "cautionary", "mixed", "context"}
    assert indicator["lookback"]
    assert indicator["formula"]
    assert indicator["supportive_when"]
    assert indicator["cautionary_when"]
    assert indicator["source"]
    assert indicator["backtestable"] is True
    assert indicator["score_effect"] == 0
    assert indicator["verdict_effect"] == "none"
    assert result["decision_rules"]["correlation_control"]
    assert result["data_inputs"]
    assert result["backtestability"]


def test_declining_price_and_distribution_raise_exit_pressure_not_a_sell():
    closes = [200 - index * .3 - (index % 3) * .15 for index in range(280)]
    highs = [value * 1.03 for value in closes]
    lows = [value * .96 for value in closes]
    volumes = [1_000_000 + index * 5_000 for index in range(280)]
    benchmark = [100 + index * .03 for index in range(280)]

    result = build_entry_exit_evidence(
        closes, volumes, benchmark, benchmark, highs=highs, lows=lows,
    )

    assert result["entry_alignment"] == "cautionary"
    assert result["exit_pressure"] == "elevated"
    assert result["automatic_trade_effect"] == "none"
    states = {family["id"]: family["state"] for family in result["families"]}
    assert states["trend"] == "cautionary"
    assert states["momentum"] == "cautionary"
    assert states["volume"] == "cautionary"


def test_missing_history_is_unavailable_not_negative():
    result = build_entry_exit_evidence(
        [100.0] * 10, [1_000.0] * 10, [100.0] * 10, [100.0] * 10,
    )

    assert result["entry_alignment"] == "unavailable"
    assert result["exit_pressure"] == "unavailable"
    assert result["agreement"]["cautionary_count"] == 0
    assert all(family["state"] == "unavailable" for family in result["families"])
