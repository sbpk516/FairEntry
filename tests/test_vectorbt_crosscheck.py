from __future__ import annotations

import duckdb

from fairentry.backtest.vectorbt_crosscheck import crosscheck


def _observation(return_pct=9.67):
    return {
        "observation_id": "obs-1",
        "ticker": "ABC",
        "decision_date": "2025-01-03",
        "entry_date": "2025-01-06",
        "verdict": "Buy",
        "execution": {"entry_cost_bps": 15, "exit_cost_bps": 15},
        "horizons": {
            "30": {
                "date": "2025-02-05",
                "return_pct": return_pct,
            }
        },
    }


def _connection():
    connection = duckdb.connect(":memory:")
    connection.execute(
        "CREATE TABLE sfa_prices(ticker VARCHAR,date DATE,closeadj DOUBLE)"
    )
    connection.execute(
        "CREATE TABLE sfa_actions("
        "ticker VARCHAR,date DATE,action VARCHAR,value DOUBLE,contraticker VARCHAR)"
    )
    connection.executemany("INSERT INTO sfa_prices VALUES (?,?,?)", [
        ("ABC", "2025-01-03", 99),
        ("ABC", "2025-01-06", 100),
        ("ABC", "2025-02-04", 109),
        ("ABC", "2025-02-05", 110),
    ])
    return connection


def test_vectorbt_independently_matches_dates_fees_and_return():
    connection = _connection()
    report = crosscheck([_observation()], connection, horizons={30})
    connection.close()

    assert report["ok"] is True
    assert report["requested_comparisons"] == 1
    assert report["compared"] == 1
    assert report["mismatch_count"] == 0
    assert report["coverage_pct"] == 100.0
    assert report["maximum_absolute_return_difference_pct"] < 0.01
    assert report["by_horizon"]["30"]["comparisons"] == 1
    assert report["by_horizon"]["30"]["fairentry_mean_return_pct"] == 9.67
    assert report["by_horizon"]["30"]["vectorbt_mean_return_pct"] == 9.6705


def test_vectorbt_crosscheck_rejects_a_material_return_disagreement():
    connection = _connection()
    report = crosscheck([_observation(return_pct=12.0)], connection, horizons={30})
    connection.close()

    assert report["ok"] is False
    assert report["mismatch_count"] == 1
    assert report["mismatch_examples"][0]["reasons"] == ["return_pct"]


def test_vectorbt_crosscheck_prices_a_declared_terminal_exit():
    connection = _connection()
    connection.execute("INSERT INTO sfa_prices VALUES ('ABC','2025-01-20',105)")
    connection.execute(
        "INSERT INTO sfa_actions VALUES "
        "('ABC','2025-01-20','acquisitionby',NULL,'XYZ')"
    )
    row = _observation(return_pct=4.69)
    row["horizons"]["30"].update({
        "date": "2025-01-20",
        "status": "closed_at_terminal_event",
        "terminal_action": "acquisitionby",
    })
    connection.execute("DELETE FROM sfa_prices WHERE date='2025-02-05'")
    report = crosscheck([row], connection, horizons={30})
    connection.close()

    assert report["ok"] is True
    assert report["coverage_pct"] == 100.0
    assert report["exit_modes"] == {"terminal_event": 1}


def test_vectorbt_crosscheck_applies_bankruptcy_total_loss_without_final_quote():
    connection = _connection()
    connection.execute(
        "INSERT INTO sfa_actions VALUES "
        "('ABC','2025-01-20','bankruptcyliquidation',NULL,NULL)"
    )
    row = _observation(return_pct=-100.0)
    row["horizons"]["30"].update({
        "date": "2025-01-20",
        "status": "closed_at_terminal_event",
        "terminal_action": "bankruptcyliquidation",
    })
    report = crosscheck([row], connection, horizons={30})
    connection.close()

    assert report["ok"] is True
    assert report["coverage_pct"] == 100.0
    assert report["exit_modes"] == {"terminal_event": 1}
