import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fairentry.store.db import Store
from fairentry.backtest.harness import run
from fairentry.tracking import record


def _board(price=100, verdict="Buy", score=76):
    return {"stocks": [{
        "ticker": "TEST", "company": "Test Co", "sector": "Technology", "country": "USA",
        "price": price, "score": score, "verdict": verdict,
        "strategy": ["growth"], "labels": [["Quality: strong", "good"]],
        "soft_gates": [], "vetoes": [],
        "action": {"action": "Buy Now"},
    }]}


def test_record_writes_one_signal_per_ticker_strategy_day(tmp_path):
    with Store(tmp_path / "test.db") as store:
        store.set_score_result("TEST", "quality_growth", 74, 76, "Buy", {})
        record(store, _board(price=100, verdict="Buy", score=76))

        store.set_score_result("TEST", "quality_growth", 70, 70, "Watch", {})
        record(store, _board(price=104, verdict="Watch", score=70))

        rows = store.con.execute("SELECT * FROM signal_events").fetchall()
        assert len(rows) == 1
        assert rows[0]["ticker"] == "TEST"
        assert rows[0]["strategy"] == "quality_growth"
        assert rows[0]["verdict"] == "Watch"
        assert rows[0]["price"] == 104


def test_backtest_uses_signal_events(tmp_path):
    with Store(tmp_path / "test.db") as store:
        store.set_metric("TEST", "price", 100, "test", fetched_at="2026-01-01T00:00:00+00:00")
        store.set_metric("TEST", "price", 112, "test", fetched_at="2026-01-09T00:00:00+00:00")
        store.con.execute(
            "INSERT INTO signal_events(signal_date,run_at,ticker,strategy,price,score,verdict,action) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("2026-01-01", "2026-01-01T00:00:00+00:00", "TEST", "quality_growth",
             100, 76, "Buy", "Buy Now"))
        store.commit()

        res = run(store, cfg=None)

        assert res["ok"] is True
        assert res["mode"] == "signal_events"
        assert res["horizons"]["1w"]["by_verdict"]["Buy"]["avg_return_pct"] == 12
