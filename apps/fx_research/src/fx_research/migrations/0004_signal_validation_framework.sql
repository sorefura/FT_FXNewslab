CREATE TABLE IF NOT EXISTS research_evaluation_runs (
    run_id TEXT PRIMARY KEY,
    evaluator_version TEXT NOT NULL,
    score_definition_version TEXT NOT NULL,
    cohort_definition_version TEXT NOT NULL,
    ordered_input_identity_hash TEXT NOT NULL,
    metric_configuration_json TEXT NOT NULL,
    bootstrap_configuration_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_evaluation_run_inputs (
    run_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    signal_id TEXT NOT NULL,
    forward_result_id TEXT NOT NULL,
    cohort_id TEXT NOT NULL,
    PRIMARY KEY(run_id, ordinal),
    UNIQUE(run_id, signal_id, forward_result_id),
    FOREIGN KEY(run_id) REFERENCES research_evaluation_runs(run_id),
    FOREIGN KEY(signal_id) REFERENCES signals(id),
    FOREIGN KEY(forward_result_id) REFERENCES research_forward_results(result_id)
);

CREATE TABLE IF NOT EXISTS research_evaluation_reports (
    report_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    cohort_id TEXT NOT NULL,
    cohort_identity_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, cohort_id),
    FOREIGN KEY(run_id) REFERENCES research_evaluation_runs(run_id)
);

CREATE TABLE IF NOT EXISTS research_validation_policies (
    policy_version TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_validation_assessments (
    assessment_id TEXT PRIMARY KEY,
    evaluation_run_id TEXT NOT NULL,
    report_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    policy_content_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(
        status IN ('EXPERIMENTAL', 'PROMISING', 'VALIDATED_FOR_RESEARCH')
    ),
    condition_results_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(report_id, policy_version, policy_content_hash),
    FOREIGN KEY(evaluation_run_id) REFERENCES research_evaluation_runs(run_id),
    FOREIGN KEY(report_id) REFERENCES research_evaluation_reports(report_id),
    FOREIGN KEY(policy_version) REFERENCES research_validation_policies(policy_version)
);

CREATE TRIGGER IF NOT EXISTS research_evaluation_runs_no_update
BEFORE UPDATE ON research_evaluation_runs
BEGIN SELECT RAISE(ABORT, 'evaluation runs are immutable'); END;

CREATE TRIGGER IF NOT EXISTS research_evaluation_runs_no_delete
BEFORE DELETE ON research_evaluation_runs
BEGIN SELECT RAISE(ABORT, 'evaluation runs are immutable'); END;

CREATE TRIGGER IF NOT EXISTS research_evaluation_run_inputs_no_update
BEFORE UPDATE ON research_evaluation_run_inputs
BEGIN SELECT RAISE(ABORT, 'evaluation inputs are immutable'); END;

CREATE TRIGGER IF NOT EXISTS research_evaluation_run_inputs_no_delete
BEFORE DELETE ON research_evaluation_run_inputs
BEGIN SELECT RAISE(ABORT, 'evaluation inputs are immutable'); END;

CREATE TRIGGER IF NOT EXISTS research_evaluation_reports_no_update
BEFORE UPDATE ON research_evaluation_reports
BEGIN SELECT RAISE(ABORT, 'evaluation reports are immutable'); END;

CREATE TRIGGER IF NOT EXISTS research_evaluation_reports_no_delete
BEFORE DELETE ON research_evaluation_reports
BEGIN SELECT RAISE(ABORT, 'evaluation reports are immutable'); END;

CREATE TRIGGER IF NOT EXISTS research_validation_policies_no_update
BEFORE UPDATE ON research_validation_policies
BEGIN SELECT RAISE(ABORT, 'validation policies are immutable'); END;

CREATE TRIGGER IF NOT EXISTS research_validation_policies_no_delete
BEFORE DELETE ON research_validation_policies
BEGIN SELECT RAISE(ABORT, 'validation policies are immutable'); END;

CREATE TRIGGER IF NOT EXISTS research_validation_assessments_no_update
BEFORE UPDATE ON research_validation_assessments
BEGIN SELECT RAISE(ABORT, 'validation assessments are immutable'); END;

CREATE TRIGGER IF NOT EXISTS research_validation_assessments_no_delete
BEFORE DELETE ON research_validation_assessments
BEGIN SELECT RAISE(ABORT, 'validation assessments are immutable'); END;
