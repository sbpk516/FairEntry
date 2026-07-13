"""Estimate-revision signal (C4): raising analyst targets -> bullish, cuts ->
bearish, and a single snapshot -> no score (activates as history accumulates)."""
import sqlite3

from fairentry.pipeline.export import _estimate_revisions


class _FakeStore:
    def __init__(self, rows):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.execute(
            "CREATE TABLE metrics_history(ticker,field_id,value_num,value_text,source,fetched_at)")
        self.con.executemany(
            "INSERT INTO metrics_history VALUES(?,?,?,?,?,?)", rows)


def _rows():
    return [
        ("RAISE", "target_price", 100, None, "finviz", "2026-06-01T09:00"),
        ("RAISE", "target_price", 115, None, "finviz", "2026-07-10T09:00"),
        ("CUT", "target_price", 100, None, "finviz", "2026-06-01T09:00"),
        ("CUT", "target_price", 88, None, "finviz", "2026-07-10T09:00"),
        ("FLAT", "target_price", 50, None, "finviz", "2026-07-10T09:00"),
    ]


def test_revisions_direction_and_sparsity():
    out = _estimate_revisions(_FakeStore(_rows()))
    assert out["RAISE"] > 60, out
    assert out["CUT"] < 40, out
    assert "FLAT" not in out          # only one snapshot -> no score
    assert 0 <= out["RAISE"] <= 100 and 0 <= out["CUT"] <= 100
