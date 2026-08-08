-- M3 B4: Paper execution and ledger persistence (see docs/phases/M3/spec.md
-- "Frozen persistence model"). Purely additive; 0001-0005 are untouched.

CREATE TABLE IF NOT EXISTS live_paper_market_observations (
    market_observation_id TEXT PRIMARY KEY,
    observation_contract_version TEXT NOT NULL,
    pair TEXT NOT NULL,
    bid TEXT NOT NULL,
    ask TEXT NOT NULL,
    provider_observed_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    source TEXT NOT NULL,
    source_version TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS live_paper_market_observations_no_update
BEFORE UPDATE ON live_paper_market_observations
BEGIN SELECT RAISE(ABORT, 'Paper market observation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_market_observations_no_delete
BEFORE DELETE ON live_paper_market_observations
BEGIN SELECT RAISE(ABORT, 'Paper market observation is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_fill_policies (
    paper_fill_policy_id TEXT PRIMARY KEY,
    policy_contract_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    market_selection_policy_version TEXT NOT NULL,
    fill_model_version TEXT NOT NULL,
    step_schedule_policy_version TEXT NOT NULL,
    maximum_market_age_us INTEGER NOT NULL,
    step_window_duration_us INTEGER NOT NULL,
    step_gap_us INTEGER NOT NULL,
    maximum_steps INTEGER NOT NULL,
    partial_fill_mode TEXT NOT NULL CHECK(partial_fill_mode IN ('FULL_REMAINING', 'FRACTION_OF_REMAINING')),
    partial_fill_fraction TEXT,
    slippage_basis_points TEXT NOT NULL,
    no_fill_terminal_order_state TEXT NOT NULL CHECK(no_fill_terminal_order_state IN ('REJECTED', 'CANCELLED', 'EXPIRED')),
    incomplete_terminal_order_state TEXT NOT NULL CHECK(incomplete_terminal_order_state IN ('CANCELLED', 'EXPIRED'))
);
CREATE TRIGGER IF NOT EXISTS live_paper_fill_policies_no_update
BEFORE UPDATE ON live_paper_fill_policies
BEGIN SELECT RAISE(ABORT, 'Paper fill policy is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_fill_policies_no_delete
BEFORE DELETE ON live_paper_fill_policies
BEGIN SELECT RAISE(ABORT, 'Paper fill policy is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_account_bootstraps (
    paper_account_id TEXT PRIMARY KEY,
    bootstrap_contract_version TEXT NOT NULL,
    initial_cash TEXT NOT NULL,
    settlement_currency TEXT NOT NULL CHECK(settlement_currency = 'JPY'),
    margin_policy_version TEXT NOT NULL,
    leverage TEXT NOT NULL,
    unrealized_mark_policy_version TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS live_paper_account_bootstraps_no_update
BEFORE UPDATE ON live_paper_account_bootstraps
BEGIN SELECT RAISE(ABORT, 'Paper account bootstrap is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_account_bootstraps_no_delete
BEFORE DELETE ON live_paper_account_bootstraps
BEGIN SELECT RAISE(ABORT, 'Paper account bootstrap is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_orders (
    paper_order_id TEXT PRIMARY KEY,
    order_contract_version TEXT NOT NULL,
    paper_account_id TEXT NOT NULL REFERENCES live_paper_account_bootstraps(paper_account_id),
    intent_kind TEXT NOT NULL CHECK(intent_kind IN ('ENTRY', 'ORDINARY_CLOSE', 'EMERGENCY_LIQUIDATION')),
    source_intent_id TEXT NOT NULL,
    source_intent_idempotency_key TEXT NOT NULL,
    source_intent_content_digest TEXT NOT NULL,
    paper_position_id TEXT NOT NULL,
    pair TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
    original_quantity TEXT NOT NULL,
    authority TEXT NOT NULL CHECK(authority = 'PAPER'),
    fill_policy_id TEXT NOT NULL REFERENCES live_paper_fill_policies(paper_fill_policy_id),
    intent_created_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(intent_kind, source_intent_id),
    UNIQUE(intent_kind, source_intent_idempotency_key)
);
CREATE TRIGGER IF NOT EXISTS live_paper_orders_no_update
BEFORE UPDATE ON live_paper_orders
BEGIN SELECT RAISE(ABORT, 'Paper order is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_orders_no_delete
BEFORE DELETE ON live_paper_orders
BEGIN SELECT RAISE(ABORT, 'Paper order is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_order_events (
    order_event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_order_event_id TEXT NOT NULL UNIQUE,
    paper_order_id TEXT NOT NULL REFERENCES live_paper_orders(paper_order_id),
    event_ordinal INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('ACCEPTED', 'REJECTED', 'OPEN', 'PARTIALLY_FILLED', 'FILLED', 'CANCELLED', 'EXPIRED')),
    source_evidence_kind TEXT NOT NULL,
    source_evidence_id TEXT,
    appended_at TEXT NOT NULL,
    UNIQUE(paper_order_id, event_ordinal)
);
CREATE TRIGGER IF NOT EXISTS live_paper_order_events_no_update
BEFORE UPDATE ON live_paper_order_events
BEGIN SELECT RAISE(ABORT, 'Paper order event is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_order_events_no_delete
BEFORE DELETE ON live_paper_order_events
BEGIN SELECT RAISE(ABORT, 'Paper order event is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_fill_evaluation_plans (
    fill_evaluation_plan_id TEXT PRIMARY KEY,
    plan_contract_version TEXT NOT NULL,
    paper_order_id TEXT NOT NULL UNIQUE REFERENCES live_paper_orders(paper_order_id),
    pair TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
    original_quantity TEXT NOT NULL,
    fill_policy_id TEXT NOT NULL REFERENCES live_paper_fill_policies(paper_fill_policy_id),
    intent_created_at TEXT NOT NULL,
    maximum_steps INTEGER NOT NULL,
    plan_expiry_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS live_paper_fill_evaluation_plans_no_update
BEFORE UPDATE ON live_paper_fill_evaluation_plans
BEGIN SELECT RAISE(ABORT, 'Paper fill evaluation plan is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_fill_evaluation_plans_no_delete
BEFORE DELETE ON live_paper_fill_evaluation_plans
BEGIN SELECT RAISE(ABORT, 'Paper fill evaluation plan is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_fill_evaluation_steps (
    fill_evaluation_step_id TEXT PRIMARY KEY,
    step_contract_version TEXT NOT NULL,
    fill_evaluation_plan_id TEXT NOT NULL REFERENCES live_paper_fill_evaluation_plans(fill_evaluation_plan_id),
    ordinal INTEGER NOT NULL,
    evaluation_window_start_at TEXT NOT NULL,
    evaluation_due_at TEXT NOT NULL,
    remaining_quantity_before TEXT NOT NULL,
    fill_policy_id TEXT NOT NULL REFERENCES live_paper_fill_policies(paper_fill_policy_id),
    created_at TEXT NOT NULL,
    UNIQUE(fill_evaluation_plan_id, ordinal)
);
CREATE TRIGGER IF NOT EXISTS live_paper_fill_evaluation_steps_no_update
BEFORE UPDATE ON live_paper_fill_evaluation_steps
BEGIN SELECT RAISE(ABORT, 'Paper fill evaluation step is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_fill_evaluation_steps_no_delete
BEFORE DELETE ON live_paper_fill_evaluation_steps
BEGIN SELECT RAISE(ABORT, 'Paper fill evaluation step is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_fill_evaluation_attempts (
    fill_evaluation_attempt_id TEXT PRIMARY KEY,
    fill_evaluation_step_id TEXT NOT NULL REFERENCES live_paper_fill_evaluation_steps(fill_evaluation_step_id),
    evaluated_at TEXT NOT NULL,
    disposition TEXT NOT NULL CHECK(disposition = 'PENDING_NO_ELIGIBLE_MARKET'),
    diagnostic_code TEXT NOT NULL CHECK(diagnostic_code IN ('NO_OBSERVATION_FOR_PAIR', 'ALL_OBSERVATIONS_INELIGIBLE')),
    worker_identity TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS live_paper_fill_evaluation_attempts_requires_unresolved_step
BEFORE INSERT ON live_paper_fill_evaluation_attempts
WHEN EXISTS (
    SELECT 1 FROM live_paper_step_terminal_claims
    WHERE fill_evaluation_step_id = NEW.fill_evaluation_step_id
)
BEGIN SELECT RAISE(ABORT, 'An attempt cannot be appended after terminal Step resolution'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_fill_evaluation_attempts_no_update
BEFORE UPDATE ON live_paper_fill_evaluation_attempts
BEGIN SELECT RAISE(ABORT, 'Paper fill evaluation attempt is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_fill_evaluation_attempts_no_delete
BEFORE DELETE ON live_paper_fill_evaluation_attempts
BEGIN SELECT RAISE(ABORT, 'Paper fill evaluation attempt is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_step_terminal_claims (
    fill_evaluation_step_id TEXT PRIMARY KEY REFERENCES live_paper_fill_evaluation_steps(fill_evaluation_step_id),
    variant TEXT NOT NULL CHECK(variant IN ('MARKET_SELECTED', 'NO_MARKET')),
    resolution_id TEXT NOT NULL UNIQUE CHECK(resolution_id != fill_evaluation_step_id),
    resolved_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS live_paper_step_terminal_claims_no_update
BEFORE UPDATE ON live_paper_step_terminal_claims
BEGIN SELECT RAISE(ABORT, 'Paper Step terminal claim is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_step_terminal_claims_no_delete
BEFORE DELETE ON live_paper_step_terminal_claims
BEGIN SELECT RAISE(ABORT, 'Paper Step terminal claim is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_market_observation_selections (
    market_observation_selection_id TEXT PRIMARY KEY,
    fill_evaluation_step_id TEXT NOT NULL UNIQUE REFERENCES live_paper_fill_evaluation_steps(fill_evaluation_step_id),
    fill_evaluation_plan_id TEXT NOT NULL REFERENCES live_paper_fill_evaluation_plans(fill_evaluation_plan_id),
    market_observation_id TEXT NOT NULL REFERENCES live_paper_market_observations(market_observation_id),
    market_selection_policy_version TEXT NOT NULL,
    evaluation_window_start_at TEXT NOT NULL,
    evaluation_due_at TEXT NOT NULL,
    intent_created_at TEXT NOT NULL,
    selected_at TEXT NOT NULL,
    UNIQUE(fill_evaluation_plan_id, market_observation_id)
);
CREATE TRIGGER IF NOT EXISTS live_paper_market_observation_selections_requires_claim
BEFORE INSERT ON live_paper_market_observation_selections
WHEN NOT EXISTS (
    SELECT 1 FROM live_paper_step_terminal_claims
    WHERE fill_evaluation_step_id = NEW.fill_evaluation_step_id
    AND variant = 'MARKET_SELECTED'
    AND resolution_id = NEW.market_observation_selection_id
)
BEGIN SELECT RAISE(ABORT, 'Market observation selection requires its own MARKET_SELECTED claim'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_market_observation_selections_no_update
BEFORE UPDATE ON live_paper_market_observation_selections
BEGIN SELECT RAISE(ABORT, 'Paper market observation selection is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_market_observation_selections_no_delete
BEFORE DELETE ON live_paper_market_observation_selections
BEGIN SELECT RAISE(ABORT, 'Paper market observation selection is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_no_market_outcomes (
    no_market_outcome_id TEXT PRIMARY KEY,
    fill_evaluation_step_id TEXT NOT NULL UNIQUE REFERENCES live_paper_fill_evaluation_steps(fill_evaluation_step_id),
    terminal_reason_code TEXT NOT NULL CHECK(terminal_reason_code = 'REJECTED_NO_MARKET_EVIDENCE'),
    evaluation_due_at TEXT NOT NULL,
    resolved_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS live_paper_no_market_outcomes_requires_claim
BEFORE INSERT ON live_paper_no_market_outcomes
WHEN NOT EXISTS (
    SELECT 1 FROM live_paper_step_terminal_claims
    WHERE fill_evaluation_step_id = NEW.fill_evaluation_step_id
    AND variant = 'NO_MARKET'
    AND resolution_id = NEW.no_market_outcome_id
)
BEGIN SELECT RAISE(ABORT, 'No-market outcome requires its own NO_MARKET claim'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_no_market_outcomes_no_update
BEFORE UPDATE ON live_paper_no_market_outcomes
BEGIN SELECT RAISE(ABORT, 'Paper no-market outcome is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_no_market_outcomes_no_delete
BEFORE DELETE ON live_paper_no_market_outcomes
BEGIN SELECT RAISE(ABORT, 'Paper no-market outcome is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_fills (
    paper_fill_id TEXT PRIMARY KEY,
    fill_contract_version TEXT NOT NULL,
    fill_evaluation_step_id TEXT NOT NULL REFERENCES live_paper_fill_evaluation_steps(fill_evaluation_step_id),
    market_observation_selection_id TEXT NOT NULL UNIQUE REFERENCES live_paper_market_observation_selections(market_observation_selection_id),
    market_observation_id TEXT NOT NULL REFERENCES live_paper_market_observations(market_observation_id),
    pair TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
    fill_quantity TEXT NOT NULL,
    fill_price TEXT NOT NULL,
    reference_price TEXT NOT NULL,
    slippage_basis_points TEXT NOT NULL,
    fill_model_version TEXT NOT NULL,
    remaining_quantity_before TEXT NOT NULL,
    remaining_quantity_after TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS live_paper_fills_no_update
BEFORE UPDATE ON live_paper_fills
BEGIN SELECT RAISE(ABORT, 'Paper Fill is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_fills_no_delete
BEFORE DELETE ON live_paper_fills
BEGIN SELECT RAISE(ABORT, 'Paper Fill is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_positions (
    paper_position_id TEXT PRIMARY KEY,
    position_contract_version TEXT NOT NULL,
    paper_account_id TEXT NOT NULL REFERENCES live_paper_account_bootstraps(paper_account_id),
    entry_paper_order_id TEXT NOT NULL REFERENCES live_paper_orders(paper_order_id),
    pair TEXT NOT NULL,
    position_side TEXT NOT NULL CHECK(position_side IN ('LONG', 'SHORT')),
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS live_paper_positions_no_update
BEFORE UPDATE ON live_paper_positions
BEGIN SELECT RAISE(ABORT, 'Paper position is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_positions_no_delete
BEFORE DELETE ON live_paper_positions
BEGIN SELECT RAISE(ABORT, 'Paper position is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_position_fill_applications (
    application_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_position_fill_application_id TEXT NOT NULL UNIQUE,
    application_contract_version TEXT NOT NULL,
    paper_position_id TEXT NOT NULL REFERENCES live_paper_positions(paper_position_id),
    paper_order_id TEXT NOT NULL REFERENCES live_paper_orders(paper_order_id),
    paper_fill_id TEXT NOT NULL UNIQUE REFERENCES live_paper_fills(paper_fill_id),
    application_kind TEXT NOT NULL CHECK(application_kind IN ('ENTRY', 'REDUCE_ONLY')),
    quantity TEXT NOT NULL,
    price TEXT NOT NULL,
    open_quantity_after TEXT NOT NULL,
    realized_pnl_amount TEXT,
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS live_paper_position_fill_applications_no_update
BEFORE UPDATE ON live_paper_position_fill_applications
BEGIN SELECT RAISE(ABORT, 'Paper position fill application is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_position_fill_applications_no_delete
BEFORE DELETE ON live_paper_position_fill_applications
BEGIN SELECT RAISE(ABORT, 'Paper position fill application is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_position_snapshots (
    paper_position_snapshot_id TEXT PRIMARY KEY,
    snapshot_contract_version TEXT NOT NULL,
    paper_account_id TEXT NOT NULL REFERENCES live_paper_account_bootstraps(paper_account_id),
    paper_position_id TEXT NOT NULL REFERENCES live_paper_positions(paper_position_id),
    pair TEXT NOT NULL,
    position_side TEXT NOT NULL CHECK(position_side IN ('LONG', 'SHORT')),
    open_quantity TEXT NOT NULL,
    average_entry_price TEXT NOT NULL,
    realized_pnl_total TEXT NOT NULL,
    accrued_swap_total TEXT NOT NULL,
    highest_application_seq INTEGER NOT NULL,
    highest_ledger_entry_seq INTEGER NOT NULL,
    average_entry_price_formula_version TEXT NOT NULL,
    realized_pnl_formula_version TEXT NOT NULL,
    swap_accrual_formula_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS live_paper_position_snapshots_no_update
BEFORE UPDATE ON live_paper_position_snapshots
BEGIN SELECT RAISE(ABORT, 'Paper position snapshot is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_position_snapshots_no_delete
BEFORE DELETE ON live_paper_position_snapshots
BEGIN SELECT RAISE(ABORT, 'Paper position snapshot is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_account_snapshots (
    paper_account_snapshot_id TEXT PRIMARY KEY,
    snapshot_contract_version TEXT NOT NULL,
    paper_account_id TEXT NOT NULL REFERENCES live_paper_account_bootstraps(paper_account_id),
    cash TEXT NOT NULL,
    realized_pnl_total TEXT NOT NULL,
    unrealized_pnl_total TEXT NOT NULL,
    accrued_swap_total TEXT NOT NULL,
    equity TEXT NOT NULL,
    used_margin TEXT NOT NULL,
    available_margin TEXT NOT NULL,
    gross_exposure TEXT NOT NULL,
    open_position_count INTEGER NOT NULL,
    open_order_count INTEGER NOT NULL,
    mark_observation_ids_json TEXT NOT NULL,
    highest_application_seq INTEGER NOT NULL,
    highest_ledger_entry_seq INTEGER NOT NULL,
    highest_order_event_seq INTEGER NOT NULL,
    margin_policy_version TEXT NOT NULL,
    unrealized_mark_policy_version TEXT NOT NULL,
    formula_versions_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS live_paper_account_snapshots_no_update
BEFORE UPDATE ON live_paper_account_snapshots
BEGIN SELECT RAISE(ABORT, 'Paper account snapshot is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_account_snapshots_no_delete
BEFORE DELETE ON live_paper_account_snapshots
BEGIN SELECT RAISE(ABORT, 'Paper account snapshot is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_ledger_entries (
    ledger_entry_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ledger_entry_id TEXT NOT NULL UNIQUE,
    entry_contract_version TEXT NOT NULL,
    paper_account_id TEXT NOT NULL REFERENCES live_paper_account_bootstraps(paper_account_id),
    paper_position_id TEXT NOT NULL REFERENCES live_paper_positions(paper_position_id),
    entry_kind TEXT NOT NULL CHECK(entry_kind IN ('REALIZED_PNL', 'SWAP_ACCRUAL', 'SWAP_ACCRUAL_CORRECTION')),
    settlement_currency TEXT NOT NULL CHECK(settlement_currency = 'JPY'),
    amount TEXT NOT NULL,
    source_evidence_kind TEXT NOT NULL,
    source_evidence_id TEXT NOT NULL,
    formula_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(entry_kind, source_evidence_id)
);
CREATE TRIGGER IF NOT EXISTS live_paper_ledger_entries_no_update
BEFORE UPDATE ON live_paper_ledger_entries
BEGIN SELECT RAISE(ABORT, 'Paper ledger entry is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_ledger_entries_no_delete
BEFORE DELETE ON live_paper_ledger_entries
BEGIN SELECT RAISE(ABORT, 'Paper ledger entry is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_swap_rollover_claims (
    paper_position_id TEXT NOT NULL REFERENCES live_paper_positions(paper_position_id),
    rollover_date TEXT NOT NULL,
    variant TEXT NOT NULL CHECK(variant IN ('ACCRUED', 'NOT_ACCRUED')),
    evidence_id TEXT NOT NULL UNIQUE CHECK(evidence_id != paper_position_id),
    resolved_at TEXT NOT NULL,
    PRIMARY KEY (paper_position_id, rollover_date)
);
CREATE TRIGGER IF NOT EXISTS live_paper_swap_rollover_claims_no_update
BEFORE UPDATE ON live_paper_swap_rollover_claims
BEGIN SELECT RAISE(ABORT, 'Paper swap rollover claim is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_swap_rollover_claims_no_delete
BEFORE DELETE ON live_paper_swap_rollover_claims
BEGIN SELECT RAISE(ABORT, 'Paper swap rollover claim is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_swap_accruals (
    paper_swap_accrual_id TEXT PRIMARY KEY,
    accrual_contract_version TEXT NOT NULL,
    paper_position_id TEXT NOT NULL REFERENCES live_paper_positions(paper_position_id),
    paper_position_snapshot_id TEXT NOT NULL REFERENCES live_paper_position_snapshots(paper_position_snapshot_id),
    swap_evidence_id TEXT NOT NULL,
    rollover_date TEXT NOT NULL,
    open_quantity TEXT NOT NULL,
    unit_basis TEXT NOT NULL,
    base_units_per_unit TEXT NOT NULL,
    settlement_currency TEXT NOT NULL CHECK(settlement_currency = 'JPY'),
    policy_version TEXT NOT NULL,
    formula_version TEXT NOT NULL,
    amount TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(paper_position_id, rollover_date)
);
CREATE TRIGGER IF NOT EXISTS live_paper_swap_accruals_requires_accrued_claim
BEFORE INSERT ON live_paper_swap_accruals
WHEN NOT EXISTS (
    SELECT 1 FROM live_paper_swap_rollover_claims
    WHERE paper_position_id = NEW.paper_position_id
    AND rollover_date = NEW.rollover_date
    AND variant = 'ACCRUED'
    AND evidence_id = NEW.paper_swap_accrual_id
)
BEGIN SELECT RAISE(ABORT, 'Swap accrual requires its own ACCRUED rollover claim'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_swap_accruals_no_update
BEFORE UPDATE ON live_paper_swap_accruals
BEGIN SELECT RAISE(ABORT, 'Paper swap accrual is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_swap_accruals_no_delete
BEFORE DELETE ON live_paper_swap_accruals
BEGIN SELECT RAISE(ABORT, 'Paper swap accrual is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_swap_non_accruals (
    paper_swap_non_accrual_id TEXT PRIMARY KEY,
    non_accrual_contract_version TEXT NOT NULL,
    paper_position_id TEXT NOT NULL REFERENCES live_paper_positions(paper_position_id),
    paper_position_snapshot_id TEXT NOT NULL REFERENCES live_paper_position_snapshots(paper_position_snapshot_id),
    swap_evidence_id TEXT,
    rollover_date TEXT NOT NULL,
    outcome TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(paper_position_id, rollover_date)
);
CREATE TRIGGER IF NOT EXISTS live_paper_swap_non_accruals_requires_not_accrued_claim
BEFORE INSERT ON live_paper_swap_non_accruals
WHEN NOT EXISTS (
    SELECT 1 FROM live_paper_swap_rollover_claims
    WHERE paper_position_id = NEW.paper_position_id
    AND rollover_date = NEW.rollover_date
    AND variant = 'NOT_ACCRUED'
    AND evidence_id = NEW.paper_swap_non_accrual_id
)
BEGIN SELECT RAISE(ABORT, 'Swap non-accrual requires its own NOT_ACCRUED rollover claim'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_swap_non_accruals_no_update
BEFORE UPDATE ON live_paper_swap_non_accruals
BEGIN SELECT RAISE(ABORT, 'Paper swap non-accrual is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_swap_non_accruals_no_delete
BEFORE DELETE ON live_paper_swap_non_accruals
BEGIN SELECT RAISE(ABORT, 'Paper swap non-accrual is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_swap_accrual_corrections (
    correction_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    correction_id TEXT NOT NULL UNIQUE,
    correction_contract_version TEXT NOT NULL,
    corrected_accrual_id TEXT NOT NULL REFERENCES live_paper_swap_accruals(paper_swap_accrual_id),
    chain_ordinal INTEGER NOT NULL,
    predecessor_correction_id TEXT UNIQUE,
    effective_amount_before TEXT NOT NULL,
    replacement_amount TEXT NOT NULL,
    delta_amount TEXT NOT NULL,
    correction_reason TEXT NOT NULL,
    swap_evidence_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(corrected_accrual_id, chain_ordinal)
);
CREATE TRIGGER IF NOT EXISTS live_paper_swap_accrual_corrections_no_update
BEFORE UPDATE ON live_paper_swap_accrual_corrections
BEGIN SELECT RAISE(ABORT, 'Paper swap accrual correction is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_swap_accrual_corrections_no_delete
BEFORE DELETE ON live_paper_swap_accrual_corrections
BEGIN SELECT RAISE(ABORT, 'Paper swap accrual correction is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_reservation_consumptions (
    consumption_id TEXT PRIMARY KEY,
    contract_version TEXT NOT NULL,
    close_intent_idempotency_key TEXT NOT NULL,
    paper_order_id TEXT NOT NULL REFERENCES live_paper_orders(paper_order_id),
    paper_fill_id TEXT NOT NULL UNIQUE REFERENCES live_paper_fills(paper_fill_id),
    consumed_quantity TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS live_paper_reservation_consumptions_no_update
BEFORE UPDATE ON live_paper_reservation_consumptions
BEGIN SELECT RAISE(ABORT, 'Paper reservation consumption is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_reservation_consumptions_no_delete
BEFORE DELETE ON live_paper_reservation_consumptions
BEGIN SELECT RAISE(ABORT, 'Paper reservation consumption is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_reservation_releases (
    release_id TEXT PRIMARY KEY,
    contract_version TEXT NOT NULL,
    close_intent_idempotency_key TEXT NOT NULL,
    paper_order_id TEXT NOT NULL UNIQUE REFERENCES live_paper_orders(paper_order_id),
    terminal_order_state TEXT NOT NULL CHECK(terminal_order_state IN ('CANCELLED', 'EXPIRED', 'REJECTED')),
    released_quantity TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS live_paper_reservation_releases_no_update
BEFORE UPDATE ON live_paper_reservation_releases
BEGIN SELECT RAISE(ABORT, 'Paper reservation release is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_reservation_releases_no_delete
BEFORE DELETE ON live_paper_reservation_releases
BEGIN SELECT RAISE(ABORT, 'Paper reservation release is immutable'); END;

CREATE TABLE IF NOT EXISTS live_paper_reconciliation_results (
    reconciliation_result_id TEXT PRIMARY KEY,
    result_contract_version TEXT NOT NULL,
    paper_account_id TEXT NOT NULL REFERENCES live_paper_account_bootstraps(paper_account_id),
    outcome TEXT NOT NULL CHECK(outcome IN ('MATCHED', 'MISMATCHED')),
    reconciled_position_ids_json TEXT NOT NULL,
    highest_application_seq INTEGER NOT NULL,
    highest_ledger_entry_seq INTEGER NOT NULL,
    highest_order_event_seq INTEGER NOT NULL,
    mismatched_record_kinds_json TEXT NOT NULL,
    mismatched_record_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS live_paper_reconciliation_results_no_update
BEFORE UPDATE ON live_paper_reconciliation_results
BEGIN SELECT RAISE(ABORT, 'Paper reconciliation result is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_paper_reconciliation_results_no_delete
BEFORE DELETE ON live_paper_reconciliation_results
BEGIN SELECT RAISE(ABORT, 'Paper reconciliation result is immutable'); END;
