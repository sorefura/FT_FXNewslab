CREATE TABLE IF NOT EXISTS research_evaluation_input_snapshots (
    run_id TEXT PRIMARY KEY,
    snapshot_version TEXT NOT NULL,
    snapshot_identity_hash TEXT NOT NULL,
    signals_scanned INTEGER NOT NULL CHECK(signals_scanned >= 0),
    completed_results_scanned INTEGER NOT NULL CHECK(completed_results_scanned >= 0),
    snapshot_json TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES research_evaluation_runs(run_id)
);

CREATE TRIGGER IF NOT EXISTS research_evaluation_input_snapshots_no_update
BEFORE UPDATE ON research_evaluation_input_snapshots
BEGIN SELECT RAISE(ABORT, 'evaluation input snapshots are immutable'); END;

CREATE TRIGGER IF NOT EXISTS research_evaluation_input_snapshots_no_delete
BEFORE DELETE ON research_evaluation_input_snapshots
BEGIN SELECT RAISE(ABORT, 'evaluation input snapshots are immutable'); END;
