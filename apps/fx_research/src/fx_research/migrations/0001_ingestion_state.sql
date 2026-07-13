CREATE TABLE IF NOT EXISTS research_fetch_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('SUCCESS', 'FAILED')),
    item_count INTEGER NOT NULL,
    error_code TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS research_ingestion_items (
    observation_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    candidate_currency TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_date_text TEXT,
    first_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_feature_jobs (
    observation_id TEXT NOT NULL,
    producer_version TEXT NOT NULL,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('COMPLETED', 'FAILED')),
    feature_id TEXT,
    signal_id TEXT,
    error_code TEXT,
    error_message TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(observation_id, producer_version, model_version, prompt_version)
);

CREATE TRIGGER IF NOT EXISTS research_ingestion_items_no_update
BEFORE UPDATE ON research_ingestion_items
BEGIN SELECT RAISE(ABORT, 'ingestion identity and first_seen_at are immutable'); END;

CREATE TRIGGER IF NOT EXISTS research_ingestion_items_no_delete
BEFORE DELETE ON research_ingestion_items
BEGIN SELECT RAISE(ABORT, 'ingestion evidence is immutable'); END;
