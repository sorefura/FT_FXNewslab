CREATE TABLE IF NOT EXISTS live_research_validation_evidence_snapshots (
    evidence_snapshot_id TEXT PRIMARY KEY,
    source_contract_version TEXT NOT NULL,
    assessment_id TEXT NOT NULL UNIQUE,
    evaluation_run_id TEXT NOT NULL,
    report_id TEXT NOT NULL,
    research_policy_version TEXT NOT NULL,
    research_policy_content_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status = 'VALIDATED_FOR_RESEARCH'),
    cohort_identity_json TEXT NOT NULL,
    cohort_identity_hash TEXT NOT NULL,
    metric_payload_json TEXT NOT NULL,
    metric_payload_hash TEXT NOT NULL,
    condition_results_json TEXT NOT NULL,
    input_snapshot_version TEXT NOT NULL,
    input_snapshot_identity_hash TEXT NOT NULL,
    input_snapshot_json TEXT NOT NULL,
    research_policy_json TEXT NOT NULL,
    assessment_created_at TEXT NOT NULL,
    report_created_at TEXT NOT NULL,
    run_created_at TEXT NOT NULL,
    research_policy_created_at TEXT NOT NULL,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS live_strategy_adoption_policies (
    adoption_policy_version TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS live_strategy_adoption_decisions (
    adoption_decision_id TEXT PRIMARY KEY,
    decision_type TEXT NOT NULL CHECK(
        decision_type IN ('APPROVED_FOR_STRATEGY', 'REVOKED')
    ),
    evidence_snapshot_id TEXT NOT NULL REFERENCES
        live_research_validation_evidence_snapshots(evidence_snapshot_id),
    adoption_policy_version TEXT NOT NULL REFERENCES
        live_strategy_adoption_policies(adoption_policy_version),
    adoption_policy_content_hash TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    strategy_config_identity TEXT,
    approved_signal_specification_json TEXT NOT NULL,
    adoption_mode TEXT NOT NULL CHECK(adoption_mode IN ('SHADOW_ONLY', 'LIVE_ELIGIBLE')),
    effective_from TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    approval_decision_id TEXT REFERENCES
        live_strategy_adoption_decisions(adoption_decision_id),
    CHECK(
        (decision_type = 'APPROVED_FOR_STRATEGY' AND approval_decision_id IS NULL)
        OR (decision_type = 'REVOKED' AND approval_decision_id IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS live_signal_authorizations (
    authorization_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    adoption_decision_id TEXT NOT NULL REFERENCES
        live_strategy_adoption_decisions(adoption_decision_id),
    evidence_snapshot_id TEXT NOT NULL REFERENCES
        live_research_validation_evidence_snapshots(evidence_snapshot_id),
    adoption_policy_version TEXT NOT NULL REFERENCES
        live_strategy_adoption_policies(adoption_policy_version),
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    adoption_mode TEXT NOT NULL CHECK(adoption_mode IN ('SHADOW_ONLY', 'LIVE_ELIGIBLE')),
    runtime_mode TEXT NOT NULL CHECK(runtime_mode IN ('SHADOW', 'LIVE')),
    authorized_at TEXT NOT NULL,
    UNIQUE(signal_id, adoption_decision_id, strategy_id, strategy_version, runtime_mode)
);

CREATE TABLE IF NOT EXISTS live_candidate_signal_authorizations (
    candidate_id TEXT NOT NULL REFERENCES live_candidates(id),
    signal_id TEXT NOT NULL,
    authorization_id TEXT NOT NULL REFERENCES live_signal_authorizations(authorization_id),
    adoption_decision_id TEXT NOT NULL REFERENCES
        live_strategy_adoption_decisions(adoption_decision_id),
    PRIMARY KEY(candidate_id, signal_id),
    UNIQUE(candidate_id, authorization_id)
);

CREATE TRIGGER IF NOT EXISTS live_research_evidence_no_update
BEFORE UPDATE ON live_research_validation_evidence_snapshots
BEGIN SELECT RAISE(ABORT, 'Research evidence snapshot is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_research_evidence_no_delete
BEFORE DELETE ON live_research_validation_evidence_snapshots
BEGIN SELECT RAISE(ABORT, 'Research evidence snapshot is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_adoption_policy_no_update
BEFORE UPDATE ON live_strategy_adoption_policies
BEGIN SELECT RAISE(ABORT, 'Strategy adoption policy is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_adoption_policy_no_delete
BEFORE DELETE ON live_strategy_adoption_policies
BEGIN SELECT RAISE(ABORT, 'Strategy adoption policy is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_adoption_decision_no_update
BEFORE UPDATE ON live_strategy_adoption_decisions
BEGIN SELECT RAISE(ABORT, 'Strategy adoption decision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_adoption_decision_no_delete
BEFORE DELETE ON live_strategy_adoption_decisions
BEGIN SELECT RAISE(ABORT, 'Strategy adoption decision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_signal_authorization_no_update
BEFORE UPDATE ON live_signal_authorizations
BEGIN SELECT RAISE(ABORT, 'Signal authorization is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_signal_authorization_no_delete
BEFORE DELETE ON live_signal_authorizations
BEGIN SELECT RAISE(ABORT, 'Signal authorization is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_candidate_authorization_no_update
BEFORE UPDATE ON live_candidate_signal_authorizations
BEGIN SELECT RAISE(ABORT, 'Candidate authorization lineage is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_candidate_authorization_no_delete
BEFORE DELETE ON live_candidate_signal_authorizations
BEGIN SELECT RAISE(ABORT, 'Candidate authorization lineage is immutable'); END;
