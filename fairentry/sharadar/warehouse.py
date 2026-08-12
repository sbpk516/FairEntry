"""Provider-neutral DuckDB warehouse built from a licensed Sharadar snapshot."""

from __future__ import annotations

import json
from pathlib import Path

try:
    import duckdb
except ImportError:  # pragma: no cover - actionable error for optional workflow
    duckdb = None

from .snapshot import DEFAULT_ROOT


RAW_TABLES = {
    "SF1": "sfa_fundamentals",
    "SEP": "sfa_prices",
    "TICKERS": "sfa_tickers",
    "ACTIONS": "sfa_actions",
    "DAILY": "sfa_daily",
    "SFP": "sfa_fund_prices",
    "SF2": "sfa_insiders",
    "SF3": "sfa_holdings",
    "SF3A": "sfa_holdings_by_ticker",
}


class SharadarWarehouse:
    def __init__(self, path: str | Path | None = None, read_only: bool = False):
        if duckdb is None:
            raise RuntimeError(
                "duckdb is required; run: pip install -r requirements.txt"
            )
        self.path = Path(path or DEFAULT_ROOT / "warehouse.duckdb")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(self.path), read_only=read_only)
        if not read_only:
            self.con.execute("PRAGMA threads=4")

    def close(self):
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @staticmethod
    def latest_snapshot(root: str | Path = DEFAULT_ROOT) -> Path:
        root = Path(root)
        marker = root / "LATEST"
        if not marker.exists():
            raise FileNotFoundError(
                "no Sharadar snapshot found; run scripts/sharadar_snapshot.py"
            )
        return root / "snapshots" / marker.read_text(encoding="utf-8").strip()

    def build(self, snapshot: str | Path | None = None) -> dict:
        snapshot = Path(snapshot) if snapshot else self.latest_snapshot()
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS sfa_manifest(key VARCHAR PRIMARY KEY, value VARCHAR)"
        )
        stats = {}
        for code, name in RAW_TABLES.items():
            record = manifest["tables"].get(code)
            if not record:
                continue
            files = [
                snapshot / p
                for p in record.get("extracted", [])
                if Path(p).suffix.lower() in {".csv", ".gz"}
            ]
            if not files:
                raise FileNotFoundError(f"{code}: no extracted CSV in snapshot")
            quoted = ",".join("'" + str(p).replace("'", "''") + "'" for p in files)
            source = f"[{quoted}]" if len(files) > 1 else quoted
            self.con.execute(
                f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM read_csv_auto({source}, header=true, sample_size=-1, union_by_name=true)"
            )
            count = self.con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            stats[code] = {"table": name, "rows": count}

        self._canonical_views()
        values = {
            "snapshot_id": manifest["snapshot_id"],
            "snapshot_path": str(snapshot),
            "provider": manifest["provider"],
            "manifest": json.dumps(manifest, separators=(",", ":")),
        }
        for key, value in values.items():
            self.con.execute(
                "INSERT OR REPLACE INTO sfa_manifest VALUES (?, ?)", [key, value]
            )
        self._feature_tables()
        self.con.execute("CHECKPOINT")
        return stats

    def _canonical_views(self) -> None:
        self.con.execute("""
        CREATE OR REPLACE VIEW canonical_securities AS
        SELECT CAST(permaticker AS VARCHAR) AS security_id, ticker, name AS company,
               exchange, CAST(isdelisted AS VARCHAR) AS isdelisted, category,
               sector, industry, location AS country, firstpricedate, lastpricedate,
               lastupdated, scalemarketcap
        FROM sfa_tickers WHERE "table"='SEP'
        """)
        self.con.execute("""
        CREATE OR REPLACE VIEW canonical_prices AS
        SELECT s.security_id, p.ticker, p.date, p.open, p.high, p.low, p.close,
               p.volume, p.closeadj, p.closeunadj, p.lastupdated
        FROM sfa_prices p JOIN canonical_securities s USING(ticker)
        """)
        self.con.execute("""
        CREATE OR REPLACE VIEW canonical_fundamentals AS
        SELECT s.security_id, f.*
        FROM sfa_fundamentals f JOIN canonical_securities s USING(ticker)
        """)
        if self._table_exists("sfa_actions"):
            self.con.execute("""
            CREATE OR REPLACE VIEW canonical_actions AS
            SELECT s.security_id, a.* FROM sfa_actions a
            LEFT JOIN canonical_securities s USING(ticker)
            """)
        if self._table_exists("sfa_fund_prices"):
            self.con.execute("""
            CREATE OR REPLACE VIEW canonical_benchmarks AS
            SELECT ticker, date, close, closeadj, closeunadj, volume
            FROM sfa_fund_prices
            """)

    def _table_exists(self, name: str) -> bool:
        return bool(
            self.con.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name=?",
                [name],
            ).fetchone()[0]
        )

    def _feature_tables(self) -> None:
        """Materialize only strictly historical features used by replay.

        ``close`` is split-adjusted and is used for valuation/targets;
        ``closeadj`` includes distributions and is used for total returns.
        All windows end on the observation row, so no future value participates.
        """
        self.con.execute("""
        CREATE OR REPLACE TABLE sfa_price_features AS
        WITH base AS (
          SELECT ticker, date, close, closeadj, closeunadj, high, low, volume,
            row_number() OVER (PARTITION BY ticker ORDER BY date) AS history_sessions,
            avg(close) OVER w50 AS sma50,
            avg(close) OVER w200 AS sma200,
            avg(close) OVER w1000 AS wma200_proxy,
            avg(volume) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 50 PRECEDING AND 1 PRECEDING) AS avgvol50,
            max(close) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 67 PRECEDING AND 5 PRECEDING) AS resistance50,
            min(close) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 125 PRECEDING AND CURRENT ROW) AS support126,
            lag(close, 63) OVER (PARTITION BY ticker ORDER BY date) AS close_3m,
            lag(close, 252) OVER (PARTITION BY ticker ORDER BY date) AS close_1y
          FROM sfa_prices
          WINDOW
            w50 AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW),
            w200 AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW),
            w1000 AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 999 PRECEDING AND CURRENT ROW)
        )
        SELECT *, lag(sma50, 20) OVER (PARTITION BY ticker ORDER BY date) AS sma50_1m_ago
        FROM base
        """)
        self.con.execute(
            "CREATE INDEX IF NOT EXISTS ix_sfa_price_features ON sfa_price_features(ticker, date)"
        )
        self.con.execute("""
        CREATE OR REPLACE TABLE sfa_arq_features AS
        SELECT *,
          lag(revenue) OVER (PARTITION BY ticker ORDER BY calendardate, datekey) AS revenue_prev_q,
          lag(revenue, 4) OVER (PARTITION BY ticker ORDER BY calendardate, datekey) AS revenue_prev_y,
          lag(opinc) OVER (PARTITION BY ticker ORDER BY calendardate, datekey) AS opinc_prev_q,
          lag(sharesbas, 4) OVER (PARTITION BY ticker ORDER BY calendardate, datekey) AS shares_prev_y,
          lag(grossmargin) OVER (PARTITION BY ticker ORDER BY calendardate, datekey) AS grossmargin_prev_q,
          lag(debtnc, 4) OVER (PARTITION BY ticker ORDER BY calendardate, datekey) AS debt_long_prev_y,
          lag(assets, 4) OVER (PARTITION BY ticker ORDER BY calendardate, datekey) AS assets_prev_y
        FROM sfa_fundamentals WHERE dimension='ARQ'
        """)
        self.con.execute(
            "CREATE INDEX IF NOT EXISTS ix_sfa_arq ON sfa_arq_features(ticker, datekey)"
        )
        self.con.execute("""
        CREATE OR REPLACE TABLE sfa_art_features AS
        SELECT * FROM sfa_fundamentals WHERE dimension='ART'
        """)
        self.con.execute(
            "CREATE INDEX IF NOT EXISTS ix_sfa_art ON sfa_art_features(ticker, datekey)"
        )

    def audit(self) -> dict:
        tables = {}
        for code, name in RAW_TABLES.items():
            if not self._table_exists(name):
                continue
            columns = {r[0] for r in self.con.execute(f"DESCRIBE {name}").fetchall()}
            date_col = next(
                (c for c in ("date", "datekey", "firstpricedate") if c in columns), None
            )
            bounds = (
                self.con.execute(
                    f"SELECT min({date_col}), max({date_col}) FROM {name}"
                ).fetchone()
                if date_col
                else (None, None)
            )
            tables[code] = {
                "rows": self.con.execute(f"SELECT count(*) FROM {name}").fetchone()[0],
                "min_date": str(bounds[0]) if bounds[0] is not None else None,
                "max_date": str(bounds[1]) if bounds[1] is not None else None,
            }
        active = delisted = None
        if self._table_exists("sfa_tickers"):
            active, delisted = self.con.execute("""
                SELECT count(*) FILTER (WHERE lower(CAST(isdelisted AS VARCHAR)) IN ('n','false','0')),
                       count(*) FILTER (WHERE lower(CAST(isdelisted AS VARCHAR)) IN ('y','true','1'))
                FROM sfa_tickers WHERE "table"='SEP'
            """).fetchone()
        return {
            "warehouse": str(self.path),
            "tables": tables,
            "securities": {"active": active, "delisted": delisted},
        }
