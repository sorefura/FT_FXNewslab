CREATE TABLE IF NOT EXISTS live_signal_authorization_content_commitments (
    authorization_id TEXT PRIMARY KEY REFERENCES
        live_signal_authorizations(authorization_id),
    signal_id TEXT NOT NULL,
    adoption_decision_id TEXT NOT NULL,
    evidence_snapshot_id TEXT NOT NULL,
    adoption_policy_version TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    adoption_mode TEXT NOT NULL,
    runtime_mode TEXT NOT NULL,
    authorized_at TEXT NOT NULL
);

INSERT OR IGNORE INTO live_signal_authorization_content_commitments
SELECT authorization_id, signal_id, adoption_decision_id, evidence_snapshot_id,
       adoption_policy_version, strategy_id, strategy_version, adoption_mode,
       runtime_mode, authorized_at
FROM live_signal_authorizations;

CREATE TRIGGER IF NOT EXISTS live_signal_authorization_commitment_no_update
BEFORE UPDATE ON live_signal_authorization_content_commitments
BEGIN SELECT RAISE(ABORT, 'Signal authorization commitment is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_signal_authorization_commitment_no_delete
BEFORE DELETE ON live_signal_authorization_content_commitments
BEGIN SELECT RAISE(ABORT, 'Signal authorization commitment is immutable'); END;

CREATE TABLE IF NOT EXISTS live_news_filtered_carry_configs (
    strategy_config_identity TEXT PRIMARY KEY,
    config_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS live_production_entry_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    strategy_config_identity TEXT NOT NULL REFERENCES
        live_news_filtered_carry_configs(strategy_config_identity),
    pair TEXT NOT NULL,
    signal_id TEXT NOT NULL,
    authorization_id TEXT NOT NULL REFERENCES
        live_signal_authorizations(authorization_id),
    materialization_request_id TEXT NOT NULL,
    pair_signal_content_hash TEXT NOT NULL,
    swap_evidence_id TEXT NOT NULL REFERENCES
        live_operational_swap_evidence(swap_evidence_id),
    outcome TEXT NOT NULL CHECK(outcome IN ('CANDIDATE', 'SKIP')),
    skip_reason TEXT,
    evaluation_json TEXT NOT NULL,
    CHECK(
        (outcome = 'CANDIDATE' AND skip_reason IS NULL)
        OR (outcome = 'SKIP' AND skip_reason IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS live_production_trade_candidates (
    candidate_id TEXT PRIMARY KEY,
    strategy_evaluation_id TEXT NOT NULL UNIQUE REFERENCES
        live_production_entry_evaluations(evaluation_id),
    candidate_json TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS live_production_candidate_requires_candidate_evaluation
BEFORE INSERT ON live_production_trade_candidates
WHEN NOT EXISTS (
    SELECT 1 FROM live_production_entry_evaluations
    WHERE evaluation_id = NEW.strategy_evaluation_id AND outcome = 'CANDIDATE'
)
BEGIN SELECT RAISE(ABORT, 'Candidate requires a CANDIDATE evaluation'); END;

CREATE TRIGGER IF NOT EXISTS live_news_filtered_carry_configs_no_update
BEFORE UPDATE ON live_news_filtered_carry_configs
BEGIN SELECT RAISE(ABORT, 'News Filtered Carry config is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_news_filtered_carry_configs_no_delete
BEFORE DELETE ON live_news_filtered_carry_configs
BEGIN SELECT RAISE(ABORT, 'News Filtered Carry config is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_production_entry_evaluations_no_update
BEFORE UPDATE ON live_production_entry_evaluations
BEGIN SELECT RAISE(ABORT, 'Production entry evaluation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_production_entry_evaluations_no_delete
BEFORE DELETE ON live_production_entry_evaluations
BEGIN SELECT RAISE(ABORT, 'Production entry evaluation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_production_trade_candidates_no_update
BEFORE UPDATE ON live_production_trade_candidates
BEGIN SELECT RAISE(ABORT, 'Production trade candidate is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_production_trade_candidates_no_delete
BEFORE DELETE ON live_production_trade_candidates
BEGIN SELECT RAISE(ABORT, 'Production trade candidate is immutable'); END;
