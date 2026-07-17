CREATE TABLE signal_store_entries (
    store_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_version TEXT NOT NULL
        CHECK(contract_version = 'signal-store-entry-v1'),
    signal_id TEXT NOT NULL UNIQUE REFERENCES signals(id),
    stored_at TEXT NOT NULL,
    storage_origin TEXT NOT NULL
        CHECK(storage_origin IN ('LEGACY_BACKFILL', 'APPEND', 'PAIR_MATERIALIZATION'))
);

INSERT INTO signal_store_entries(
    contract_version,
    signal_id,
    stored_at,
    storage_origin
)
SELECT
    'signal-store-entry-v1',
    signals.id,
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
    'LEGACY_BACKFILL'
FROM signals
ORDER BY signals.created_at ASC, signals.id ASC;

CREATE TABLE pair_signal_materialization_specifications (
    specification_id TEXT PRIMARY KEY,
    contract_version TEXT NOT NULL,
    pair TEXT NOT NULL,
    source_signal_type TEXT NOT NULL,
    output_signal_type TEXT NOT NULL,
    horizon TEXT NOT NULL,
    producer_version TEXT NOT NULL,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    scorer_version TEXT NOT NULL,
    expected_source_transformation_version TEXT,
    output_transformation_version TEXT NOT NULL,
    source_signal_max_age_microseconds INTEGER NOT NULL
        CHECK(source_signal_max_age_microseconds > 0),
    observation_group_policy_version TEXT NOT NULL,
    selection_policy_version TEXT NOT NULL
);

CREATE TABLE pair_signal_materialization_requests (
    request_id TEXT PRIMARY KEY,
    contract_version TEXT NOT NULL,
    pair TEXT NOT NULL,
    as_of TEXT NOT NULL,
    specification_id TEXT NOT NULL
        REFERENCES pair_signal_materialization_specifications(specification_id),
    UNIQUE(pair, as_of, specification_id)
);

CREATE TABLE pair_signal_materialization_claims (
    request_id TEXT PRIMARY KEY
        REFERENCES pair_signal_materialization_requests(request_id),
    contract_version TEXT NOT NULL,
    checkpoint_sequence INTEGER NOT NULL CHECK(checkpoint_sequence >= 0),
    captured_at TEXT NOT NULL
);

CREATE TRIGGER signal_store_entries_no_update
BEFORE UPDATE ON signal_store_entries
BEGIN SELECT RAISE(ABORT, 'Signal Store entries are immutable'); END;

CREATE TRIGGER signal_store_entries_no_delete
BEFORE DELETE ON signal_store_entries
BEGIN SELECT RAISE(ABORT, 'Signal Store entries are immutable'); END;

CREATE TRIGGER pair_signal_materialization_specifications_no_update
BEFORE UPDATE ON pair_signal_materialization_specifications
BEGIN SELECT RAISE(ABORT, 'materialization specifications are immutable'); END;

CREATE TRIGGER pair_signal_materialization_specifications_no_delete
BEFORE DELETE ON pair_signal_materialization_specifications
BEGIN SELECT RAISE(ABORT, 'materialization specifications are immutable'); END;

CREATE TRIGGER pair_signal_materialization_requests_no_update
BEFORE UPDATE ON pair_signal_materialization_requests
BEGIN SELECT RAISE(ABORT, 'materialization requests are immutable'); END;

CREATE TRIGGER pair_signal_materialization_requests_no_delete
BEFORE DELETE ON pair_signal_materialization_requests
BEGIN SELECT RAISE(ABORT, 'materialization requests are immutable'); END;

CREATE TRIGGER pair_signal_materialization_claims_no_update
BEFORE UPDATE ON pair_signal_materialization_claims
BEGIN SELECT RAISE(ABORT, 'materialization claims are immutable'); END;

CREATE TRIGGER pair_signal_materialization_claims_no_delete
BEFORE DELETE ON pair_signal_materialization_claims
BEGIN SELECT RAISE(ABORT, 'materialization claims are immutable'); END;
