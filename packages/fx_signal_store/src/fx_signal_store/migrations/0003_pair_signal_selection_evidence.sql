CREATE UNIQUE INDEX signal_store_entries_signal_sequence
ON signal_store_entries(signal_id, store_sequence);

CREATE TABLE pair_signal_selection_snapshots (
    selection_snapshot_id TEXT PRIMARY KEY,
    contract_version TEXT NOT NULL
        CHECK(contract_version = 'pair-signal-selection-snapshot-v1'),
    request_id TEXT NOT NULL UNIQUE
        REFERENCES pair_signal_materialization_requests(request_id),
    checkpoint_sequence INTEGER NOT NULL CHECK(checkpoint_sequence >= 0),
    captured_at TEXT NOT NULL,
    candidate_set_hash TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('SELECTED', 'NO_MATCH', 'AMBIGUOUS')),
    reason TEXT NOT NULL CHECK(reason IN (
        'SELECTED_EXACT_GROUP',
        'NO_ELIGIBLE_BASE_SIGNAL',
        'NO_ELIGIBLE_QUOTE_SIGNAL',
        'NO_COMPLETE_OBSERVATION_GROUP',
        'AMBIGUOUS_BASE_SIGNAL',
        'AMBIGUOUS_QUOTE_SIGNAL',
        'AMBIGUOUS_SOURCE_GROUP'
    )),
    selected_base_candidate_id TEXT,
    selected_quote_candidate_id TEXT,
    selected_base_signal_id TEXT,
    selected_quote_signal_id TEXT,
    selected_observation_group_identity TEXT,
    CHECK(
        (
            outcome = 'SELECTED'
            AND selected_base_candidate_id IS NOT NULL
            AND selected_quote_candidate_id IS NOT NULL
            AND selected_base_signal_id IS NOT NULL
            AND selected_quote_signal_id IS NOT NULL
            AND selected_observation_group_identity IS NOT NULL
        )
        OR
        (
            outcome IN ('NO_MATCH', 'AMBIGUOUS')
            AND selected_base_candidate_id IS NULL
            AND selected_quote_candidate_id IS NULL
            AND selected_base_signal_id IS NULL
            AND selected_quote_signal_id IS NULL
            AND selected_observation_group_identity IS NULL
        )
    )
);

CREATE TABLE pair_signal_selection_candidates (
    candidate_id TEXT PRIMARY KEY,
    selection_snapshot_id TEXT NOT NULL
        REFERENCES pair_signal_selection_snapshots(selection_snapshot_id),
    candidate_ordinal INTEGER NOT NULL CHECK(candidate_ordinal >= 0),
    contract_version TEXT NOT NULL
        CHECK(contract_version = 'pair-signal-selection-candidate-v1'),
    request_id TEXT NOT NULL
        REFERENCES pair_signal_materialization_requests(request_id),
    role TEXT NOT NULL CHECK(role IN ('BASE', 'QUOTE')),
    signal_id TEXT NOT NULL,
    signal_content_hash TEXT NOT NULL,
    store_sequence INTEGER NOT NULL CHECK(store_sequence > 0),
    observation_group_identity TEXT NOT NULL,
    eligibility TEXT NOT NULL CHECK(eligibility IN ('ELIGIBLE', 'INELIGIBLE')),
    rejection_reason TEXT CHECK(rejection_reason IN (
        'TARGET_TYPE_MISMATCH',
        'TARGET_CURRENCY_MISMATCH',
        'SIGNAL_TYPE_MISMATCH',
        'HORIZON_MISMATCH',
        'PRODUCER_VERSION_MISMATCH',
        'MODEL_VERSION_MISMATCH',
        'PROMPT_VERSION_MISMATCH',
        'SCORER_VERSION_MISMATCH',
        'SOURCE_TRANSFORMATION_VERSION_MISMATCH',
        'CREATED_AFTER_AS_OF',
        'OBSERVED_AFTER_AS_OF',
        'STALE_AT_AS_OF'
    )),
    UNIQUE(selection_snapshot_id, candidate_ordinal),
    UNIQUE(selection_snapshot_id, role, signal_id, store_sequence),
    FOREIGN KEY(signal_id, store_sequence)
        REFERENCES signal_store_entries(signal_id, store_sequence),
    CHECK(
        (eligibility = 'ELIGIBLE' AND rejection_reason IS NULL)
        OR
        (eligibility = 'INELIGIBLE' AND rejection_reason IS NOT NULL)
    )
);

CREATE TABLE pair_signal_selection_candidate_observations (
    candidate_id TEXT NOT NULL
        REFERENCES pair_signal_selection_candidates(candidate_id),
    observation_ordinal INTEGER NOT NULL CHECK(observation_ordinal >= 0),
    observation_id TEXT NOT NULL REFERENCES observations(id),
    PRIMARY KEY(candidate_id, observation_ordinal),
    UNIQUE(candidate_id, observation_id)
);

CREATE TRIGGER pair_signal_selection_snapshots_no_update
BEFORE UPDATE ON pair_signal_selection_snapshots
BEGIN SELECT RAISE(ABORT, 'Pair Signal selection snapshots are immutable'); END;

CREATE TRIGGER pair_signal_selection_snapshots_no_delete
BEFORE DELETE ON pair_signal_selection_snapshots
BEGIN SELECT RAISE(ABORT, 'Pair Signal selection snapshots are immutable'); END;

CREATE TRIGGER pair_signal_selection_candidates_no_update
BEFORE UPDATE ON pair_signal_selection_candidates
BEGIN SELECT RAISE(ABORT, 'Pair Signal selection candidates are immutable'); END;

CREATE TRIGGER pair_signal_selection_candidates_no_delete
BEFORE DELETE ON pair_signal_selection_candidates
BEGIN SELECT RAISE(ABORT, 'Pair Signal selection candidates are immutable'); END;

CREATE TRIGGER pair_signal_selection_candidate_observations_no_update
BEFORE UPDATE ON pair_signal_selection_candidate_observations
BEGIN SELECT RAISE(ABORT, 'Pair Signal candidate observations are immutable'); END;

CREATE TRIGGER pair_signal_selection_candidate_observations_no_delete
BEFORE DELETE ON pair_signal_selection_candidate_observations
BEGIN SELECT RAISE(ABORT, 'Pair Signal candidate observations are immutable'); END;
