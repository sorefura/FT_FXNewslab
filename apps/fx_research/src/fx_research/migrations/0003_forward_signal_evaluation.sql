CREATE TABLE IF NOT EXISTS research_forward_jobs (
    job_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    horizon TEXT NOT NULL,
    instrument TEXT NOT NULL,
    projection_sign INTEGER NOT NULL CHECK(projection_sign IN (-1, 1)),
    projection_version TEXT NOT NULL,
    anchor_at TEXT NOT NULL,
    target_at TEXT NOT NULL,
    market_source TEXT NOT NULL,
    granularity TEXT NOT NULL,
    price_basis TEXT NOT NULL,
    market_data_version TEXT NOT NULL,
    formula_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PENDING', 'COMPLETED', 'FAILED', 'UNAVAILABLE')),
    unavailable_reason TEXT,
    error_code TEXT,
    error_message TEXT,
    result_id TEXT,
    updated_at TEXT NOT NULL,
    CHECK((status = 'COMPLETED' AND result_id IS NOT NULL)
       OR (status != 'COMPLETED' AND result_id IS NULL)),
    CHECK(status = 'UNAVAILABLE' OR unavailable_reason IS NULL)
);

CREATE TABLE IF NOT EXISTS research_market_candles (
    revision_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    instrument TEXT NOT NULL,
    granularity TEXT NOT NULL,
    price_basis TEXT NOT NULL,
    open_time TEXT NOT NULL,
    open_price TEXT NOT NULL,
    high_price TEXT NOT NULL,
    low_price TEXT NOT NULL,
    close_price TEXT NOT NULL,
    complete INTEGER NOT NULL CHECK(complete IN (0, 1)),
    market_data_version TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS research_market_candle_time
ON research_market_candles(source, instrument, granularity, price_basis, open_time);

CREATE TABLE IF NOT EXISTS research_market_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_market_snapshot_candles (
    snapshot_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    candle_revision_id TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, ordinal),
    UNIQUE(snapshot_id, candle_revision_id),
    FOREIGN KEY(snapshot_id) REFERENCES research_market_snapshots(snapshot_id),
    FOREIGN KEY(candle_revision_id) REFERENCES research_market_candles(revision_id)
);

CREATE TABLE IF NOT EXISTS research_forward_results (
    result_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    horizon TEXT NOT NULL,
    instrument TEXT NOT NULL,
    projection_sign INTEGER NOT NULL CHECK(projection_sign IN (-1, 1)),
    projection_version TEXT NOT NULL,
    anchor_at TEXT NOT NULL,
    target_at TEXT NOT NULL,
    price_t0 TEXT NOT NULL,
    price_tx TEXT NOT NULL,
    t0_observed_at TEXT NOT NULL,
    tx_observed_at TEXT NOT NULL,
    target_return_bps TEXT NOT NULL,
    mfe_bps TEXT,
    mae_bps TEXT,
    realized_volatility REAL NOT NULL,
    completed_at TEXT NOT NULL,
    market_source TEXT NOT NULL,
    market_data_version TEXT NOT NULL,
    price_basis TEXT NOT NULL,
    granularity TEXT NOT NULL,
    formula_version TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    FOREIGN KEY(snapshot_id) REFERENCES research_market_snapshots(snapshot_id),
    UNIQUE(
        signal_id, horizon, instrument, projection_sign, projection_version,
        market_source, market_data_version, price_basis, granularity, formula_version
    )
);

CREATE TRIGGER IF NOT EXISTS research_market_candles_no_update
BEFORE UPDATE ON research_market_candles
BEGIN SELECT RAISE(ABORT, 'market candle revisions are immutable'); END;

CREATE TRIGGER IF NOT EXISTS research_market_candles_no_delete
BEFORE DELETE ON research_market_candles
BEGIN SELECT RAISE(ABORT, 'market candle revisions are immutable'); END;

CREATE TRIGGER IF NOT EXISTS research_market_snapshots_no_update
BEFORE UPDATE ON research_market_snapshots
BEGIN SELECT RAISE(ABORT, 'market snapshots are immutable'); END;

CREATE TRIGGER IF NOT EXISTS research_market_snapshots_no_delete
BEFORE DELETE ON research_market_snapshots
BEGIN SELECT RAISE(ABORT, 'market snapshots are immutable'); END;

CREATE TRIGGER IF NOT EXISTS research_market_snapshot_candles_no_update
BEFORE UPDATE ON research_market_snapshot_candles
BEGIN SELECT RAISE(ABORT, 'market snapshot evidence is immutable'); END;

CREATE TRIGGER IF NOT EXISTS research_market_snapshot_candles_no_delete
BEFORE DELETE ON research_market_snapshot_candles
BEGIN SELECT RAISE(ABORT, 'market snapshot evidence is immutable'); END;

CREATE TRIGGER IF NOT EXISTS research_forward_results_no_update
BEFORE UPDATE ON research_forward_results
BEGIN SELECT RAISE(ABORT, 'forward results are immutable'); END;

CREATE TRIGGER IF NOT EXISTS research_forward_results_no_delete
BEFORE DELETE ON research_forward_results
BEGIN SELECT RAISE(ABORT, 'forward results are immutable'); END;
