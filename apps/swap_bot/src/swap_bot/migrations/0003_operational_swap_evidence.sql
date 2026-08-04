CREATE TABLE IF NOT EXISTS live_operational_swap_evidence (
    swap_evidence_id TEXT PRIMARY KEY,
    evidence_contract_version TEXT NOT NULL,
    pair TEXT NOT NULL,
    availability TEXT NOT NULL,
    long_received_amount TEXT,
    short_received_amount TEXT,
    unit_basis TEXT,
    settlement_currency TEXT,
    source TEXT NOT NULL,
    source_version TEXT NOT NULL,
    provider_observed_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_until TEXT
);

CREATE TRIGGER IF NOT EXISTS live_operational_swap_evidence_no_update
BEFORE UPDATE ON live_operational_swap_evidence
BEGIN SELECT RAISE(ABORT, 'Operational Swap evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_operational_swap_evidence_no_delete
BEFORE DELETE ON live_operational_swap_evidence
BEGIN SELECT RAISE(ABORT, 'Operational Swap evidence is immutable'); END;
