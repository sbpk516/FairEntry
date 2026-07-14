"""SQLite store — the canonical source of truth. Screeners/scoring read only
from here; adapters write here. Values carry provenance (source, fetched_at)
and every write also appends to metrics_history (point-in-time).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB = ROOT / "data" / "fairentry.db"
SCHEMA = Path(__file__).with_name("schema.sql")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str = DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.path)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA.read_text(encoding="utf-8"))

    def close(self):
        self.con.commit()
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- securities ------------------------------------------------------------
    def upsert_security(self, ticker, company="", sector="", industry="", country=""):
        self.con.execute(
            "INSERT INTO securities(ticker,company,sector,industry,country,updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(ticker) DO UPDATE SET "
            "company=excluded.company, sector=excluded.sector, industry=excluded.industry, "
            "country=excluded.country, updated_at=excluded.updated_at",
            (ticker, company, sector, industry, country, _now()))

    def securities(self, sectors=None) -> list[dict]:
        q = "SELECT * FROM securities"
        args = ()
        if sectors:
            q += " WHERE sector IN (%s)" % ",".join("?" * len(sectors))
            args = tuple(sectors)
        return [dict(r) for r in self.con.execute(q, args)]

    # -- metrics ---------------------------------------------------------------
    def set_metric(self, ticker, field_id, value, source, fetched_at=None):
        fetched_at = fetched_at or _now()
        num = value if isinstance(value, (int, float)) else None
        txt = None if isinstance(value, (int, float)) else (None if value is None else str(value))
        self.con.execute(
            "INSERT INTO metrics_current(ticker,field_id,value_num,value_text,source,fetched_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(ticker,field_id) DO UPDATE SET "
            "value_num=excluded.value_num, value_text=excluded.value_text, "
            "source=excluded.source, fetched_at=excluded.fetched_at",
            (ticker, field_id, num, txt, source, fetched_at))
        self.con.execute(
            "INSERT INTO metrics_history(ticker,field_id,value_num,value_text,source,fetched_at) "
            "VALUES(?,?,?,?,?,?)",
            (ticker, field_id, num, txt, source, fetched_at))

    def metrics_for(self, ticker) -> dict:
        """Return {field_id: {value, source, fetched_at}} for a ticker."""
        out = {}
        for r in self.con.execute(
                "SELECT field_id,value_num,value_text,source,fetched_at "
                "FROM metrics_current WHERE ticker=?", (ticker,)):
            out[r["field_id"]] = {
                "value": r["value_num"] if r["value_num"] is not None else r["value_text"],
                "source": r["source"], "fetched_at": r["fetched_at"]}
        return out

    def metric_ages(self, field_id) -> dict:
        return {r["ticker"]: r["fetched_at"] for r in self.con.execute(
            "SELECT ticker,fetched_at FROM metrics_current WHERE field_id=?", (field_id,))}

    # -- fetch log -------------------------------------------------------------
    def log_fetch(self, run_id, source, ok, rows, seconds, error=""):
        self.con.execute(
            "INSERT INTO source_fetch_log(run_id,source,ok,rows,seconds,error,fetched_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (run_id, source, int(ok), rows, seconds, error, _now()))

    # -- results ---------------------------------------------------------------
    def set_screen_result(self, ticker, screener, passed, detail):
        self.con.execute(
            "INSERT INTO screen_results(ticker,screener,passed,detail_json,run_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(ticker,screener) DO UPDATE SET "
            "passed=excluded.passed, detail_json=excluded.detail_json, run_at=excluded.run_at",
            (ticker, screener, int(passed), json.dumps(detail), _now()))

    def screened(self, screener, passed_only=True) -> list[str]:
        q = "SELECT ticker FROM screen_results WHERE screener=?"
        if passed_only:
            q += " AND passed=1"
        return [r["ticker"] for r in self.con.execute(q, (screener,))]

    def set_score_result(self, ticker, strategy, base, preliminary, verdict, trace):
        self.con.execute(
            "INSERT INTO score_results(ticker,strategy,base_score,preliminary,verdict,trace_json,run_at) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(ticker,strategy) DO UPDATE SET "
            "base_score=excluded.base_score, preliminary=excluded.preliminary, "
            "verdict=excluded.verdict, trace_json=excluded.trace_json, run_at=excluded.run_at",
            (ticker, strategy, base, preliminary, verdict, json.dumps(trace), _now()))

    def score_results(self, strategy) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM score_results WHERE strategy=?", (strategy,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["trace"] = json.loads(d.pop("trace_json") or "{}")
            out.append(d)
        return out

    # -- thesis (Layer B) results ---------------------------------------------
    # Persist the LLM thesis so an AI review survives across deterministic builds
    # (the reasoning layer only runs weekly; the twice-daily builds re-attach the
    # stored thesis instead of reverting names to "not reviewed").
    def set_thesis_result(self, ticker, strategy, thesis_score, modifier,
                          thesis_json, provider, run_at=None):
        self.con.execute(
            "INSERT INTO thesis_results(ticker,strategy,thesis_score,modifier,thesis_json,provider,run_at) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(ticker,strategy) DO UPDATE SET "
            "thesis_score=excluded.thesis_score, modifier=excluded.modifier, "
            "thesis_json=excluded.thesis_json, provider=excluded.provider, run_at=excluded.run_at",
            (ticker, strategy, thesis_score, modifier, thesis_json, provider, run_at or _now()))

    def latest_theses(self) -> dict:
        """{ticker: {strategy, thesis_score, modifier, thesis_json, provider, run_at}}
        — the most recently reasoned thesis per ticker."""
        out: dict = {}
        for r in self.con.execute(
                "SELECT ticker,strategy,thesis_score,modifier,thesis_json,provider,run_at "
                "FROM thesis_results"):
            d = dict(r)
            cur = out.get(r["ticker"])
            if not cur or (d["run_at"] or "") > (cur["run_at"] or ""):
                out[r["ticker"]] = d
        return out

    def commit(self):
        self.con.commit()
