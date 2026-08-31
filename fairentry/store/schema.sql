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

-- Versioned state for deterministic paper exits.  Keeping this separate from
-- the legacy position row makes migrations safe and preserves a full JSON
-- audit trail of partial profit-taking, pending instructions, and fills.
CREATE TABLE IF NOT EXISTS paper_exit_state (
  ticker       TEXT PRIMARY KEY,
  policy_version TEXT NOT NULL,
  state_json   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

-- The latest successfully screened universe for each defining source.  This is
-- deliberately separate from securities: removing a name from today's active
-- universe must never erase its metrics, signals, or backtest history.
CREATE TABLE IF NOT EXISTS universe_membership (
  source       TEXT NOT NULL,
  ticker       TEXT NOT NULL,
  refreshed_at TEXT NOT NULL,
  PRIMARY KEY (source, ticker)
);
CREATE INDEX IF NOT EXISTS ix_universe_ticker ON universe_membership(ticker);
CREATE TABLE IF NOT EXISTS universe_refresh (
  source       TEXT PRIMARY KEY,
  refreshed_at TEXT NOT NULL,
  member_count INTEGER NOT NULL
);

-- One-shot delivery ledger.  Event keys include the recommendation/position
-- identity so scheduled refreshes do not resend the same alert.
CREATE TABLE IF NOT EXISTS notification_events (
  event_type  TEXT NOT NULL,
  ticker      TEXT NOT NULL,
  strategy    TEXT NOT NULL DEFAULT '',
  event_key   TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  PRIMARY KEY (event_type, ticker, strategy, event_key)
);

-- prospective backtest ledger: one signal snapshot per ticker/strategy/day.
-- Later backtests can join this to future metrics_history price snapshots.
CREATE TABLE IF NOT EXISTS signal_events (
  signal_date TEXT NOT NULL,
  run_at      TEXT NOT NULL,
  ticker      TEXT NOT NULL,
  strategy    TEXT NOT NULL,
  company     TEXT,
  sector      TEXT,
  country     TEXT,
  price       REAL,
  score       REAL,
  verdict     TEXT,
  action      TEXT,
  labels_json TEXT,
  gates_json  TEXT,
  vetoes_json TEXT,
  trace_json  TEXT,
  PRIMARY KEY (signal_date, ticker, strategy)
);
CREATE INDEX IF NOT EXISTS ix_signal_events_ticker ON signal_events(ticker, signal_date);
CREATE INDEX IF NOT EXISTS ix_signal_events_verdict ON signal_events(signal_date, verdict, strategy);

-- Research-only names observed immediately outside the official Finviz
-- universe. Current state is convenient for the UI; the append-only event
-- stream makes first detection, changes, exits, and later graduation auditable.
CREATE TABLE IF NOT EXISTS emerging_candidates (
  ticker                TEXT PRIMARY KEY,
  status                TEXT NOT NULL,
  strategy              TEXT,
  company               TEXT,
  sector                TEXT,
  price                 REAL,
  shadow_score          REAL,
  shadow_verdict        TEXT,
  financial_strength    REAL,
  avg_dollar_volume     REAL,
  first_seen            TEXT NOT NULL,
  last_seen             TEXT NOT NULL,
  active                INTEGER NOT NULL DEFAULT 1,
  source                TEXT NOT NULL,
  policy_version        TEXT NOT NULL,
  evidence_json         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_emerging_active ON emerging_candidates(active, status);

CREATE TABLE IF NOT EXISTS emerging_candidate_events (
  event_id              INTEGER PRIMARY KEY AUTOINCREMENT,
  observed_at           TEXT NOT NULL,
  ticker                TEXT NOT NULL,
  status                TEXT NOT NULL,
  price                 REAL,
  shadow_score          REAL,
  shadow_verdict        TEXT,
  financial_strength    REAL,
  avg_dollar_volume     REAL,
  source                TEXT NOT NULL,
  policy_version        TEXT NOT NULL,
  evidence_json         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_emerging_events_ticker
  ON emerging_candidate_events(ticker, observed_at);
