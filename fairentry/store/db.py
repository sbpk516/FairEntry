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

    def replace_universe(self, source: str, tickers, refreshed_at=None):
        """Atomically mark the members from a successful source refresh.

        Historical securities and metrics are intentionally left untouched.
        """
        refreshed_at = refreshed_at or _now()
        members = sorted({str(t).strip() for t in tickers if str(t).strip()})
        with self.con:
            self.con.execute("DELETE FROM universe_membership WHERE source=?", (source,))
            self.con.executemany(
                "INSERT INTO universe_membership(source,ticker,refreshed_at) VALUES(?,?,?)",
                ((source, ticker, refreshed_at) for ticker in members))
            self.con.execute(
                "INSERT INTO universe_refresh(source,refreshed_at,member_count) VALUES(?,?,?) "
                "ON CONFLICT(source) DO UPDATE SET refreshed_at=excluded.refreshed_at, "
                "member_count=excluded.member_count",
                (source, refreshed_at, len(members)))

    def active_securities(self, source="finviz", sectors=None) -> list[dict]:
        """Current source universe, with a migration-safe legacy fallback."""
        has_snapshot = self.con.execute(
            "SELECT 1 FROM universe_refresh WHERE source=?", (source,)).fetchone()
        if not has_snapshot:
            return self.securities(sectors)
        q = ("SELECT s.* FROM securities s JOIN universe_membership u "
             "ON u.ticker=s.ticker WHERE u.source=?")
        args = [source]
        if sectors:
            q += " AND s.sector IN (%s)" % ",".join("?" * len(sectors))
            args.extend(sectors)
        return [dict(r) for r in self.con.execute(q, tuple(args))]

    def universe_refreshed_at(self, source="finviz"):
        row = self.con.execute(
            "SELECT refreshed_at FROM universe_refresh WHERE source=?",
            (source,)).fetchone()
        return row["refreshed_at"] if row else None

    # -- emerging candidates --------------------------------------------------
    def replace_emerging_candidates(self, candidates, official_tickers=(), observed_at=None):
        """Persist a research-only discovery snapshot and its lifecycle.

        Candidates never touch ``score_results`` or ``recommendations``. A name
        that subsequently enters the official universe is explicitly recorded
        as ``graduated_to_active``; other missing names become
        ``no_longer_qualified`` while all prior evidence remains queryable.
        """
        observed_at = observed_at or _now()
        official = {str(t).strip().upper() for t in official_tickers if str(t).strip()}
        rows = {str(c.get("ticker", "")).strip().upper(): c for c in candidates
                if str(c.get("ticker", "")).strip()}
        prior = {r["ticker"]: dict(r) for r in self.con.execute(
            "SELECT * FROM emerging_candidates WHERE active=1")}
        added = changed = graduated = removed = 0
        graduated_10m = graduated_20m = 0

        def event_values(ticker, status, candidate, evidence):
            return (
                observed_at, ticker, status, candidate.get("price"),
                candidate.get("shadow_score"), candidate.get("shadow_verdict"),
                candidate.get("financial_strength"),
                candidate.get("avg_dollar_volume"),
                candidate.get("source", "finviz_discovery"),
                candidate.get("policy_version", "emerging_research_v2"),
                json.dumps(evidence, sort_keys=True),
            )

        with self.con:
            for ticker, candidate in rows.items():
                evidence = candidate.get("evidence") or {}
                status = candidate.get("status", "emerging_candidate")
                old = prior.get(ticker)
                if old is None:
                    added += 1
                elif old.get("status") != status:
                    changed += 1
                if old is not None:
                    old_evidence = json.loads(old.get("evidence_json") or "{}")
                    old_band = old_evidence.get("liquidity_band")
                    new_band = evidence.get("liquidity_band")
                    if old_band == "5m_to_10m" and new_band in {"10m_to_20m", "20m_plus"}:
                        graduated_10m += 1
                    if old_band == "10m_to_20m" and new_band == "20m_plus":
                        graduated_20m += 1
                self.con.execute(
                    "INSERT INTO emerging_candidates("
                    "ticker,status,strategy,company,sector,price,shadow_score,shadow_verdict,"
                    "financial_strength,avg_dollar_volume,first_seen,last_seen,active,source,"
                    "policy_version,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(ticker) DO UPDATE SET status=excluded.status, "
                    "strategy=excluded.strategy, company=excluded.company, sector=excluded.sector, "
                    "price=excluded.price, shadow_score=excluded.shadow_score, "
                    "shadow_verdict=excluded.shadow_verdict, "
                    "financial_strength=excluded.financial_strength, "
                    "avg_dollar_volume=excluded.avg_dollar_volume, last_seen=excluded.last_seen, "
                    "active=1, source=excluded.source, policy_version=excluded.policy_version, "
                    "evidence_json=excluded.evidence_json",
                    (ticker, status, candidate.get("strategy"), candidate.get("company"),
                     candidate.get("sector"), candidate.get("price"),
                     candidate.get("shadow_score"), candidate.get("shadow_verdict"),
                     candidate.get("financial_strength"), candidate.get("avg_dollar_volume"),
                     observed_at, observed_at, 1,
                     candidate.get("source", "finviz_discovery"),
                     candidate.get("policy_version", "emerging_research_v2"),
                     json.dumps(evidence, sort_keys=True)))
                self.con.execute(
                    "INSERT INTO emerging_candidate_events("
                    "observed_at,ticker,status,price,shadow_score,shadow_verdict,"
                    "financial_strength,avg_dollar_volume,source,policy_version,evidence_json) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    event_values(ticker, status, candidate, evidence))

            for ticker, old in prior.items():
                if ticker in rows:
                    continue
                legacy_policy = str(old.get("policy_version") or "").startswith(
                    "emerging_candidate_v1")
                status = ("graduated_to_active" if ticker in official and legacy_policy
                          else "no_longer_qualified")
                graduated += int(status == "graduated_to_active")
                removed += int(status == "no_longer_qualified")
                evidence = json.loads(old.get("evidence_json") or "{}")
                evidence["lifecycle_reason"] = (
                    "Now present in the official Finviz universe" if ticker in official
                    else "No longer passes the emerging-candidate research rule")
                self.con.execute(
                    "UPDATE emerging_candidates SET status=?, active=0, last_seen=?, evidence_json=? "
                    "WHERE ticker=?",
                    (status, observed_at, json.dumps(evidence, sort_keys=True), ticker))
                old_candidate = {
                    "price": old.get("price"), "shadow_score": old.get("shadow_score"),
                    "shadow_verdict": old.get("shadow_verdict"),
                    "financial_strength": old.get("financial_strength"),
                    "avg_dollar_volume": old.get("avg_dollar_volume"),
                    "source": old.get("source"), "policy_version": old.get("policy_version"),
                }
                self.con.execute(
                    "INSERT INTO emerging_candidate_events("
                    "observed_at,ticker,status,price,shadow_score,shadow_verdict,"
                    "financial_strength,avg_dollar_volume,source,policy_version,evidence_json) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    event_values(ticker, status, old_candidate, evidence))
        return {"active": len(rows), "new": added, "status_changed": changed,
                "graduated_to_active": graduated, "no_longer_qualified": removed,
                "liquidity_graduated_10m": graduated_10m,
                "liquidity_graduated_20m": graduated_20m,
                "observed_at": observed_at}

    def emerging_candidates(self, active_only=True) -> list[dict]:
        query = "SELECT * FROM emerging_candidates"
        if active_only:
            query += " WHERE active=1"
        query += " ORDER BY last_seen DESC, ticker"
        out = []
        for row in self.con.execute(query):
            item = dict(row)
            item["active"] = bool(item["active"])
            item["evidence"] = json.loads(item.pop("evidence_json") or "{}")
            out.append(item)
        return out

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
