from datetime import UTC, datetime
from pathlib import Path

import pytest
from fx_core import Currency
from fx_research.application import CollectOnceService, ProduceSignalsOnceService
from fx_research.collection import CollectedNewsItem
from fx_research.feature_production import ProviderLlmFeatureExtractor, RecordedFeatureProvider
from fx_research.normalization import NewsNormalizer
from fx_research.persistence import SQLiteIngestionStateStore
from fx_signal_store import SQLiteSignalStore

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


class RecordedNewsSource:
    source_id = "fed.press_monetary.rss"

    def fetch(self) -> tuple[CollectedNewsItem, ...]:
        return (
            CollectedNewsItem(
                source_id=self.source_id,
                candidate_currency=Currency("USD"),
                canonical_url="https://www.federalreserve.gov/example.htm",
                title="Federal Reserve issues FOMC statement",
                body="The Committee decided to maintain the target range.",
                published_at=NOW,
                source_date_text="Mon, 13 Jul 2026 12:00:00 GMT",
                normalizer_version="fed-rss-v1",
            ),
        )


def _stores(database: Path) -> tuple[SQLiteSignalStore, SQLiteIngestionStateStore]:
    return SQLiteSignalStore(database), SQLiteIngestionStateStore(database)


def _extractor(observation_id: str, response: dict[str, object]) -> ProviderLlmFeatureExtractor:
    return ProviderLlmFeatureExtractor(
        RecordedFeatureProvider({observation_id: response}),
        producer_version="llm-feature-v1",
        model_version="recorded-model-v1",
        prompt_version="currency-feature-prompt-v1",
        clock=lambda: NOW,
    )


def test_operational_pipeline_persists_versioned_observation_feature_signal_lineage(
    tmp_path: Path,
) -> None:
    signal_store, state_store = _stores(tmp_path / "pipeline.sqlite3")
    source = RecordedNewsSource()
    CollectOnceService(signal_store, state_store).run(source, fetched_at=NOW)
    observation_id = NewsNormalizer().observation_id(source.fetch()[0])
    extractor = _extractor(
        observation_id.value,
        {
            "event_type": "monetary_policy",
            "factor_scores": {"monetary_policy": 0.5, "inflation": 0.2},
            "impact_strength": 0.7,
            "confidence": 0.8,
        },
    )
    service = ProduceSignalsOnceService(signal_store, state_store, clock=lambda: NOW)

    first = service.run(extractor)
    second = service.run(extractor)
    signal = signal_store.list_signals(target="USD", scorer_version="fundamental-scorer-v1")[0]
    lineage = signal_store.get_lineage(signal.signal_id)

    assert first.completed == 1
    assert second.attempted == 0
    assert signal.versions.producer_version == "llm-feature-v1"
    assert signal.versions.model_version == "recorded-model-v1"
    assert signal.versions.prompt_version == "currency-feature-prompt-v1"
    assert signal.versions.scorer_version == "fundamental-scorer-v1"
    assert lineage.observation_ids == (observation_id,)


@pytest.mark.parametrize(
    "field", ["action", "side", "quantity", "leverage", "order", "order_action"]
)
def test_forbidden_llm_action_field_creates_no_signal(tmp_path: Path, field: str) -> None:
    signal_store, state_store = _stores(tmp_path / "pipeline.sqlite3")
    source = RecordedNewsSource()
    CollectOnceService(signal_store, state_store).run(source, fetched_at=NOW)
    observation_id = NewsNormalizer().observation_id(source.fetch()[0])
    extractor = _extractor(
        observation_id.value,
        {
            "event_type": "monetary_policy",
            "factor_scores": {"monetary_policy": 0.5},
            "impact_strength": 0.7,
            "confidence": 0.8,
            field: "BUY",
        },
    )

    result = ProduceSignalsOnceService(
        signal_store, state_store, clock=lambda: NOW
    ).run(extractor)

    assert result.failed == 1
    assert signal_store.list_signals() == ()
