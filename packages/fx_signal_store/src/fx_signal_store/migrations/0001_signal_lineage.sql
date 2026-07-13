CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    published_at TEXT,
    first_seen_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    payload_reference TEXT NOT NULL,
    normalizer_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS features (
    id TEXT PRIMARY KEY,
    currency TEXT NOT NULL,
    event_type TEXT NOT NULL,
    factor_scores_json TEXT NOT NULL,
    impact_strength REAL NOT NULL,
    confidence REAL NOT NULL,
    producer_version TEXT NOT NULL,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feature_sources (
    feature_id TEXT NOT NULL REFERENCES features(id),
    observation_id TEXT NOT NULL REFERENCES observations(id),
    PRIMARY KEY (feature_id, observation_id)
);

CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL CHECK(target_type IN ('currency', 'pair')),
    target_value TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    direction REAL NOT NULL,
    strength REAL NOT NULL,
    confidence REAL NOT NULL,
    horizon TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    producer_version TEXT,
    model_version TEXT,
    prompt_version TEXT,
    scorer_version TEXT NOT NULL,
    transformation_version TEXT
);

CREATE TABLE IF NOT EXISTS signal_sources (
    signal_id TEXT NOT NULL REFERENCES signals(id),
    feature_id TEXT NOT NULL REFERENCES features(id),
    PRIMARY KEY (signal_id, feature_id)
);

CREATE TRIGGER IF NOT EXISTS observations_no_update
BEFORE UPDATE ON observations BEGIN SELECT RAISE(ABORT, 'observations are immutable'); END;
CREATE TRIGGER IF NOT EXISTS observations_no_delete
BEFORE DELETE ON observations BEGIN SELECT RAISE(ABORT, 'observations are immutable'); END;
CREATE TRIGGER IF NOT EXISTS features_no_update
BEFORE UPDATE ON features BEGIN SELECT RAISE(ABORT, 'features are immutable'); END;
CREATE TRIGGER IF NOT EXISTS features_no_delete
BEFORE DELETE ON features BEGIN SELECT RAISE(ABORT, 'features are immutable'); END;
CREATE TRIGGER IF NOT EXISTS feature_sources_no_update
BEFORE UPDATE ON feature_sources BEGIN SELECT RAISE(ABORT, 'feature lineage is immutable'); END;
CREATE TRIGGER IF NOT EXISTS feature_sources_no_delete
BEFORE DELETE ON feature_sources BEGIN SELECT RAISE(ABORT, 'feature lineage is immutable'); END;
CREATE TRIGGER IF NOT EXISTS signals_no_update
BEFORE UPDATE ON signals BEGIN SELECT RAISE(ABORT, 'signals are immutable'); END;
CREATE TRIGGER IF NOT EXISTS signals_no_delete
BEFORE DELETE ON signals BEGIN SELECT RAISE(ABORT, 'signals are immutable'); END;
CREATE TRIGGER IF NOT EXISTS signal_sources_no_update
BEFORE UPDATE ON signal_sources BEGIN SELECT RAISE(ABORT, 'signal lineage is immutable'); END;
CREATE TRIGGER IF NOT EXISTS signal_sources_no_delete
BEFORE DELETE ON signal_sources BEGIN SELECT RAISE(ABORT, 'signal lineage is immutable'); END;

