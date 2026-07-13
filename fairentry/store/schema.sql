-- FairEntry canonical store (SQLite). Rebuilt each run; gitignored; CI artifact.
-- Every fetched value carries its source + fetched_at (provenance); history is
-- append-only for backtesting / point-in-time replay.

CREATE TABLE IF NOT EXISTS securities (
  ticker      TEXT PRIMARY KEY,
  company     TEXT,
  sector      TEXT,
  industry    TEXT,
  country     TEXT,
  updated_at  TEXT
);

-- current value of every catalog field, per ticker (latest snapshot)
CREATE TABLE IF NOT EXISTS metrics_current (
  ticker      TEXT NOT NULL,
  field_id    TEXT NOT NULL,
  value_num   REAL,
  value_text  TEXT,
  source      TEXT,
  fetched_at  TEXT,
  PRIMARY KEY (ticker, field_id)
);

-- append-only history (point-in-time) for revision tracking / backtesting
CREATE TABLE IF NOT EXISTS metrics_history (
  ticker      TEXT NOT NULL,
  field_id    TEXT NOT NULL,
  value_num   REAL,
  value_text  TEXT,
  source      TEXT,
  fetched_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_hist ON metrics_history(ticker, field_id, fetched_at);

-- one row per (source, run) fetch, for observability + source-failure isolation
CREATE TABLE IF NOT EXISTS source_fetch_log (
  run_id      TEXT,
  source      TEXT,
  ok          INTEGER,
  rows        INTEGER,
  seconds     REAL,
  error       TEXT,
  fetched_at  TEXT
);

CREATE TABLE IF NOT EXISTS screen_results (
  ticker      TEXT NOT NULL,
  screener    TEXT NOT NULL,
  passed      INTEGER,
  detail_json TEXT,
  run_at      TEXT,
  PRIMARY KEY (ticker, screener)
);

CREATE TABLE IF NOT EXISTS score_results (
  ticker      TEXT NOT NULL,
  strategy    TEXT NOT NULL,
  base_score  REAL,
  preliminary REAL,
  verdict     TEXT,
  trace_json  TEXT,        -- full category/item/metric tree for the UI
  run_at      TEXT,
  PRIMARY KEY (ticker, strategy)
);

CREATE TABLE IF NOT EXISTS thesis_results (
  ticker        TEXT NOT NULL,
  strategy      TEXT NOT NULL,
  thesis_score  REAL,
  modifier      REAL,
  thesis_json   TEXT,
  provider      TEXT,
  run_at        TEXT,
  PRIMARY KEY (ticker, strategy)
);

CREATE TABLE IF NOT EXISTS recommendations (
  ticker      TEXT NOT NULL,
  strategy    TEXT NOT NULL,
  verdict     TEXT,
  action      TEXT,
  score       REAL,
  first_seen  TEXT,
  last_seen   TEXT,
  PRIMARY KEY (ticker, strategy)
);

CREATE TABLE IF NOT EXISTS paper_portfolio (
  ticker      TEXT PRIMARY KEY,
  entered_at  TEXT,
  entry_price REAL,
  strategy    TEXT,
  status      TEXT,
  notes       TEXT
);
