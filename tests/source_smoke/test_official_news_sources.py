import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fx_research.application import CollectOnceService, ProduceSignalsOnceService
from fx_research.feature_production import ProviderLlmFeatureExtractor, RecordedFeatureProvider
from fx_research.infrastructure.http_client import HttpGetPolicy, UrllibHttpClient
from fx_research.persistence import SQLiteIngestionStateStore
from fx_research.source_registry import build_source
from fx_signal_store import SQLiteSignalStore


@pytest.mark.source_smoke
@pytest.mark.parametrize(
    "source_id",
    ["fed.press_monetary.rss", "boj.monetary_policy.html"],
)
def test_official_source_can_reach_versioned_feature_signal_pipeline(
    source_id: str, tmp_path: Path
) -> None:
    if os.getenv("RUN_SOURCE_SMOKE") != "1":
        pytest.skip("set RUN_SOURCE_SMOKE=1 to call official sources")
    database = tmp_path / "source-smoke.sqlite3"
    signal_store = SQLiteSignalStore(database)
    state_store = SQLiteIngestionStateStore(database)
    source = build_source(
        source_id,
        UrllibHttpClient(HttpGetPolicy(timeout_seconds=20, maximum_attempts=2)),
        limit=1,
    )
    now = datetime.now(UTC)
    collection = CollectOnceService(signal_store, state_store).run(source, fetched_at=now)
    pending = state_store.pending_items(
        producer_version="source-smoke-v1",
        model_version="recorded-smoke-v1",
        prompt_version="currency-feature-prompt-v1",
    )
    responses = {
        item.observation_id.value: {
            "event_type": "monetary_policy",
            "factor_scores": {"monetary_policy": 0.0},
            "impact_strength": 0.1,
            "confidence": 0.1,
        }
        for item in pending
    }
    extractor = ProviderLlmFeatureExtractor(
        RecordedFeatureProvider(responses),
        producer_version="source-smoke-v1",
        model_version="recorded-smoke-v1",
        prompt_version="currency-feature-prompt-v1",
        clock=lambda: now,
    )
    production = ProduceSignalsOnceService(
        signal_store, state_store, clock=lambda: now
    ).run(extractor)

    assert collection.inserted == 1
    assert production.completed == 1
