CREATE TABLE IF NOT EXISTS live_ordinary_close_capacity_evidence (
    capacity_evidence_id TEXT PRIMARY KEY,
    capacity_contract_version TEXT NOT NULL,
    position_id TEXT NOT NULL,
    position_evidence_id TEXT NOT NULL,
    pair TEXT NOT NULL,
    existing_position_side TEXT NOT NULL,
    position_observed_at TEXT NOT NULL,
    open_quantity TEXT NOT NULL,
    quantity_unit TEXT NOT NULL CHECK(quantity_unit = 'BASE_UNITS'),
    source TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS live_ordinary_close_signal_resolutions (
    resolution_id TEXT PRIMARY KEY,
    outcome TEXT NOT NULL CHECK(
        outcome IN ('AUTHORIZED', 'NO_SELECTION', 'AMBIGUOUS', 'ADOPTION_INACTIVE')
    ),
    signal_selection_checkpoint_id TEXT NOT NULL,
    selection_request_id TEXT,
    selection_claim_id TEXT,
    selection_snapshot_id TEXT,
    selection_completion_id TEXT,
    prior_adoption_decision_id TEXT NOT NULL,
    adoption_state_evidence_id TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    resolved_at TEXT NOT NULL,
    signal_id TEXT,
    authorization_id TEXT REFERENCES live_signal_authorizations(authorization_id),
    CHECK(
        (outcome = 'AUTHORIZED' AND signal_id IS NOT NULL AND authorization_id IS NOT NULL)
        OR (outcome != 'AUTHORIZED' AND signal_id IS NULL AND authorization_id IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS live_ordinary_close_work_items (
    work_item_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    pair TEXT NOT NULL,
    existing_position_side TEXT NOT NULL,
    strategy_config_identity TEXT NOT NULL REFERENCES
        live_news_filtered_carry_configs(strategy_config_identity),
    capacity_evidence_id TEXT NOT NULL REFERENCES
        live_ordinary_close_capacity_evidence(capacity_evidence_id),
    resolution_id TEXT NOT NULL REFERENCES
        live_ordinary_close_signal_resolutions(resolution_id),
    swap_evidence_id TEXT REFERENCES
        live_operational_swap_evidence(swap_evidence_id),
    authority TEXT NOT NULL CHECK(authority IN ('SHADOW_NOT_SUBMITTED', 'PAPER')),
    work_item_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS live_ordinary_close_operational_evaluations (
    operational_evaluation_id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL UNIQUE REFERENCES
        live_ordinary_close_work_items(work_item_id),
    evaluation_id TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('CLOSE_CANDIDATE', 'KEEP')),
    close_candidate_id TEXT,
    keep_reason TEXT,
    evaluation_json TEXT NOT NULL,
    CHECK(
        (outcome = 'CLOSE_CANDIDATE' AND close_candidate_id IS NOT NULL AND keep_reason IS NULL)
        OR (outcome = 'KEEP' AND close_candidate_id IS NULL AND keep_reason IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS live_ordinary_close_candidates (
    close_candidate_id TEXT PRIMARY KEY,
    operational_evaluation_id TEXT NOT NULL UNIQUE REFERENCES
        live_ordinary_close_operational_evaluations(operational_evaluation_id),
    candidate_json TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS live_ordinary_close_candidate_requires_close_evaluation
BEFORE INSERT ON live_ordinary_close_candidates
WHEN NOT EXISTS (
    SELECT 1 FROM live_ordinary_close_operational_evaluations
    WHERE operational_evaluation_id = NEW.operational_evaluation_id
    AND outcome = 'CLOSE_CANDIDATE'
)
BEGIN SELECT RAISE(ABORT, 'Ordinary close Candidate requires a CLOSE_CANDIDATE evaluation'); END;

CREATE TRIGGER IF NOT EXISTS live_ordinary_close_capacity_evidence_no_update
BEFORE UPDATE ON live_ordinary_close_capacity_evidence
BEGIN SELECT RAISE(ABORT, 'Ordinary close capacity evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_ordinary_close_capacity_evidence_no_delete
BEFORE DELETE ON live_ordinary_close_capacity_evidence
BEGIN SELECT RAISE(ABORT, 'Ordinary close capacity evidence is immutable'); END;

CREATE TRIGGER IF NOT EXISTS live_ordinary_close_signal_resolutions_no_update
BEFORE UPDATE ON live_ordinary_close_signal_resolutions
BEGIN SELECT RAISE(ABORT, 'Ordinary close Signal/Adoption resolution is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_ordinary_close_signal_resolutions_no_delete
BEFORE DELETE ON live_ordinary_close_signal_resolutions
BEGIN SELECT RAISE(ABORT, 'Ordinary close Signal/Adoption resolution is immutable'); END;

CREATE TRIGGER IF NOT EXISTS live_ordinary_close_work_items_no_update
BEFORE UPDATE ON live_ordinary_close_work_items
BEGIN SELECT RAISE(ABORT, 'Ordinary close work item is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_ordinary_close_work_items_no_delete
BEFORE DELETE ON live_ordinary_close_work_items
BEGIN SELECT RAISE(ABORT, 'Ordinary close work item is immutable'); END;

CREATE TRIGGER IF NOT EXISTS live_ordinary_close_operational_evaluations_no_update
BEFORE UPDATE ON live_ordinary_close_operational_evaluations
BEGIN SELECT RAISE(ABORT, 'Ordinary close operational evaluation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_ordinary_close_operational_evaluations_no_delete
BEFORE DELETE ON live_ordinary_close_operational_evaluations
BEGIN SELECT RAISE(ABORT, 'Ordinary close operational evaluation is immutable'); END;

CREATE TRIGGER IF NOT EXISTS live_ordinary_close_candidates_no_update
BEFORE UPDATE ON live_ordinary_close_candidates
BEGIN SELECT RAISE(ABORT, 'Ordinary close Candidate is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_ordinary_close_candidates_no_delete
BEFORE DELETE ON live_ordinary_close_candidates
BEGIN SELECT RAISE(ABORT, 'Ordinary close Candidate is immutable'); END;

CREATE TABLE IF NOT EXISTS live_ordinary_close_portfolio_decisions (
    portfolio_decision_id TEXT PRIMARY KEY,
    close_candidate_id TEXT NOT NULL UNIQUE REFERENCES
        live_ordinary_close_candidates(close_candidate_id),
    operational_evaluation_id TEXT NOT NULL REFERENCES
        live_ordinary_close_operational_evaluations(operational_evaluation_id),
    capacity_evidence_id TEXT NOT NULL REFERENCES
        live_ordinary_close_capacity_evidence(capacity_evidence_id),
    allocation_policy_version TEXT NOT NULL,
    target_fraction TEXT NOT NULL,
    position_id TEXT NOT NULL,
    reservation_snapshot_json TEXT NOT NULL,
    target_quantity TEXT NOT NULL,
    available_before TEXT NOT NULL,
    disposition TEXT NOT NULL CHECK(disposition IN ('ACCEPT', 'REDUCE', 'REJECT')),
    allocated_quantity TEXT,
    CHECK(
        (disposition = 'REJECT' AND allocated_quantity IS NULL)
        OR (disposition != 'REJECT' AND allocated_quantity IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS live_ordinary_close_risk_decisions (
    risk_decision_id TEXT PRIMARY KEY,
    portfolio_decision_id TEXT NOT NULL UNIQUE REFERENCES
        live_ordinary_close_portfolio_decisions(portfolio_decision_id),
    risk_policy_version TEXT NOT NULL,
    maximum_capacity_age_us INTEGER NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('APPROVE', 'REJECT')),
    reason TEXT NOT NULL CHECK(reason IN (
        'APPROVED', 'PORTFOLIO_REJECTED', 'CAPACITY_IN_FUTURE',
        'CAPACITY_STALE', 'NON_POSITIVE_QUANTITY', 'OVERCLOSE_QUANTITY'
    )),
    CHECK(
        (outcome = 'APPROVE' AND reason = 'APPROVED')
        OR (outcome = 'REJECT' AND reason != 'APPROVED')
    )
);

CREATE TABLE IF NOT EXISTS live_ordinary_close_approved_intents (
    intent_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    close_candidate_id TEXT NOT NULL UNIQUE REFERENCES
        live_ordinary_close_candidates(close_candidate_id),
    portfolio_decision_id TEXT NOT NULL UNIQUE REFERENCES
        live_ordinary_close_portfolio_decisions(portfolio_decision_id),
    risk_decision_id TEXT NOT NULL UNIQUE REFERENCES
        live_ordinary_close_risk_decisions(risk_decision_id),
    capacity_evidence_id TEXT NOT NULL REFERENCES
        live_ordinary_close_capacity_evidence(capacity_evidence_id),
    position_id TEXT NOT NULL,
    pair TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
    quantity TEXT NOT NULL,
    authority TEXT NOT NULL CHECK(authority IN ('SHADOW_NOT_SUBMITTED', 'PAPER')),
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS live_ordinary_close_approved_intents_requires_risk_approve
BEFORE INSERT ON live_ordinary_close_approved_intents
WHEN NOT EXISTS (
    SELECT 1 FROM live_ordinary_close_risk_decisions
    WHERE risk_decision_id = NEW.risk_decision_id AND outcome = 'APPROVE'
)
BEGIN SELECT RAISE(ABORT, 'Approved close Intent requires a Risk APPROVE decision'); END;

CREATE TRIGGER IF NOT EXISTS live_ordinary_close_portfolio_decisions_no_update
BEFORE UPDATE ON live_ordinary_close_portfolio_decisions
BEGIN SELECT RAISE(ABORT, 'Ordinary close Portfolio decision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_ordinary_close_portfolio_decisions_no_delete
BEFORE DELETE ON live_ordinary_close_portfolio_decisions
BEGIN SELECT RAISE(ABORT, 'Ordinary close Portfolio decision is immutable'); END;

CREATE TRIGGER IF NOT EXISTS live_ordinary_close_risk_decisions_no_update
BEFORE UPDATE ON live_ordinary_close_risk_decisions
BEGIN SELECT RAISE(ABORT, 'Ordinary close Risk decision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_ordinary_close_risk_decisions_no_delete
BEFORE DELETE ON live_ordinary_close_risk_decisions
BEGIN SELECT RAISE(ABORT, 'Ordinary close Risk decision is immutable'); END;

CREATE TRIGGER IF NOT EXISTS live_ordinary_close_approved_intents_no_update
BEFORE UPDATE ON live_ordinary_close_approved_intents
BEGIN SELECT RAISE(ABORT, 'Approved close Intent is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_ordinary_close_approved_intents_no_delete
BEFORE DELETE ON live_ordinary_close_approved_intents
BEGIN SELECT RAISE(ABORT, 'Approved close Intent is immutable'); END;
