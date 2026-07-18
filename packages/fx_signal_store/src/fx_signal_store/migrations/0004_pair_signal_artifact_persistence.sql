CREATE UNIQUE INDEX pair_signal_selection_candidates_snapshot_candidate
ON pair_signal_selection_candidates(selection_snapshot_id, candidate_id);

CREATE TABLE pair_signal_derivations (
    derivation_id TEXT PRIMARY KEY,
    contract_version TEXT NOT NULL
        CHECK(contract_version = 'pair-signal-derivation-v1'),
    pair_signal_id TEXT NOT NULL UNIQUE REFERENCES signals(id),
    pair_signal_content_hash TEXT NOT NULL,
    selection_snapshot_id TEXT NOT NULL UNIQUE
        REFERENCES pair_signal_selection_snapshots(selection_snapshot_id),
    materialization_request_id TEXT NOT NULL UNIQUE
        REFERENCES pair_signal_materialization_requests(request_id),
    pair TEXT NOT NULL,
    base_candidate_id TEXT NOT NULL,
    base_signal_id TEXT NOT NULL REFERENCES signals(id),
    base_signal_content_hash TEXT NOT NULL,
    quote_candidate_id TEXT NOT NULL,
    quote_signal_id TEXT NOT NULL REFERENCES signals(id),
    quote_signal_content_hash TEXT NOT NULL,
    observation_group_identity TEXT NOT NULL,
    horizon TEXT NOT NULL,
    transformation_version TEXT NOT NULL,
    specification_id TEXT NOT NULL
        REFERENCES pair_signal_materialization_specifications(specification_id),
    materialization_request_as_of TEXT NOT NULL,
    base_signal_created_at TEXT NOT NULL,
    quote_signal_created_at TEXT NOT NULL,
    materialized_at TEXT NOT NULL,
    FOREIGN KEY(selection_snapshot_id, base_candidate_id)
        REFERENCES pair_signal_selection_candidates(selection_snapshot_id, candidate_id),
    FOREIGN KEY(selection_snapshot_id, quote_candidate_id)
        REFERENCES pair_signal_selection_candidates(selection_snapshot_id, candidate_id),
    CHECK(base_candidate_id != quote_candidate_id),
    CHECK(base_signal_id != quote_signal_id)
);

CREATE TABLE pair_signal_derivation_observations (
    derivation_id TEXT NOT NULL REFERENCES pair_signal_derivations(derivation_id),
    observation_ordinal INTEGER NOT NULL CHECK(observation_ordinal >= 0),
    observation_id TEXT NOT NULL REFERENCES observations(id),
    PRIMARY KEY(derivation_id, observation_ordinal),
    UNIQUE(derivation_id, observation_id)
);

CREATE TABLE pair_signal_materialization_completions (
    request_id TEXT PRIMARY KEY
        REFERENCES pair_signal_materialization_requests(request_id),
    contract_version TEXT NOT NULL
        CHECK(contract_version = 'pair-signal-materialization-completion-v1'),
    selection_snapshot_id TEXT NOT NULL UNIQUE
        REFERENCES pair_signal_selection_snapshots(selection_snapshot_id),
    selection_outcome TEXT NOT NULL
        CHECK(selection_outcome IN ('SELECTED', 'NO_MATCH', 'AMBIGUOUS')),
    pair_signal_id TEXT UNIQUE,
    pair_signal_store_sequence INTEGER,
    derivation_id TEXT UNIQUE REFERENCES pair_signal_derivations(derivation_id),
    FOREIGN KEY(pair_signal_id, pair_signal_store_sequence)
        REFERENCES signal_store_entries(signal_id, store_sequence),
    CHECK(
        (
            selection_outcome = 'SELECTED'
            AND pair_signal_id IS NOT NULL
            AND pair_signal_store_sequence IS NOT NULL
            AND derivation_id IS NOT NULL
        )
        OR
        (
            selection_outcome IN ('NO_MATCH', 'AMBIGUOUS')
            AND pair_signal_id IS NULL
            AND pair_signal_store_sequence IS NULL
            AND derivation_id IS NULL
        )
    )
);

CREATE TRIGGER pair_signal_derivations_no_update
BEFORE UPDATE ON pair_signal_derivations
BEGIN SELECT RAISE(ABORT, 'Pair Signal derivations are immutable'); END;

CREATE TRIGGER pair_signal_derivations_no_delete
BEFORE DELETE ON pair_signal_derivations
BEGIN SELECT RAISE(ABORT, 'Pair Signal derivations are immutable'); END;

CREATE TRIGGER pair_signal_derivation_observations_no_update
BEFORE UPDATE ON pair_signal_derivation_observations
BEGIN SELECT RAISE(ABORT, 'Pair Signal derivation observations are immutable'); END;

CREATE TRIGGER pair_signal_derivation_observations_no_delete
BEFORE DELETE ON pair_signal_derivation_observations
BEGIN SELECT RAISE(ABORT, 'Pair Signal derivation observations are immutable'); END;

CREATE TRIGGER pair_signal_materialization_completions_no_update
BEFORE UPDATE ON pair_signal_materialization_completions
BEGIN SELECT RAISE(ABORT, 'Pair Signal materialization completions are immutable'); END;

CREATE TRIGGER pair_signal_materialization_completions_no_delete
BEFORE DELETE ON pair_signal_materialization_completions
BEGIN SELECT RAISE(ABORT, 'Pair Signal materialization completions are immutable'); END;
