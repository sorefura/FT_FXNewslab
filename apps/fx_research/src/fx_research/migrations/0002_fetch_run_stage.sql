ALTER TABLE research_fetch_runs ADD COLUMN stage TEXT
    CHECK(stage IN ('RETRIEVAL', 'NORMALIZATION', 'PERSISTENCE', 'COMPLETED'));

ALTER TABLE research_fetch_runs ADD COLUMN processed_item_count INTEGER NOT NULL DEFAULT 0;

UPDATE research_fetch_runs
SET stage = CASE WHEN status = 'SUCCESS' THEN 'COMPLETED' ELSE 'RETRIEVAL' END,
    processed_item_count = item_count;
